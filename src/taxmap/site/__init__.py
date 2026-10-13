"""Static Leaflet site generator.

Produces a backend-free site: chunked parcel geometry + attributes, a lazy search
index, district/zone outlines and roll index, compiled formulas for in-map
calculator modals, and source/evidence data for transparency modals.
"""

from __future__ import annotations

import math
import shutil
from collections import defaultdict
from importlib import resources as pkg_resources
from pathlib import Path

from taxmap.assess import AssessmentContext
from taxmap.formula import compile_formula
from taxmap.gis import ADDRESS_FIELD_PATTERN, apn_field, combined_bounds, detect_field, geometry_bounds, load_layer
from taxmap.project import Project, ProjectError
from taxmap.site.markdown import markdown_to_html
from taxmap.util import now_iso, read_json, to_number, write_json, write_text

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#db2777"]


def _static_dir() -> Path:
    return Path(str(pkg_resources.files("taxmap.site") / "static"))


def _published_fields(project: Project, all_fields: list[str], required: set[str]) -> tuple[list[str], list[str]]:
    site = project.config.site
    allowed = list(all_fields) if site.publish_fields is None else [f for f in all_fields if f in site.publish_fields]
    for field in required:
        if field in all_fields and field not in allowed:
            allowed.append(field)
    hidden = set(site.hide_fields)
    warnings = [f"Field {f} is hidden but needed by a formula/search; calculators will not prefill it"
                for f in sorted(required & hidden)]
    return [f for f in allowed if f not in hidden], warnings


def _build_parcels(project: Project, out: Path, required_fields: set[str]) -> tuple[dict, list[str]]:
    layer = project.parcel_layer_name()
    config = project.config.layers[layer]
    data = load_layer(project, layer)
    features = data["features"]
    if not features:
        raise ProjectError("Parcel layer is empty")
    apn_key = apn_field(project, layer)
    address_key = config.address_field or detect_field(features, ADDRESS_FIELD_PATTERN)
    first_props = features[0].get("properties") or {}
    id_key = config.id_field or ("OBJECTID" if "OBJECTID" in first_props else None)
    all_fields: list[str] = []
    seen_fields: set[str] = set()
    for feature in features[:1000]:
        for key in feature.get("properties") or {}:
            if key not in seen_fields:
                seen_fields.add(key)
                all_fields.append(key)
    label_fields = list(config.label_fields)
    fields, warnings = _published_fields(
        project, all_fields, required_fields | {apn_key} | ({address_key} if address_key else set()) | set(label_fields))
    cell = project.config.site.chunk_degrees
    groups: dict[str, list] = defaultdict(list)
    rows, null_ids, ids = [], [], set()
    for position, feature in enumerate(features):
        props = feature.get("properties") or {}
        fid = props.get(id_key) if id_key else position + 1
        if fid in ids or fid is None:
            fid = f"f{position + 1}"
        ids.add(fid)
        bounds = geometry_bounds(feature.get("geometry"))
        if bounds:
            tile = f"{math.floor((bounds[0] + bounds[2]) / 2 / cell)}_{math.floor((bounds[1] + bounds[3]) / 2 / cell)}"
        else:
            tile = "no-geometry"
            null_ids.append(fid)
        groups[tile].append((fid, feature, bounds))
        rows.append([fid, str(props.get(apn_key) or ""), str(props.get(address_key) or "") if address_key else "",
                     tile, bounds])
    for name in ("tiles", "attributes"):
        target = out / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    manifest_tiles = []
    for tile, entries in sorted(groups.items()):
        attributes, tile_features = {}, []
        for fid, feature, bounds in entries:
            props = feature.get("properties") or {}
            attributes[str(fid)] = {k: props.get(k) for k in fields}
            if bounds:
                tile_features.append({"type": "Feature", "id": fid, "geometry": feature["geometry"],
                                      "properties": {"id": fid, "apn": props.get(apn_key)}})
        write_json(out / "attributes" / f"{tile}.json", attributes)
        if tile_features:
            write_json(out / "tiles" / f"{tile}.geojson", {"type": "FeatureCollection", "features": tile_features})
            manifest_tiles.append({"id": tile, "count": len(tile_features),
                                   "bounds": combined_bounds([b for _, _, b in entries if b])})
    if not manifest_tiles:
        raise ProjectError("No parcel geometries to map")
    write_json(out / "search.json", rows)
    manifest = {
        "built_at": now_iso(), "feature_count": len(features), "geometry_count": len(features) - len(null_ids),
        "null_geometry_ids": null_ids[:1000], "bounds": combined_bounds([t["bounds"] for t in manifest_tiles]),
        "tiles": manifest_tiles, "fields": fields, "apn_field": apn_key, "address_field": address_key,
        "label_fields": label_fields, "minimum_zoom": project.config.site.minimum_zoom,
    }
    write_json(out / "manifest.json", manifest)
    return manifest, warnings


