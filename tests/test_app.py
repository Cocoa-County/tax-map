import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import socket
import subprocess
import sys
import threading
from urllib.request import urlopen

import pytest

from taxmap import app, assess, gis, ingest, model_ops
from taxmap.domain.models import AppState, District, Formula
from taxmap.project import Project, ProjectError
from taxmap.util import read_json, read_yaml, write_json, write_yaml


def make_app(tmp_path, name="demo", stated_total=60):
    project, _ = app.start("Demo City", state="CA", name=name, path=str(tmp_path / name),
                           fiscal_year="2026-27")
    features = []
    for n in range(1, 4):
        x = -122 + n * 0.001
        features.append({
            "type": "Feature",
            "properties": {"APN": f"001-{n}", "SQFT": n * 1000, "ADDR": f"{n} Main St",
                           "OWNER": "private-owner"},
            "geometry": {"type": "Polygon", "coordinates": [
                [[x, 38], [x + .0005, 38], [x + .0005, 38.0005], [x, 38.0005], [x, 38]],
            ]},
        })
    source = tmp_path / f"{name}-parcels.json"
    write_json(source, {"type": "FeatureCollection", "features": features})
    gis.import_layer(project, "parcels", source, kind="parcels",
                     apn_field_name="APN", address_field="ADDR")
    resource = ingest.add_note(project, "Adopted synthetic report", "Adopted FY 2026-27; annual rates and roll.")
    evidence = [{"resource": resource.id, "page": 1}]
    model_ops.upsert_source(project, {
        "id": "adoption", "title": "Adoption record", "resource": resource.id,
        "year": "2026-27", "status": "verified", "evidence_location": "Page 1",
    })
    assess.import_roll(project, "annual", "lighting", rows=[
        {"apn": f"001-{n}", "assessment": n * 10, "private": "private-roll-value"} for n in range(1, 4)
    ], stated_count=3, stated_total=stated_total, fiscal_year="2026-27", evidence=evidence)
    project.save_entity("district", District.model_validate({
        "id": "lighting", "name": "Lighting", "fiscal_year": "2026-27",
        "membership": {"roll": "annual"}, "formulas": ["rate"], "total": stated_total,
        "notes": "lighting.md", "evidence": evidence,
    }))
    project.save_entity("formula", Formula.model_validate({
        "id": "rate", "name": "Annual rate schedule", "district": "lighting",
        "fiscal_year": "2026-27", "expression": "s / 100",
        "inputs": [{"name": "s", "label": "Floor area", "source": {"parcel": "SQFT"}}],
        "evidence": evidence,
    }))
    (project.model_dir / "notes" / "lighting.md").write_text("# Lighting\nApproved explanation.", encoding="utf-8")
    app.status(project, {"year_evidence": evidence})
    return Project(project.root), resource.id


@pytest.fixture
def modeled(tmp_path):
    return make_app(tmp_path)


def approve(project, resource, **overrides):
    proposed = app.review(project, {
        "publish_fields": ["APN", "SQFT", "ADDR"], "publish_resources": [resource], **overrides,
    })
    assert not proposed["approved"]
    result = app.review(project, approve=proposed["digest"])
    assert result["approved"]
    return result


def test_defaults_and_safe_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project, created = app.start("Martinez", state="CA")
    assert project.root == tmp_path / "taxmap-projects" / "martinez"
    assert created["config"]["title"] == "Martinez Tax and Assessment Map"
    assert project.config.site.publish_fields == []
    assert not project.config.site.publish_resources
    assert project.config.fiscal_year is None
    prompt = project.path("PROJECT-PROMPT.md")
    prompt.write_text("user-edited request", encoding="utf-8")
    _, resumed = app.start("MARTINEZ", state="ca")
    assert resumed["resumed"]
    assert prompt.read_text(encoding="utf-8") == "user-edited request"
    assert app.status(project)["status"] == "blocked"


@pytest.mark.parametrize("name", ["../escape", "CON", "con", "aux", "com1", "LPT1", "", "with space", "a" * 61])
def test_unsafe_names_do_not_create_files(tmp_path, name):
    with pytest.raises(ProjectError, match="project ID"):
        app.start("Example", name=name, path=str(tmp_path / "target"))
    assert not list(tmp_path.iterdir())


