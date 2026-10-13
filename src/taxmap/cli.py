"""taxmap command line: the same deterministic operations as the MCP server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from taxmap.project import Project, ProjectError, find_project, init_project


def _open(args) -> Project:
    root = Path(args.project) if args.project else find_project()
    if root is None:
        raise ProjectError("No taxmap.yaml found; pass --project or run `taxmap init`")
    return Project(root)


def _print(value) -> None:
    if hasattr(value, "dump"):
        value = value.dump()
    print(json.dumps(value, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taxmap", description="AI-ready tax map projects")
    parser.add_argument("--project", "-p", help="Project folder (default: search upward for taxmap.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create a project")
    p.add_argument("path")
    p.add_argument("--name", required=True)
    p.add_argument("--jurisdiction", required=True)
    p.add_argument("--title")
    p.add_argument("--county")
    p.add_argument("--state")
    p.add_argument("--fiscal-year")

    p = sub.add_parser("add", help="Add a file or URL as a resource")
    p.add_argument("target", help="File path or http(s) URL")
    p.add_argument("--title")
    p.add_argument("--kind")
    p.add_argument("--tag", action="append", dest="tags")
    p.add_argument("--origin-url")
    p.add_argument("--link-only", action="store_true")

    sub.add_parser("list", help="List resources")
    p = sub.add_parser("extract", help="Extract text from a resource")
    p.add_argument("resource")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("read", help="Print extracted text for pages")
    p.add_argument("resource")
    p.add_argument("start", type=int, nargs="?", default=1)
    p.add_argument("end", type=int, nargs="?")
    sub.add_parser("index", help="Rebuild the search index")
    p = sub.add_parser("search", help="Full-text search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("gis", help="GIS layers")
    gis = p.add_subparsers(dest="gis_command", required=True)
    g = gis.add_parser("describe", help="Describe an ArcGIS REST layer/service")
    g.add_argument("url")
    g = gis.add_parser("download", help="Download an ArcGIS layer")
    g.add_argument("name")
    g.add_argument("--url")
    g.add_argument("--where", default="1=1")
    g.add_argument("--kind", choices=["parcels", "overlay", "boundary"])
    g.add_argument("--max-features", type=int)
    g = gis.add_parser("import", help="Import a GeoJSON file as a layer")
    g.add_argument("name")
    g.add_argument("path")
    g.add_argument("--kind", default="overlay", choices=["parcels", "overlay", "boundary"])
    g.add_argument("--apn-field")
    g.add_argument("--address-field")
    g = gis.add_parser("info", help="Summarize a layer")
    g.add_argument("name")

    p = sub.add_parser("roll", help="Assessment rolls")
    roll = p.add_subparsers(dest="roll_command", required=True)
    r = roll.add_parser("import", help="Import a roll CSV")
    r.add_argument("roll_id")
    r.add_argument("csv")
    r.add_argument("--district", required=True)
    r.add_argument("--apn-column", default="apn")
    r.add_argument("--amount-column", default="assessment")
    r.add_argument("--fiscal-year")
    r.add_argument("--stated-total", type=float)
    r.add_argument("--stated-count", type=int)
    r = roll.add_parser("summary")
    r.add_argument("roll_id")
    r = roll.add_parser("join")
    r.add_argument("roll_id")

    p = sub.add_parser("evaluate", help="Evaluate a formula for a parcel")
    p.add_argument("formula")
    p.add_argument("--apn")
    p.add_argument("--input", action="append", default=[], help="name=value overrides")
    p = sub.add_parser("check", help="Validate a formula against its roll")
    p.add_argument("formula")
    sub.add_parser("districts", help="District summary")
    p = sub.add_parser("outlines", help="Build approximate district outlines")
    p.add_argument("only", nargs="*")
    p = sub.add_parser("validate", help="Validate the project")
    p.add_argument("--no-hashes", action="store_true")
    p = sub.add_parser("build", help="Build the static map site")
    p.add_argument("--output")
    p = sub.add_parser("serve", help="Serve the built site")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--output")
    sub.add_parser("mcp", help="Run the MCP server on stdio")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (ProjectError, ValueError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args) -> int:
    command = args.command
    if command == "init":
        project = init_project(args.path, args.name, args.jurisdiction, title=args.title, county=args.county,
                               state=args.state, fiscal_year=args.fiscal_year)
        print(f"Created project at {project.root}")
        return 0
    if command == "mcp":
        from taxmap.mcp.server import main as mcp_main
        mcp_main(["--project", args.project] if args.project else [])
        return 0
    if command == "gis" and args.gis_command == "describe":
        from taxmap.gis.arcgis import describe
        _print(describe(args.url))
        return 0
    project = _open(args)
    if command == "add":
        from taxmap import ingest
        if args.target.startswith(("http://", "https://")):
            _print(ingest.add_url(project, args.target, title=args.title, kind=args.kind, tags=args.tags,
                                  link_only=args.link_only))
        else:
            _print(ingest.add_file(project, args.target, title=args.title, kind=args.kind, tags=args.tags,
                                   origin_url=args.origin_url))
    elif command == "list":
        for resource in project.resources():
            print(f"{resource.id:32} {resource.kind:9} {resource.title}")
    elif command == "extract":
        from taxmap.extract import extract_text
        _print(extract_text(project, args.resource, force=args.force))
    elif command == "read":
        from taxmap.extract import read_pages
        for page in read_pages(project, args.resource, args.start, args.end)["pages"]:
            print(f"--- page {page['page']} ---\n{page['text']}")
    elif command == "index":
        from taxmap import index
        _print(index.rebuild(project))
    elif command == "search":
        from taxmap import index
        for hit in index.search(project, args.query, limit=args.limit):
            where = f"{hit['ref']}" + (f" p.{hit['page']}" if hit.get("page") else "")
            print(f"[{hit['kind']}] {where}: {hit['snippet']}")
    elif command == "gis":
        from taxmap import gis
        if args.gis_command == "download":
            from taxmap.domain.models import ArcGISSource
            from taxmap.gis.arcgis import download_layer
            source = ArcGISSource(url=args.url, where=args.where) if args.url else None
            _print(download_layer(project, args.name, source, kind=args.kind, max_features=args.max_features))
        elif args.gis_command == "import":
            _print(gis.import_layer(project, args.name, args.path, kind=args.kind, apn_field_name=args.apn_field,
                                    address_field=args.address_field))
        elif args.gis_command == "info":
            _print(gis.layer_info(project, args.name))
    elif command == "roll":
        from taxmap import assess
        if args.roll_command == "import":
            _print(assess.import_roll(project, args.roll_id, args.district, args.csv, apn_column=args.apn_column,
                                      amount_column=args.amount_column, fiscal_year=args.fiscal_year,
                                      stated_total=args.stated_total, stated_count=args.stated_count))
        elif args.roll_command == "summary":
            _print(assess.roll_summary(project, args.roll_id))
        else:
            _print(assess.roll_join_parcels(project, args.roll_id))
    elif command == "evaluate":
        from taxmap import assess
        inputs = {}
        for item in args.input:
            name, _, value = item.partition("=")
            inputs[name] = value
        _print(assess.evaluate_parcel(project, args.formula, args.apn, inputs or None))
    elif command == "check":
        from taxmap import assess
        _print(assess.validate_formula(project, args.formula))
    elif command == "districts":
        from taxmap import assess
        _print(assess.district_summary(project))
    elif command == "outlines":
        from taxmap.gis.outlines import build_outlines
        _print(build_outlines(project, args.only or None))
    elif command == "validate":
        from taxmap.validate import validate_project
        result = validate_project(project, check_hashes=not args.no_hashes)
        _print(result)
        return 0 if result["ok"] else 2
    elif command == "build":
        from taxmap.site import build_site
        _print(build_site(project, args.output))
    elif command == "serve":
        from taxmap.site import serve_site
        server = serve_site(project, args.output, args.port)
        print(f"Serving http://127.0.0.1:{server.server_address[1]}/ (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
