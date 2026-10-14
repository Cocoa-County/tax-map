import json

import pytest

from taxmap import assess, gis, index, ingest, model_ops
from taxmap.domain.models import BillLabel, District, Evidence, Formula, Membership, ParcelLink
from taxmap.extract import extract_text, read_pages
from taxmap.gis.outlines import build_outlines
from taxmap.project import Project, ProjectError, find_project, init_project
from taxmap.site import build_site
from taxmap.validate import validate_project


def minimal_pdf(pages: list[str]) -> bytes:
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def square(x, y, size=0.0005):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "demo"
    init_project(root, "demo", "Town of Demo", county="Example", state="CA", fiscal_year="2026-27")
    return Project(root)


def test_init_and_find(project, tmp_path):
    assert (project.root / "taxmap.yaml").exists()
    nested = project.root / "documents"
    nested.mkdir(exist_ok=True)
    assert find_project(nested) == project.root


def test_ingest_extract_search(project, tmp_path):
    pdf = tmp_path / "engineers-report.pdf"
    pdf.write_bytes(minimal_pdf(["Engineer report cover", "The maintenance rate is 42 dollars per EDU"]))
    resource = ingest.add_file(project, pdf, title="Engineer's Report")
    assert resource.kind == "document"
    text = extract_text(project, resource.id)
    assert text["pages"] == 2
    pages = read_pages(project, resource.id, 2, 2)["pages"]
    assert "42 dollars" in pages[0]["text"]

    note = ingest.add_note(project, "Council remarks", "Residents asked about the lighting district.")
    chat = ingest.add_chat(project, "Clerk chat", [{"role": "user", "content": "Where is the landscaping roll?"}])
    assert {r.id for r in project.resources()} >= {resource.id, note.id, chat.id}

    index.rebuild(project)
    hits = index.search(project, "maintenance rate")
    assert hits and hits[0]["ref"] == resource.id and hits[0]["page"] == 2
    assert index.search(project, "landscaping")

    # Re-adding identical content does not duplicate the resource.
    again = ingest.add_file(project, pdf)
    assert again.id == resource.id
    assert validate_project(project)["ok"]


@pytest.fixture
def mapped(project, tmp_path):
    features = []
    for i, (apn, sqft) in enumerate([("001-001-001", 1000), ("001-001-002", 2000), ("001-001-003", 3000),
                                     ("002-001-001", 1500)]):
        features.append({"type": "Feature", "id": i + 1,
                         "properties": {"APN": apn, "SQFT": sqft, "ADDR": f"{i + 1} Main St", "OWNER": "Private"},
                         "geometry": square(-122.03 + 0.0005 * (i if i < 3 else 10), 37.97)})
    path = tmp_path / "parcels.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    gis.import_layer(project, "parcels", path, kind="parcels", apn_field_name="APN", address_field="ADDR")

    roll = tmp_path / "roll.csv"
    roll.write_text("apn,assessment\n001001001,10.00\n001-001-002,20.00\n001001003,30.00\n", encoding="utf-8")
    assess.import_roll(project, "lmd-2026-27", "lmd", roll, stated_total=60.0, stated_count=3)
    project.save_entity("district", District.model_validate({
        "id": "lmd", "name": "Landscape District", "fiscal_year": "2026-27",
        "membership": {"roll": "lmd-2026-27"}, "formulas": ["lmd"], "total": 60.0, "parcel_count": 3,
    }))
    project.save_entity("formula", Formula.model_validate({
        "id": "lmd", "name": "Landscape assessment", "district": "lmd",
        "inputs": [{"name": "s", "label": "Floor area", "source": {"parcel": "SQFT"}}],
        "constants": {"T": {"value": 60.0}, "S": {"value": 6000.0}},
        "expression": "round(T * s / S, 2)",
    }))
    return Project(project.root)


def test_rolls_formulas_outlines_site(mapped):
    summary = assess.roll_summary(mapped, "lmd-2026-27")
    assert all(check["ok"] for check in summary["checks"])
    join = assess.roll_join_parcels(mapped, "lmd-2026-27")
    assert join["matched"] == 3

    check = assess.validate_formula(mapped, "lmd")
    assert check["status"] == "pass" and check["match"] == 3

    result = assess.evaluate_parcel(mapped, "lmd", "001-001-002")
    assert result["value"] == 20.0
    what_if = assess.evaluate_parcel(mapped, "lmd", "001-001-002", {"s": "3000"})
    assert what_if["value"] == 30.0

    outlines = build_outlines(mapped)
    layer = next(item for item in outlines["layers"] if item["id"] == "lmd")
    assert layer["member_parcels"] == 3 and layer["non_member_parcels_inside"] == 0

    report = validate_project(mapped)
    assert report["ok"], report

    mapped.config.site.publish_fields = ["APN", "SQFT", "ADDR"]
    mapped.config.site.parcel_links = [ParcelLink(label="County bill", url_template="https://example.org/bill?apn={apn}")]
    mapped.save_config()
    built = build_site(Project(mapped.root))
    site = mapped.root / "site"
    config = json.loads((site / "data" / "config.json").read_text(encoding="utf-8"))
    assert config["parcel_links"] == [{"label": "County bill", "url_template": "https://example.org/bill?apn={apn}"}]
    assert built["parcels"] == 4 and built["indexed_apns"] == 3
    assert (site / "index.html").exists() and (site / "formula.js").exists()
    assert (site / "formula-display.js").exists()
    html = (site / "index.html").read_text(encoding="utf-8")
    assert 'src="formula-display.js"' in html
    assert 'src="parcel-hit.js"' in html and (site / "parcel-hit.js").exists()
    assert 'id="open-panel"' in html and 'id="close-panel"' in html
    assert 'aria-label="Parcel details" hidden' in html
    assert 'role="combobox"' in html and 'id="search-status"' in html
    published = "".join(p.read_text(encoding="utf-8") for p in (site / "data").rglob("*.json"))
    published += "".join(p.read_text(encoding="utf-8") for p in (site / "data").rglob("*.geojson"))
    assert "Private" not in published


