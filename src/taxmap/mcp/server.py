"""taxmap MCP server: deterministic project, document, GIS, and model tools for agents.

The server never calls an LLM. Agents read documents (text, tables, page images) via
these tools, do the reasoning, and write structured, cited results back.
"""

from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path
from typing import Any, Literal

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image

from taxmap import assess, extract, index, ingest, model_ops
from taxmap.domain.models import ArcGISSource, LayerConfig
from taxmap.gis import arcgis, import_layer
from taxmap.project import Project, ProjectError, find_project, init_project
from taxmap.validate import validate_project

INSTRUCTIONS = """\
taxmap turns a folder of public records (documents, web pages, chats, images, GIS data)
into a cited model of tax/assessment districts and a static transparency map.
Workflow: project_open/project_init -> resource_add_* -> extract_text/resource_search ->
source_upsert (screen) -> roll_import -> arcgis_download_layer/layer_import ->
district_upsert -> formula_upsert -> formula_validate -> district_outline_build ->
project_validate -> site_build. Every fact should cite evidence (resource id + page).
Load the skills in the repo's skills/ folder for detailed procedures.
"""

mcp = FastMCP("taxmap", instructions=INSTRUCTIONS)
_state: dict[str, Any] = {"project": None, "server": None}


def _project() -> Project:
    if _state["project"] is None:
        found = find_project()
        if found is None:
            raise ProjectError("No project open. Call project_open(path) or project_init(...)")
        _state["project"] = Project(found)
    return _state["project"]


def _dump(item):
    return item.dump() if hasattr(item, "dump") else item


# ------------------------------------------------------------------ project

@mcp.tool()
def project_open(path: str) -> dict:
    """Open an existing taxmap project folder (containing taxmap.yaml) for all later calls."""
    _state["project"] = Project(Path(path))
    return project_info()


@mcp.tool()
def project_init(path: str, name: str, jurisdiction: str, title: str | None = None, county: str | None = None,
                 state: str | None = None, fiscal_year: str | None = None) -> dict:
    """Create a new project folder layout (taxmap.yaml, resources/, model/, gis/, notes) and open it."""
    _state["project"] = init_project(path, name, jurisdiction, title=title, county=county, state=state,
                                     fiscal_year=fiscal_year)
    return project_info()


@mcp.tool()
def project_info() -> dict:
    """Project config, counts, layers, and districts summary."""
    project = _project()
    return {
        "root": str(project.root), "config": project.config.dump(),
        "resources": len(project.resources()), "sources": len(project.sources()),
        "districts": assess.district_summary(project),
        "formulas": [{"id": f.id, "name": f.name, "district": f.district} for f in project.formulas()],
        "rolls": [{"id": r.id, "district": r.district} for r in project.rolls()],
        "layers": {name: {"kind": layer.kind, "has_data": project.layer_path(name).exists()}
                   for name, layer in project.config.layers.items()},
    }


@mcp.tool()
def project_configure(updates: dict) -> dict:
    """Shallow-merge top-level keys into taxmap.yaml (e.g. {"apn": {...}, "site": {...},
    "projected_crs": 26910}). Nested objects replace the existing value; validated before saving."""
    project = _project()
    data = project.config.model_dump(mode="json") | updates
    project.config = type(project.config).model_validate(data)
    project.save_config()
    return project.config.dump()


@mcp.tool()
def project_validate(check_hashes: bool = True) -> dict:
    """Check registry files, model cross-references, formula compilation, and evidence citations."""
    return validate_project(_project(), check_hashes=check_hashes)


@mcp.tool()
def index_rebuild(extract_missing: bool = True) -> dict:
    """Rebuild the SQLite search/provenance cache from the project files."""
    return index.rebuild(_project(), extract_missing=extract_missing)


# ------------------------------------------------------------------ resources

@mcp.tool()
def resource_add_file(path: str, kind: str | None = None, title: str | None = None, origin_url: str | None = None,
                      tags: list[str] | None = None, notes: str | None = None, resource_id: str | None = None) -> dict:
    """Copy a local file (PDF, image, CSV, GeoJSON, text...) into the project and register it.
    kind: document|web|chat|image|data|note|other (inferred when omitted)."""
    return ingest.add_file(_project(), path, kind=kind, title=title, origin_url=origin_url, tags=tags,
                           notes=notes, resource_id=resource_id).dump()


@mcp.tool()
def resource_add_url(url: str, title: str | None = None, kind: str | None = None, tags: list[str] | None = None,
                     notes: str | None = None, link_only: bool = False, resource_id: str | None = None) -> dict:
    """Download a web page or file and snapshot it (link_only=True records the URL without fetching)."""
    return ingest.add_url(_project(), url, title=title, kind=kind, tags=tags, notes=notes, link_only=link_only,
                          resource_id=resource_id).dump()