def test_nonempty_and_conflicting_targets_are_preserved(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    keep = root / "keep.txt"
    keep.write_text("preserve", encoding="utf-8")
    with pytest.raises(ProjectError, match="not an empty"):
        app.start("Example", path=str(root))
    assert keep.read_text() == "preserve"
    project, _ = app.start("Example", state="CA", path=str(tmp_path / "example"), fiscal_year="2025-26")
    before = project.path("taxmap.yaml").read_bytes()
    with pytest.raises(ProjectError, match="different identity"):
        app.start("Example", state="NV", path=str(project.root))
    with pytest.raises(ProjectError, match="fiscal year conflicts"):
        app.start("Example", state="CA", path=str(project.root), fiscal_year="2026-27")
    assert project.path("taxmap.yaml").read_bytes() == before


def test_requested_year_and_state_version_survive_resume(modeled):
    project, _ = modeled
    project.config.fiscal_year = "2027-28"
    project.save_config()
    assert any("requested 2026-27" in message for message in app.check(project).blockers)
    data = read_yaml(project.path("app.yaml"))
    data["version"] = 99
    write_yaml(project.path("app.yaml"), data)
    with pytest.raises(ValueError, match="version"):
        app.status(project)


def test_research_can_enrich_optional_jurisdiction_metadata(modeled):
    project, _ = modeled
    project.config.jurisdiction.county = "Example County"
    project.config.jurisdiction.agency = "City Council"
    project.save_config()
    assert app.status(project)["status"] == "needs_approval"
    state = AppState.model_validate(read_yaml(project.path("app.yaml")))
    assert state.jurisdiction.county == "Example County"
    assert state.jurisdiction.agency == "City Council"
    project.config.jurisdiction.county = "Conflicting County"
    project.save_config()
    with pytest.raises(ProjectError, match="identity differs"):
        app.status(project)


def test_legacy_project_adopts_state_without_reinitializing(modeled):
    project, _ = modeled
    project.path("app.yaml").unlink()
    before = project.path("PROJECT-PROMPT.md").read_bytes()
    _, result = app.start("Demo City", state="CA", path=str(project.root))
    assert result["resumed"]
    assert result["state"]["requested_fiscal_year"] == "2026-27"
    assert project.path("PROJECT-PROMPT.md").read_bytes() == before


def test_integrity_green_does_not_hide_failed_total(tmp_path):
    project, resource = make_app(tmp_path, stated_total=60.26)
    report = app.check(project)
    assert report.integrity["ok"]
    assert report.status == "ready_with_caveats"
    assert not report.rolls[0]["checks"][0]["ok"]
    assert report.rolls[0]["join"]["matched"] == 3
    assert report.formulas[0]["match"] == 3
    assert any("60.26" in text and "60.0" in text for text in report.caveats)
    assert app.status(project)["status"] == "needs_approval"
    approved = approve(project, resource)
    page = approved["manifest"]["public_config"]["pages"][-1]
    assert "60.26" in page["html"] and "Source values preserved" in page["html"]
    assert assess.roll_summary(project, "annual")["total"] == 60


def test_consolidated_approval_exact_content_and_private_fields(modeled):
    project, resource = modeled
    with pytest.raises(ProjectError, match="approval is required"):
        app.Preview().start(project)
    result = approve(project, resource)
    assert result["manifest"]["parcel_fields"] == ["APN", "SQFT", "ADDR"]
    assert list(result["manifest"]["resources"]) == [resource]
    assert result["manifest"]["copy_documents"] is False
    public_model = "".join(path.read_text(encoding="utf-8") for path in
                           project.path("derived/app-review").rglob("*.json"))
    assert "private-owner" not in public_model
    assert "private-roll-value" not in public_model
    assert app.status(project)["approval_current"]
    assert app.status(Project(project.root))["status"] == "ready_with_caveats"


def test_content_changes_require_new_approval(modeled):
    project, resource = modeled
    original = approve(project, resource)
    note = project.model_dir / "notes" / "lighting.md"
    note.write_text("# Lighting\nChanged explanation.", encoding="utf-8")
    with pytest.raises(ProjectError, match="changed since review"):
        app.review(project, approve=original["digest"])
    assert app.status(project)["status"] == "needs_approval"
    with pytest.raises(ProjectError, match="Export content changed"):
        app.Preview().start(project)
    updated = app.review(project)
    assert "data/config.json" in updated["changed_files"]
    assert updated["digest"] != original["digest"]
    app.review(project, approve=updated["digest"])
    assert app.status(project)["approval_current"]


def test_private_data_edits_do_not_need_reapproval(modeled):
    project, resource = modeled
    approve(project, resource)
    path = project.layer_path("parcels")
    data = read_json(path)
    data["features"][0]["properties"]["OWNER"] = "different-private-owner"
    write_json(path, data)
    assert app.status(project)["approval_current"]


def test_denied_resources_and_fields_stay_withheld(modeled):
    project, resource = modeled
    approve(project, resource, publish_resources=[], publish_fields=["APN"])
    preview = app.Preview()
    try:
        result = preview.start(project)
        public = "".join(path.read_text(encoding="utf-8") for path in
                         project.path(result["site"]).rglob("*.json"))
        assert "private-owner" not in public and "private-roll-value" not in public
        assert "Main St" not in public and "Adopted synthetic report" not in public
        sources = read_json(project.path(result["site"]) / "data" / "sources.json")
        assert not sources["resources"]
        assert any("withheld" in warning for warning in result["warnings"])
    finally:
        preview.stop(project)


def test_withheld_resource_link_cannot_bypass_review(modeled):
    project, resource = modeled
    ingest.annotate(project, resource, origin_url="https://example.invalid/private-record.pdf")
    note = project.model_dir / "notes" / "lighting.md"
    note.write_text("# Lighting\n[Record](https://example.invalid/private-record.pdf)", encoding="utf-8")
    with pytest.raises(ProjectError, match="withheld resource URL"):
        app.review(project, {"publish_fields": ["APN"], "publish_resources": []})


def test_review_rejects_unknown_resources_and_implicit_all_fields(modeled):
    project, _ = modeled
    with pytest.raises(ProjectError, match="unknown resource"):
        app.review(project, {"publish_fields": ["APN"], "publish_resources": ["unknown"]})
    with pytest.raises(ProjectError, match="explicit publish_fields"):
        app.review(project, {"publish_fields": None})
    with pytest.raises(ProjectError, match="APN.*must be approved"):
        app.review(project, {"publish_fields": ["ADDR"]})


def test_guided_review_confines_public_note_paths(modeled, tmp_path):
    project, _ = modeled
    outside = tmp_path / "outside.md"
    outside.write_text("Unrelated private file", encoding="utf-8")
    district = project.district("lighting")
    district.notes = str(outside)
    project.save_entity("district", district)
    report = app.check(project)
    assert report.status == "blocked"
    assert any("Public model paths" in text and "escapes" in text for text in report.blockers)


def test_preview_is_owned_loopback_healthy_and_restartable(modeled, tmp_path):
    project, resource = modeled
    approve(project, resource)
    preview = app.Preview()
    try:
        result = preview.start(project)
        assert result["url"].startswith("http://127.0.0.1:")
        with urlopen(result["url"] + "data/config.json", timeout=3) as response:
            data = json.load(response)
        assert "Partial assessment coverage" in data["disclaimer"]
        assert "absent charge does not mean zero" in data["disclaimer"]
        assert data["pages"][-1]["id"] == "taxmap-app-coverage"
        assert preview.start(project)["url"] == result["url"]
        assert "--output" in result["restart_command"] and "--port" in result["restart_command"]
        assert sys.executable in result["restart_command"]
        saved = AppState.model_validate(read_yaml(project.path("app.yaml")))
        assert saved.last_preview.restart_command == result["restart_command"]
        assert saved.last_preview.url == result["url"]
        other, _ = app.start("Other City", state="CA", path=str(tmp_path / "other"))
        with pytest.raises(ProjectError, match="different project's"):
            preview.start(other)
        with pytest.raises(ProjectError, match="different project's"):
            preview.stop(other)
        with urlopen(result["url"], timeout=3) as response:
            assert response.status == 200
        owned = project.path(result["site"]) / "unexpected.txt"
        owned.write_text("preserve user edits", encoding="utf-8")
        with pytest.raises(ProjectError, match="preview files were edited"):
            preview.start(project)
        assert owned.read_text() == "preserve user edits"
    finally:
        preview.stop(project)
    assert preview.server is None


def test_reapproved_export_replaces_only_owned_server(modeled):
    project, resource = modeled
    approve(project, resource)
    preview = app.Preview()
    try:
        first = preview.start(project)
        path = project.model_dir / "notes" / "lighting.md"
        path.write_text("# Lighting\nUpdated public notes.", encoding="utf-8")
        updated = app.review(project)
        app.review(project, approve=updated["digest"])
        second = preview.start(project)
        assert second["site"] != first["site"]
        with urlopen(second["url"] + "data/config.json", timeout=3) as response:
            assert "Updated public notes" in json.load(response)["pages"][0]["html"]
    finally:
        preview.stop(project)


def test_standalone_restart_command_serves_saved_export(modeled):
    project, resource = modeled
    approve(project, resource)
    preview = app.Preview()
    try:
        result = preview.start(project)
    finally:
        preview.stop(project)
    process = subprocess.Popen(result["restart_argv"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=os.environ | {"PYTHONUNBUFFERED": "1"})
    lines = Queue()
    reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        try:
            line = lines.get(timeout=60)
        except Empty:
            pytest.fail("Standalone preview did not report its URL within 60 seconds")
        match = re.search(r"http://127\.0\.0\.1:\d+/", line)
        assert match is not None
        with urlopen(match[0] + "data/config.json", timeout=3) as response:
            assert json.load(response)["fiscal_year"] == "2026-27"
    finally:
        process.terminate()
        reader.join(timeout=10)
        stdout, stderr = process.communicate(timeout=10)
        print(f"Standalone preview stdout: {stdout}\nstderr: {stderr}")


def test_occupied_port_is_not_interrupted(modeled):
    project, resource = modeled
    approve(project, resource)
    preview = app.Preview()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(OSError):
            preview.start(project, port=occupied.getsockname()[1])
        assert occupied.fileno() != -1
    assert preview.server is None


def test_failed_health_check_does_not_return_success(modeled, monkeypatch):
    project, resource = modeled
    approve(project, resource)

    class Unavailable:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise OSError("probe unavailable")

        def close(self):
            pass

    monkeypatch.setattr(app, "HTTPConnection", Unavailable)
    preview = app.Preview()
    with pytest.raises(ProjectError, match="health check failed"):
        preview.start(project)
    assert preview.server is None
    assert AppState.model_validate(read_yaml(project.path("app.yaml"))).last_preview is None


def test_document_copy_approval_and_revocation(modeled):
    project, resource = modeled
    approve(project, resource, copy_documents=True)
    preview = app.Preview()
    try:
        first = preview.start(project)
        assert list((project.path(first["site"]) / "docs").iterdir())
        proposed = app.review(project, {"copy_documents": False, "publish_resources": []})
        app.review(project, approve=proposed["digest"])
        second = preview.start(project)
        assert not (project.path(second["site"]) / "docs").exists()
        with urlopen(second["url"] + "data/sources.json", timeout=3) as response:
            assert json.load(response)["resources"] == {}
    finally:
        preview.stop(project)


def test_progress_requires_confined_existing_artifacts_and_valid_evidence(modeled):
    project, _ = modeled
    with pytest.raises(ProjectError, match="escapes"):
        app.status(project, {"artifacts": ["../outside"]})
    with pytest.raises(ProjectError, match="Not found"):
        app.status(project, {"artifacts": ["nonexistent"]})
    result = app.status(project, {"year_evidence": [{"resource": "unknown"}]})
    assert result["status"] == "blocked"
    assert any("unknown resource" in text for text in result["blockers"])


def test_missing_geometry_unmatched_rows_and_unparsed_amounts_are_explicit(modeled):
    project, _ = modeled
    path = project.layer_path("parcels")
    data = read_json(path)
    data["features"] = data["features"][1:]
    write_json(path, data)
    report = app.check(project)
    assert report.rolls[0]["join"]["unmatched_count"] == 1
    assert any("do not join" in text for text in report.caveats)
    with_geometry = json.loads(json.dumps(data))
    for feature in data["features"]:
        feature["geometry"] = None
    write_json(path, data)
    assert any("No usable parcel geometry" in text for text in app.check(project).blockers)
    write_json(path, with_geometry)
    project.roll_csv_path(project.roll("annual")).write_text("apn,assessment\n001-2,unknown\n", encoding="utf-8")
    assert any("unparsed amounts" in text for text in app.check(project).blockers)


def test_multiple_rolls_are_all_compared(modeled):
    project, _ = modeled
    roll = project.roll("annual")
    assess.import_roll(project, "second", "lighting",
                       rows=[{"apn": "001-1", "assessment": 99}], evidence=[e.dump() for e in roll.evidence])
    report = app.check(project)
    assert {result["roll"] for result in report.formulas} == {"annual", "second"}
    assert next(result for result in report.formulas if result["roll"] == "second")["status"] == "review"


def test_saved_state_is_typed(modeled):
    project, _ = modeled
    state = AppState.model_validate(read_yaml(project.path("app.yaml")))
    assert state.last_report is not None
    assert state.requested_fiscal_year == "2026-27"