@pytest.mark.parametrize("url", [
    "javascript:alert('{apn}')", "/bill?apn={apn}", "https://{apn}.example.org/",
    "https://example.org/{unknown}", "https://example.org/bill",
])
def test_invalid_parcel_link_templates_rejected(url):
    with pytest.raises(ValueError):
        ParcelLink(label="Bill", url_template=url)


def test_parcel_link_display_template():
    link = ParcelLink(label="Bill", url_template="https://example.org/bill?apn={apn_display}")
    assert "{apn_display}" in link.url_template


def test_bill_labels_published_searchable_and_cited(mapped):
    resource = ingest.add_note(mapped, "Bill wording", "Landscape charges appear as DEMO LMD / AB.")
    district = mapped.districts()[0]
    label = BillLabel(code="AB", label="DEMO LMD", fiscal_year="2026-27",
                      note="The county uses an abbreviated description.",
                      evidence=[{"resource": resource.id, "locator": "Special taxes and assessments"}])
    district.bill_labels = [label]
    mapped.save_entity("district", district)
    mapped.config.site.publish_resources = [resource.id]
    mapped.save_config()
    assert Project(mapped.root).config.site.publish_resources == [resource.id]
    assert validate_project(mapped)["ok"]
    build_site(mapped)
    data = json.loads((mapped.root / "site" / "data" / "districts.json").read_text(encoding="utf-8"))
    assert data["districts"][0]["bill_labels"] == [label.dump()]
    index.rebuild(mapped)
    assert any(hit["ref"] == "lmd" for hit in index.search(mapped, "DEMO LMD"))
    citations = index.evidence_for(mapped, resource_id=resource.id)
    assert any(item["field"] == "bill_label:AB" for item in citations)

    district.bill_labels[0].evidence[0].resource = "missing-bill"
    mapped.save_entity("district", district)
    report = validate_project(mapped)
    assert not report["ok"]
    assert any("bill label AB" in error and "missing-bill" in error for error in report["errors"])


def test_bill_labels_optional_and_nonempty():
    assert District(id="demo", name="Demo").bill_labels == []
    for code, label in [("", "DEMO LMD"), ("AB", "")]:
        with pytest.raises(ValueError):
            BillLabel(code=code, label=label)


def site_data(project, name):
    return json.loads((project.root / "site" / "data" / name).read_text(encoding="utf-8"))


def test_publication_fields_are_strict_across_site_outputs(mapped):
    mapped.config.site.publish_fields = ["APN"]
    layer = mapped.config.layers["parcels"]
    layer.label_fields = ["OWNER"]
    layer.id_field = "OWNER"
    district = mapped.districts()[0]
    district.membership = Membership(attribute="OWNER", values=["Private"])
    mapped.save_entity("district", district)
    result = build_site(mapped)
    manifest = site_data(mapped, "manifest.json")
    assert manifest["fields"] == ["APN"]
    assert manifest["address_field"] is None and manifest["label_fields"] == []
    assert all(row[2] == "" for row in site_data(mapped, "search.json"))
    assert site_data(mapped, "districts.json")["districts"][0]["rule"] == {"type": "list"}
    assert all("lmd" in entry["g"] for entry in site_data(mapped, "districts.json")["index"].values())
    public = "".join(p.read_text(encoding="utf-8") for p in (mapped.root / "site" / "data").rglob("*") if p.is_file())
    assert "Private" not in public and "Main St" not in public
    assert any("SQFT" in warning and "excluded" in warning for warning in result["warnings"])
    assert any("OWNER" in warning and "excluded" in warning for warning in result["warnings"])
    assert len({row[0] for row in site_data(mapped, "search.json")}) == 4


def test_hidden_fields_override_allow_list(mapped):
    mapped.config.site.publish_fields = ["APN", "SQFT", "ADDR"]
    mapped.config.site.hide_fields = ["SQFT", "ADDR"]
    result = build_site(mapped)
    assert site_data(mapped, "manifest.json")["fields"] == ["APN"]
    assert all(row[2] == "" for row in site_data(mapped, "search.json"))
    assert any("SQFT" in warning for warning in result["warnings"])