@mcp.tool()
def resource_add_chat(title: str, messages: list[dict] | str, tags: list[str] | None = None,
                      notes: str | None = None) -> dict:
    """Save a conversation (list of {role, content, [author], [time]} or plain text) as a citable resource."""
    return ingest.add_chat(_project(), title, messages, tags=tags, notes=notes).dump()


@mcp.tool()
def resource_add_note(title: str, text: str, tags: list[str] | None = None) -> dict:
    """Save a Markdown research note as a citable resource."""
    return ingest.add_note(_project(), title, text, tags=tags).dump()


@mcp.tool()
def resource_list(kind: str | None = None, tag: str | None = None) -> list[dict]:
    """List registered resources, optionally filtered by kind or tag."""
    return [r.dump() for r in _project().resources()
            if (kind is None or r.kind == kind) and (tag is None or tag in r.tags)]


@mcp.tool()
def resource_get(resource_id: str) -> dict:
    """Resource metadata plus extraction status and what model entities cite it."""
    project = _project()
    out = project.resource(resource_id).dump()
    meta = project.derived_dir(resource_id) / "meta.json"
    if meta.exists():
        from taxmap.util import read_json
        info = read_json(meta)
        out["extraction"] = {k: info.get(k) for k in ("pages", "chars", "warnings", "extractor")}
    out["cited_by"] = index.evidence_for(project, resource_id=resource_id)
    return out


@mcp.tool()
def resource_read(resource_id: str, start_page: int = 1, end_page: int | None = None, max_chars: int = 60000) -> dict:
    """Read extracted text for a page range (extracts on first use). Pages are 1-based."""
    return extract.read_pages(_project(), resource_id, start_page, end_page, max_chars)


@mcp.tool()
def resource_search(query: str, kinds: list[str] | None = None, limit: int = 20, raw: bool = False) -> list[dict]:
    """Full-text search across document pages, sources, notes, and model entities. Results include
    resource id + page for citations. raw=True passes FTS5 syntax through (AND/OR/NEAR/"phrases")."""
    return index.search(_project(), query, kinds=kinds, limit=limit, raw=raw)


@mcp.tool()
def resource_annotate(resource_id: str, title: str | None = None, tags: list[str] | None = None,
                      notes: str | None = None, origin_url: str | None = None) -> dict:
    """Update a resource's title, tags, notes, or origin URL."""
    return ingest.annotate(_project(), resource_id, title=title, tags=tags, notes=notes, origin_url=origin_url).dump()


# ------------------------------------------------------------------ extraction

@mcp.tool()
def extract_text(resource_id: str, force: bool = False) -> dict:
    """Extract per-page text (PDF/HTML/text). Reports pages with no text layer (need OCR/visual reading)."""
    return extract.extract_text(_project(), resource_id, force=force)


@mcp.tool()
def extract_tables(resource_id: str, page: int, strategy: Literal["lines", "text"] = "lines") -> dict:
    """Detect tables on a PDF page and save them as CSV. Try strategy='text' for borderless tables."""
    return extract.extract_tables(_project(), resource_id, page, strategy)


@mcp.tool()
def extract_words(resource_id: str, page: int, bbox: list[float] | None = None, limit: int = 4000) -> dict:
    """Positioned words on a PDF page (x0, top, x1, bottom in points) for column-based parsing of rolls."""
    return extract.extract_words(_project(), resource_id, page, tuple(bbox) if bbox else None, limit)


@mcp.tool()
def render_page_image(resource_id: str, page: int = 1, resolution: int = 110) -> Image:
    """Render a PDF page (or return an image resource) so you can read scans, maps, and diagrams visually."""
    project = _project()
    info = extract.render_page_image(project, resource_id, page, resolution)
    path = project.path(info["path"])
    return Image(data=path.read_bytes(), format=path.suffix.lstrip(".").lower().replace("jpg", "jpeg"))


# ------------------------------------------------------------------ sources and evidence

@mcp.tool()
def source_upsert(source: dict) -> dict:
    """Create/update a research-inventory row in model/sources.csv. Fields: id, title, jurisdiction,
    district, category, year, url, resource, source_type, status (lead|screened|verified|rejected|unreadable),
    notes, finding, evidence_location. Omitted fields keep their current values."""
    return model_ops.upsert_source(_project(), source)


@mcp.tool()
def source_list(status: str | None = None, district: str | None = None) -> list[dict]:
    """List research-inventory sources, optionally filtered."""
    return [s.dump() | {"id": s.id, "title": s.title, "status": s.status} for s in _project().sources()
            if (status is None or s.status == status) and (district is None or s.district == district)]