def _resource_links(project: Project, site_root: Path) -> dict[str, dict]:
    links = {}
    copy = project.config.site.copy_documents
    for resource in project.resources():
        url = resource.origin_url
        if copy and resource.path:
            source = project.path(resource.path)
            target = site_root / "docs" / f"{resource.id}{source.suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            url = f"docs/{target.name}"
        links[resource.id] = {"id": resource.id, "title": resource.title, "kind": resource.kind, "url": url,
                              "origin_url": resource.origin_url, "retrieved_at": resource.retrieved_at,
                              "media_type": resource.media_type}
    return links


def _evidence(items) -> list[dict]:
    return [e.dump() for e in items]


def build_site(project: Project, output: str | None = None) -> dict:
    site_root = project.path(output or "site")
    data_dir = site_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    formulas = project.formulas()
    compiled = {f.id: compile_formula(f).data for f in formulas}
    required = set()
    for formula in formulas:
        for spec in formula.inputs:
            if spec.source and spec.source.parcel:
                required |= {spec.source.parcel} if isinstance(spec.source.parcel, str) else set(spec.source.parcel)
    context = AssessmentContext(project)
    districts = context.districts
    for district in districts:
        for group in [district.membership, *(z.membership for z in district.zones)]:
            if group and group.attribute:
                required.add(group.attribute)

    manifest, parcel_warnings = _build_parcels(project, data_dir, required)
    warnings += parcel_warnings

    # Districts, zones, outlines, and the APN -> roll/membership index.
    outlines_path = project.root / "gis" / "districts" / "outlines.geojson"
    outlines = read_json(outlines_path) if outlines_path.exists() else {"type": "FeatureCollection", "features": []}
    if not outlines["features"] and districts:
        warnings.append("No district outlines; run district_outline_build for map boundaries")
    groups_out, apn_index = [], defaultdict(lambda: {"g": [], "a": {}, "r": {}})
    for n, district in enumerate(districts):
        color = district.color or PALETTE[n % len(PALETTE)]
        rolls = [r for r in context.rolls.values() if r.district == district.id]
        roll_total = sum(to_number(row.get(r.amount_column)) or 0 for r in rolls
                         for row in context.roll_rows[r.id].values())
        notes_html = None
        if district.notes:
            note = project.model_dir / "notes" / district.notes
            if note.exists():
                notes_html = markdown_to_html(note.read_text(encoding="utf-8"))

        def rule(membership):
            if membership and membership.parent:
                return {"type": "parent"}
            if membership and membership.attribute:
                return {"type": "attribute", "attribute": membership.attribute, "values": membership.values}
            return {"type": "list"}

        zones = []
        for m, zone in enumerate(district.zones):
            zones.append({"id": zone.id, "name": zone.name, "description": zone.description,
                          "color": zone.color or PALETTE[(n + m + 1) % len(PALETTE)],
                          "members": len(context.members.get(zone.id, ())), "rule": rule(zone.membership),
                          "evidence": _evidence(zone.evidence)})
        groups_out.append({
            "id": district.id, "name": district.name, "short_name": district.short_name or district.name,
            "fiscal_year": district.fiscal_year, "agency": district.agency, "color": color,
            "description": district.description, "stated_total": district.total,
            "stated_count": district.parcel_count, "roll_total": round(roll_total, 2),
            "rolls": [r.id for r in rolls], "members": len(context.members.get(district.id, ())),
            "rule": rule(district.membership), "zones": zones, "formulas": district.formulas,
            "notes_html": notes_html, "evidence": _evidence(district.evidence),
            "bill_labels": [label.dump() for label in district.bill_labels],
        })
        list_groups = [district.id] if groups_out[-1]["rule"]["type"] == "list" else []
        list_groups += [z["id"] for z in zones if z["rule"]["type"] == "list"]
        for group_id in list_groups:
            for apn in context.members.get(group_id, ()):
                apn_index[apn]["g"].append(group_id)
        for roll in rolls:
            for apn, row in context.roll_rows[roll.id].items():
                apn_index[apn]["a"][district.id] = to_number(row.get(roll.amount_column))
                apn_index[apn]["r"][roll.id] = {c: v for c, v in row.items() if c != roll.apn_column}
    write_json(data_dir / "districts.json", {
        "districts": groups_out, "outlines": outlines,
        "apn_normalize": project.config.apn.normalize,
        "index": {apn: {k: v for k, v in entry.items() if v} for apn, entry in apn_index.items()},
    })

    # Formulas: compiled ASTs plus provenance.
    formula_out = []
    for formula in formulas:
        data = dict(compiled[formula.id])
        data["evidence"] = _evidence(formula.evidence)
        data["constant_evidence"] = {k: _evidence(c.evidence) for k, c in formula.constants.items() if c.evidence}
        formula_out.append(data)
    write_json(data_dir / "formulas.json", {"formulas": formula_out})

    # Sources and evidence for transparency modals.
    links = _resource_links(project, site_root)
    sources = [s.dump() | {"id": s.id, "title": s.title, "status": s.status} for s in project.sources()]
    rolls_out = [{"id": r.id, "district": r.district, "fiscal_year": r.fiscal_year, "columns": r.columns,
                  "notes": r.notes, "evidence": _evidence(r.evidence)} for r in context.rolls.values()]
    write_json(data_dir / "sources.json", {"resources": links, "sources": sources, "rolls": rolls_out})

    notes_dir = project.model_dir / "notes"
    pages = []
    for note in sorted(notes_dir.glob("*.md")) if notes_dir.exists() else []:
        text = note.read_text(encoding="utf-8")
        title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), note.stem)
        pages.append({"id": note.stem, "title": title, "html": markdown_to_html(text)})
    jurisdiction = project.config.jurisdiction
    write_json(data_dir / "config.json", {
        "title": project.config.site.title or project.config.title or project.config.name,
        "subtitle": project.config.site.subtitle or jurisdiction.name,
        "disclaimer": project.config.site.disclaimer or (
            "Unofficial map built from public records. Outlines are approximate. The adopted assessment roll "
            "and county tax bill are authoritative."),
        "jurisdiction": jurisdiction.dump(), "fiscal_year": project.config.fiscal_year,
        "apn_display_pattern": project.config.apn.display_pattern, "pages": pages, "built_at": now_iso(),
        "parcel_links": [link.dump() for link in project.config.site.parcel_links],
    })

    static = _static_dir()
    for item in static.rglob("*"):
        if item.is_file():
            target = site_root / item.relative_to(static)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
    write_text(site_root / ".nojekyll", "")
    return {
        "site": project.rel(site_root), "parcels": manifest["feature_count"], "tiles": len(manifest["tiles"]),
        "districts": len(groups_out), "formulas": len(formula_out), "indexed_apns": len(apn_index),
        "published_fields": len(manifest["fields"]), "warnings": warnings,
        "preview": "Serve the folder over HTTP (site_serve or `taxmap serve`); file:// will not work.",
    }


def serve_site(project: Project, output: str | None = None, port: int = 8000, host: str = "127.0.0.1"):
    """Serve the built site (blocking). Returns only when interrupted."""
    import functools
    import http.server

    site_root = project.path(output or "site")
    if not (site_root / "index.html").exists():
        raise ProjectError("Site not built yet; run site_build first")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site_root))
    server = http.server.ThreadingHTTPServer((host, port), handler)
    return server