@pytest.mark.parametrize("allow,hide", [([], []), (["SQFT"], []), (None, ["APN"])])
def test_unapproved_apns_fail_explicitly(mapped, allow, hide):
    mapped.config.site.publish_fields = allow
    mapped.config.site.hide_fields = hide
    with pytest.raises(ProjectError, match="APN.*must be approved"):
        build_site(mapped)


def test_resources_require_explicit_approval_and_rebuild_removes_copies(mapped):
    private = ingest.add_chat(mapped, "PRIVATE TITLE", "PRIVATE CHAT CONTENT")
    public = ingest.add_note(mapped, "Public reference", "PUBLIC DOCUMENT CONTENT")
    model_ops.upsert_source(mapped, {"id": "private-source", "title": "PRIVATE SOURCE TITLE", "resource": private.id,
                                    "url": "https://example.org/private", "notes": "PRIVATE SOURCE NOTES"})
    model_ops.upsert_source(mapped, {"id": "public-source", "title": "Public reference", "resource": public.id})
    model_ops.upsert_source(mapped, {"id": "url-only", "title": "PRIVATE URL TITLE",
                                    "url": "https://example.org/unapproved"})
    district = mapped.districts()[0]
    citation = Evidence(resource=private.id, quote="PRIVATE QUOTE", locator="PRIVATE LOCATOR")
    district.evidence = [citation]
    district.bill_labels = [BillLabel(code="AB", label="DEMO", evidence=[citation])]
    mapped.save_entity("district", district)
    formula = mapped.formulas()[0]
    formula.evidence = [citation]
    formula.constants["T"].evidence = [citation]
    mapped.save_entity("formula", formula)
    roll = mapped.rolls()[0]
    roll.evidence = [citation]
    mapped.save_entity("roll", roll)
    mapped.config.site.copy_documents = True
    result = build_site(mapped)
    assert site_data(mapped, "sources.json")["resources"] == {}
    assert site_data(mapped, "sources.json")["sources"] == []
    assert not (mapped.root / "site" / "docs").exists()
    exported = "".join(p.read_text(encoding="utf-8") for p in (mapped.root / "site" / "data").glob("*.json"))
    assert "PRIVATE" not in exported
    assert any("Citations to unapproved resources" in w for w in result["warnings"])
    assert index.evidence_for(mapped, resource_id=private.id)
    mapped.config.site.publish_resources = [public.id, private.id]
    build_site(mapped)
    assert set(site_data(mapped, "sources.json")["resources"]) == {public.id, private.id}
    assert site_data(mapped, "districts.json")["districts"][0]["evidence"][0]["quote"] == "PRIVATE QUOTE"
    docs = mapped.root / "site" / "docs"
    assert (docs / f"{private.id}.md").exists()
    (docs / "user-added.txt").write_text("Preserve unrelated files")
    mapped.config.site.publish_resources = [public.id]
    build_site(mapped)
    assert not (docs / f"{private.id}.md").exists()
    assert (docs / f"{public.id}.md").exists()
    assert (docs / "user-added.txt").exists()
    assert [s["id"] for s in site_data(mapped, "sources.json")["sources"]] == ["public-source"]
    assert (mapped.root / private.path).read_text(encoding="utf-8").strip() == "PRIVATE CHAT CONTENT"
    mapped.config.site.copy_documents = False
    build_site(mapped)
    assert not (docs / f"{public.id}.md").exists()
    assert (docs / "user-added.txt").exists()


def test_unknown_publication_ids_fail_validation_and_build(mapped):
    mapped.config.site.publish_resources = ["typo"]
    assert any("publish_resources" in e for e in validate_project(mapped)["errors"])
    with pytest.raises(ProjectError, match="unknown resource IDs"):
        build_site(mapped)
    mapped.config.site.publish_resources = []
    mapped.config.site.publish_roll_fields = {"unknown-roll": ["OWNER"]}
    assert any("publish_roll_fields" in e for e in validate_project(mapped)["errors"])
    with pytest.raises(ProjectError, match="unknown roll IDs"):
        build_site(mapped)


def test_roll_extra_columns_require_approval(mapped):
    roll = mapped.rolls()[0]
    mapped.roll_csv_path(roll).write_text("apn,assessment,OWNER,units\n001001001,10,PRIVATE OWNER,2\n")
    formula = mapped.formulas()[0]
    formula.inputs[0].source.parcel = None
    formula.inputs[0].source.roll = roll.id
    formula.inputs[0].source.column = "units"
    mapped.save_entity("formula", formula)
    result = build_site(mapped)
    entry = site_data(mapped, "districts.json")["index"]["001001001"]
    assert entry["a"]["lmd"] == 10
    assert entry["r"][roll.id] == {"assessment": "10"}
    assert any("units" in w and "excluded" in w for w in result["warnings"])
    mapped.config.site.publish_roll_fields = {roll.id: ["units"]}
    mapped.save_config()
    assert Project(mapped.root).config.site.publish_roll_fields == {roll.id: ["units"]}
    build_site(mapped)
    entry = site_data(mapped, "districts.json")["index"]["001001001"]
    assert entry["r"][roll.id] == {"assessment": "10", "units": "2"}