@mcp.tool()
def evidence_add(kind: Literal["district", "formula", "roll"], entity_id: str, evidence: dict,
                 target: str | None = None) -> dict:
    """Attach a citation {resource, page, locator, quote, note} to a district/formula/roll. target may be
    'zone:<id>' (district), 'constant:<name>' or 'lookup:<name>' (formula)."""
    return model_ops.add_evidence(_project(), kind, entity_id, evidence, target)


@mcp.tool()
def evidence_for(resource_id: str | None = None, entity_kind: str | None = None,
                 entity_id: str | None = None) -> list[dict]:
    """Provenance: which entities cite a resource, or which resources an entity cites."""
    return index.evidence_for(_project(), resource_id=resource_id, entity_kind=entity_kind, entity_id=entity_id)


# ------------------------------------------------------------------ GIS and rolls

@mcp.tool()
def arcgis_describe(url: str) -> dict:
    """Summarize an ArcGIS REST service or layer: fields, geometry type, record count, max page size."""
    return arcgis.describe(url)


@mcp.tool()
def arcgis_download_layer(name: str, url: str | None = None, where: str = "1=1",
                          kind: Literal["parcels", "overlay", "boundary"] | None = None,
                          batch_size: int | None = None, max_features: int | None = None) -> dict:
    """Download a public ArcGIS feature layer to gis/<name>/ as WGS84 GeoJSON (resumable, rate-limited).
    url defaults to the layer's configured source. Registers the layer in taxmap.yaml."""
    project = _project()
    source = None
    if url:
        params = {"url": url, "where": where}
        if batch_size:
            params["batch_size"] = batch_size
        source = ArcGISSource(**params)
    return arcgis.download_layer(project, name, source, kind=kind, max_features=max_features)


@mcp.tool()
def layer_import(name: str, path: str, kind: Literal["parcels", "overlay", "boundary"] = "overlay",
                 apn_field: str | None = None, id_field: str | None = None, address_field: str | None = None) -> dict:
    """Import a WGS84 GeoJSON FeatureCollection as a project layer."""
    return import_layer(_project(), name, path, kind=kind, apn_field_name=apn_field, id_field=id_field,
                        address_field=address_field)


@mcp.tool()
def layer_configure(name: str, settings: dict) -> dict:
    """Create or update a layer entry in taxmap.yaml (kind, path, source, id_field, apn_field,
    address_field, label_fields)."""
    project = _project()
    current = project.config.layers.get(name)
    data = (current.model_dump(mode="json") if current else {}) | settings
    project.config.layers[name] = LayerConfig.model_validate(data)
    project.save_config()
    return project.config.layers[name].dump()


@mcp.tool()
def layer_list() -> list[dict]:
    """List configured layers and whether data exists."""
    project = _project()
    return [{"name": n, **l.dump(), "has_data": project.layer_path(n).exists()} for n, l in project.config.layers.items()]


@mcp.tool()
def layer_info(name: str) -> dict:
    """Feature count, bounds, and per-field fill rate and sample values (find APN, use code, area fields)."""
    from taxmap.gis import layer_info as info
    return info(_project(), name)


@mcp.tool()
def layer_query(name: str, apn: str | None = None, where: dict | None = None, text: str | None = None,
                point: list[float] | None = None, fields: list[str] | None = None, limit: int = 20,
                include_geometry: bool = False) -> dict:
    """Find features by APN, exact attribute values, text substring, or containing [lon, lat] point."""
    from taxmap.gis import layer_query as query
    return query(_project(), name, apn=apn, where=where, text=text, point=tuple(point) if point else None,
                 fields=fields, limit=limit, include_geometry=include_geometry)


@mcp.tool()
def roll_import(roll_id: str, district: str, csv_path: str | None = None, rows: list[dict] | None = None,
                apn_column: str = "apn", amount_column: str = "assessment", fiscal_year: str | None = None,
                stated_total: float | None = None, stated_count: int | None = None,
                columns: dict[str, str] | None = None, evidence: list[dict] | None = None,
                notes: str | None = None) -> dict:
    """Store an assessment roll (from a CSV path or row dicts) as model/rolls/<id>.csv and check it against
    the document's stated total/count. Extra columns (zone, class, ...) are kept for memberships and inputs."""
    return assess.import_roll(_project(), roll_id, district, csv_path, rows=rows, apn_column=apn_column,
                              amount_column=amount_column, fiscal_year=fiscal_year, stated_total=stated_total,
                              stated_count=stated_count, columns=columns, evidence=evidence, notes=notes)


@mcp.tool()
def roll_summary(roll_id: str) -> dict:
    """Row count, total, duplicates, unparsed amounts, and stated total/count checks for a roll."""
    return assess.roll_summary(_project(), roll_id)


@mcp.tool()
def roll_join_parcels(roll_id: str) -> dict:
    """How many roll APNs match parcels in the parcel layer (after APN normalization)."""
    return assess.roll_join_parcels(_project(), roll_id)


