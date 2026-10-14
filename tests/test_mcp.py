import asyncio
import json
from pathlib import Path
import sys
from urllib.request import urlopen

import pytest

from taxmap import ingest
from taxmap.project import Project, init_project

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SOURCE_ENV = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}


def _attr(obj, *names):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _json(result, many=False):
    if getattr(result, "structuredContent", None):
        content = result.structuredContent
        return content.get("result", content) if isinstance(content, dict) else content
    items = [json.loads(item.text) for item in result.content]
    return items if many else items[0]


async def _session_run(root, resource_id):
    params = StdioServerParameters(command=sys.executable, args=["-m", "taxmap.mcp.server", "--project", str(root)],
                                   env=SOURCE_ENV)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {"project_info", "resource_add_file", "resource_search", "formula_evaluate",
                    "formula_validate", "site_build", "project_validate"} <= tools

            info = _json(await session.call_tool("project_info", {}))
            assert info["config"]["name"] == "demo"

            hits = _json(await session.call_tool("resource_search", {"query": "lighting"}), many=True)
            assert any(hit["ref"] == resource_id for hit in hits)

            templates = {_attr(t, "uri_template", "uriTemplate") for t in
                         _attr(await session.list_resource_templates(), "resource_templates", "resourceTemplates")}
            assert "taxmap://resource/{resource_id}/page/{page}" in templates
            page = await session.read_resource(f"taxmap://resource/{resource_id}/page/1")
            assert "lighting" in page.contents[0].text

            failed = await session.call_tool("formula_evaluate", {"formula_id": "missing"})
            assert _attr(failed, "is_error", "isError")


def test_mcp_stdio(tmp_path):
    root = tmp_path / "demo"
    init_project(root, "demo", "Town of Demo")
    note = ingest.add_note(Project(root), "Council remarks", "Residents asked about the lighting district.")
    asyncio.run(_session_run(root, note.id))


def test_mcp_bootstrap_without_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAXMAP_PROJECT", str(tmp_path / "old-city"))

    async def connect():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "taxmap.mcp.server"],
            env=SOURCE_ENV | {"TAXMAP_PROJECT": ""},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {"project_init", "project_open", "project_info"} <= tools
                info = await session.call_tool("project_info", {})
                assert _attr(info, "is_error", "isError")
                assert not list(tmp_path.iterdir())

                root = tmp_path / "new-city"
                created = await session.call_tool("project_init", {
                    "path": str(root), "name": "new-city",
                    "jurisdiction": "Example City", "focus": "Explain downtown",
                })
                assert not _attr(created, "is_error", "isError")
                assert _json(created)["root"] == str(root.resolve())
                current = _json(await session.call_tool("project_info", {}))
                assert current["root"] == str(root.resolve())
                assert current["config"]["name"] == "new-city"
                assert "Explain downtown" in (root / "PROJECT-PROMPT.md").read_text(encoding="utf-8")
                assert not (tmp_path / "old-city").exists()

    asyncio.run(connect())


def test_guided_app_projectless_stdio(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    layer = tmp_path / "parcels.json"
    layer.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"APN": "001", "OWNER": "private"},
        "geometry": {"type": "Polygon", "coordinates": [
            [[-122, 38], [-121.99, 38], [-121.99, 38.01], [-122, 38.01], [-122, 38]],
        ]},
    }]}), encoding="utf-8")

    async def connect():
        params = StdioServerParameters(command=sys.executable, args=["-m", "taxmap.mcp.server"],
                                       env=SOURCE_ENV | {"TAXMAP_PROJECT": ""})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {"app_start", "app_status", "app_check", "app_review", "app_preview"} <= tools

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not _attr(result, "is_error", "isError"), result
                    return _json(result)

                root = tmp_path / "guided"
                created = await call("app_start", {
                    "jurisdiction": "Example City", "state": "CA", "path": str(root),
                    "fiscal_year": "2026-27",
                })
                assert created["root"] == str(root.resolve())
                note = await call("resource_add_note", {
                    "title": "Synthetic adopted report", "text": "Adopted rate: ten dollars, FY 2026-27.",
                })
                evidence = [{"resource": note["id"], "page": 1}]
                await call("layer_import", {
                    "name": "parcels", "path": str(layer), "kind": "parcels", "apn_field": "APN",
                })
                await call("roll_import", {
                    "roll_id": "annual", "district": "lighting", "rows": [{"apn": "001", "assessment": 10}],
                    "stated_total": 10, "stated_count": 1, "fiscal_year": "2026-27", "evidence": evidence,
                })
                await call("district_upsert", {"district": {
                    "id": "lighting", "name": "Lighting", "membership": {"roll": "annual"},
                    "formulas": ["rate"], "evidence": evidence,
                }})
                await call("formula_upsert", {"formula": {
                    "id": "rate", "name": "Annual rate", "district": "lighting",
                    "expression": "10", "evidence": evidence,
                }})
                await call("app_status", {"progress": {"year_evidence": evidence}})
                report = await call("app_check", {})
                assert report["integrity"]["ok"] and report["status"] == "needs_approval"
                proposed = await call("app_review", {"site": {
                    "publish_fields": ["APN"], "publish_resources": [note["id"]],
                }})
                assert not proposed["approved"]
                approved = await call("app_review", {"approve": proposed["digest"]})
                assert approved["approved"]
                # Configuration reads must observe app_review's on-disk approval.
                current = await call("project_info", {})
                assert current["config"]["site"]["publish_resources"] == [note["id"]]
                preview = await call("app_preview", {})
                with urlopen(preview["url"] + "data/manifest.json", timeout=3) as response:
                    assert json.load(response)["fields"] == ["APN"]
                resumed = await call("app_start", {
                    "jurisdiction": "Example City", "state": "CA", "path": str(root),
                })
                assert resumed["resumed"]
                assert (await call("app_check", {}))["approval_current"]
                assert (await call("app_preview", {}))["url"] == preview["url"]
                assert (await call("app_preview", {"stop": True}))["stopped"]
    asyncio.run(connect())