@mcp.tool()
def district_outline_build(only: list[str] | None = None) -> dict:
    """Build approximate outlines for districts/zones from member parcels (gis/districts/outlines.geojson)."""
    from taxmap.gis.outlines import build_outlines
    return build_outlines(_project(), only)


# ------------------------------------------------------------------ model

@mcp.tool()
def district_upsert(district: dict, merge: bool = False) -> dict:
    """Create/replace a district: {id, name, fiscal_year, agency, description, color, membership:
    {roll, where} | {attribute, values}, zones: [{id, name, membership: {roll, where} | {parent: true}}],
    formulas: [ids], total, parcel_count, notes, outline: {gap_m, tolerance_m, fill_holes_m2}, evidence: [...],
    bill_labels: [{code, label, fiscal_year, note, evidence: [...]}]}.
    merge=True shallow-merges into the existing definition."""
    return model_ops.upsert_entity(_project(), "district", district, merge)


@mcp.tool()
def formula_upsert(formula: dict, merge: bool = False) -> dict:
    """Create/replace a formula: {id, name, district, fiscal_year, description, inputs: [{name, label, unit,
    type, source: {parcel: field|[fields]} | {roll, column} | {member: group_id}, default, caveat}],
    constants: {name: {value, label, unit, evidence}}, lookups: {name: {key_type, default, entries:
    [{keys: '11, 62-69', value, label}]}}, terms: [{name, expression}], expression, applies_when, tolerance,
    caveats, evidence}. Expressions support + - * / % ^, comparisons, and/or/not, if(), min, max, round,
    floor, ceil, abs, coalesce, present, lookup(table, key). Compiled and checked before saving."""
    return model_ops.upsert_entity(_project(), "formula", formula, merge)


@mcp.tool()
def model_get(kind: Literal["district", "formula", "roll"], entity_id: str) -> dict:
    """Read a district, formula, or roll definition."""
    project = _project()
    return {"district": project.district, "formula": project.formula, "roll": project.roll}[kind](entity_id).dump()


@mcp.tool()
def formula_evaluate(formula_id: str, apn: str | None = None, inputs: dict | None = None) -> dict:
    """Evaluate a formula for a parcel (inputs resolved from parcel/roll/membership data, overridable)
    or for explicit inputs. Shows terms, lookups, and the official roll amount when available."""
    return assess.evaluate_parcel(_project(), formula_id, apn, inputs)


@mcp.tool()
def formula_validate(formula_id: str, roll_id: str | None = None, max_examples: int = 15) -> dict:
    """Evaluate a formula for every parcel on its district's roll and compare with official amounts."""
    return assess.validate_formula(_project(), formula_id, roll_id, max_examples)


@mcp.tool()
def district_summary() -> list[dict]:
    """Members, zones, roll totals, and stated totals for every district."""
    return assess.district_summary(_project())


# ------------------------------------------------------------------ site

@mcp.tool()
def site_build(output: str | None = None) -> dict:
    """Generate the static Leaflet map site (parcels, districts, calculator modals, sources) into site/."""
    from taxmap.site import build_site
    return build_site(_project(), output)


@mcp.tool()
def site_serve(port: int = 8000, output: str | None = None, stop: bool = False) -> dict:
    """Start (or stop) a local HTTP preview server for the built site in a background thread."""
    from taxmap.site import serve_site
    if _state["server"] is not None:
        _state["server"].shutdown()
        _state["server"].server_close()
        _state["server"] = None
    if stop:
        return {"stopped": True}
    server = serve_site(_project(), output, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _state["server"] = server
    return {"url": f"http://127.0.0.1:{server.server_address[1]}/"}


# ------------------------------------------------------------------ MCP resources

@mcp.resource("taxmap://project")
def project_resource() -> dict:
    """Project summary."""
    return project_info()


@mcp.resource("taxmap://resource/{resource_id}")
def resource_resource(resource_id: str) -> dict:
    """Resource metadata."""
    return resource_get(resource_id)


@mcp.resource("taxmap://resource/{resource_id}/page/{page}")
def resource_page(resource_id: str, page: str) -> str:
    """Extracted text of one page."""
    data = extract.read_pages(_project(), resource_id, int(page), int(page))
    return "\n".join(p["text"] for p in data["pages"])


@mcp.resource("taxmap://model/{kind}/{entity_id}")
def model_resource(kind: str, entity_id: str) -> dict:
    """District, formula, or roll definition."""
    return model_get(kind, entity_id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="taxmap-mcp", description="taxmap MCP server (stdio)")
    parser.add_argument("--project", default=os.environ.get("TAXMAP_PROJECT"), help="Project folder to open")
    args = parser.parse_args(argv)
    if args.project:
        _state["project"] = Project(Path(args.project))
    mcp.run()


if __name__ == "__main__":
    main()
