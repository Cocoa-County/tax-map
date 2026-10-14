import asyncio
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
from urllib.parse import unquote, urlsplit

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_plugin", ROOT / "scripts" / "build_plugin.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_bundle(tmp_path):
    executable = tmp_path / "tools with spaces" / "taxmap-mcp"
    executable.parent.mkdir()
    executable.touch()
    output = tmp_path / "City's tools \u00e9"
    plugin = builder.build_plugin(output, executable, "a" * 40)

    manifest = read_json(plugin / "plugin.json")
    assert manifest == read_json(ROOT / "plugin.json")
    from taxmap import __version__
    assert manifest["version"] == __version__
    assert manifest["$schema"].endswith("/1.0.0/plugin.schema.json")
    claude = read_json(plugin / ".claude-plugin" / "plugin.json")
    assert claude["name"] == manifest["name"] == "taxmap"
    assert claude["version"] == manifest["version"]
    mcp = read_json(plugin / "mcp.json")
    assert mcp["$schema"].endswith("/1.0.0/mcp.schema.json")
    assert mcp["mcpServers"] == read_json(plugin / ".mcp.json")["mcpServers"]
    assert mcp["mcpServers"] == {"taxmap": {
        "type": "stdio", "command": str(executable.resolve()),
        "args": [], "env": {"TAXMAP_PROJECT": ""},
    }}
    original_skills = {p.name for p in (ROOT / "skills").iterdir()}
    assert {p.name for p in (plugin / "skills").iterdir()} == original_skills
    assert "taxmap-uninstall" in original_skills
    assert "build-taxmap-app" in original_skills
    assert {"find-jurisdiction-resources", "contribute-jurisdiction-resources"} <= original_skills
    for name in original_skills:
        for original in (ROOT / "skills" / name).rglob("*"):
            if original.is_file():
                copied = plugin / "skills" / name / original.relative_to(ROOT / "skills" / name)
                assert copied.read_bytes() == original.read_bytes()
        skill = (plugin / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(skill.split("---", 2)[1])
        assert frontmatter["name"] == name
        assert frontmatter["description"]

    for name in ["AGENTS.md", "SETUP.md", "UNINSTALL.md"]:
        assert (plugin / name).read_bytes() == (ROOT / name).read_bytes()
    context = read_json(plugin / "taxmap-plugin.json")
    assert context["tools_root"] == str(ROOT)
    assert context["bundle_root"] == str(output)
    assert context["tools_revision"] == "a" * 40
    for name in ["build-taxmap-app", "taxmap-project-setup", "taxmap-uninstall",
                 "find-jurisdiction-resources", "contribute-jurisdiction-resources"]:
        text = (plugin / "skills" / name / "TAXMAP-TOOLS.md").read_text(encoding="utf-8")
        assert json.loads(text.split("```json\n")[1].split("```")[0]) == context
    marketplace = read_json(output / ".claude-plugin" / "marketplace.json")
    codex = read_json(output / ".agents" / "plugins" / "marketplace.json")
    assert marketplace["name"] == codex["name"] == "taxmap-local"
    assert marketplace["plugins"][0]["source"] == "./taxmap"
    assert codex["plugins"][0]["source"] == {"source": "local", "path": "./taxmap"}
    assert not list(output.rglob("taxmap.yaml"))


@pytest.mark.parametrize("existing", ["empty", "nonempty", "file"])
def test_plugin_refuses_existing_destination(tmp_path, existing):
    output = tmp_path / "bundle"
    if existing == "file":
        output.write_text("keep")
    else:
        output.mkdir()
        if existing == "nonempty":
            (output / "keep").write_text("keep")
    before = sorted((str(p), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())
    with pytest.raises(ValueError, match="already exists"):
        builder.build_plugin(output, Path(sys.executable), "revision")
    assert sorted((str(p), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()) == before


def test_plugin_refuses_missing_executable(tmp_path):
    with pytest.raises(ValueError, match="executable not found"):
        builder.build_plugin(tmp_path / "bundle", tmp_path / "missing", "revision")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("entry_point", [
    "build-taxmap-app", "acquire-parcel-gis", "screen-sources", "build-map-site",
])
def test_catalog_discovery_entry_points(entry_point):
    text = (ROOT / "skills" / entry_point / "SKILL.md").read_text(encoding="utf-8")
    assert "`find-jurisdiction-resources`" in text


def test_bundled_guidance_resolves_links_and_checkout_assets(tmp_path):
    plugin = builder.build_plugin(tmp_path / "bundle", Path(sys.executable), "test-revision")
    for path in plugin.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            url = urlsplit(link)
            if url.scheme or url.netloc or not url.path:
                continue
            assert (path.parent / unquote(url.path)).exists(), (path.relative_to(plugin), link)
    skill = plugin / "skills" / "build-map-site" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "../../taxmap-plugin.json" in text and "tools_root" in text
    context = read_json(skill.parent / ".." / ".." / "taxmap-plugin.json")
    template = Path(context["tools_root"]) / "templates" / "github-pages.yml"
    assert template.is_file()
    assert template.read_bytes() == (ROOT / "templates" / "github-pages.yml").read_bytes()
    assert not (plugin / "templates").exists()


def test_plugin_refuses_output_inside_source_skills():
    output = ROOT / "skills" / "taxmap-uninstall" / "generated-test-bundle"
    with pytest.raises(ValueError, match="preserve source"):
        builder.build_plugin(output, Path(sys.executable), "revision")
    assert not output.exists()


@pytest.mark.parametrize("revision", ["", " ", "rev\ninjected", "rev\x00"])
def test_plugin_refuses_invalid_revision(tmp_path, revision):
    with pytest.raises(ValueError, match="revision"):
        builder.build_plugin(tmp_path / "bundle", Path(sys.executable), revision)
    assert not (tmp_path / "bundle").exists()


def test_plugin_cli_reports_failure(tmp_path):
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "build_plugin.py"),
        "--output", str(tmp_path / "bundle"), "--mcp", str(tmp_path / "missing"),
        "--revision", "revision",
    ], text=True, capture_output=True)
    assert result.returncode == 1
    assert "Plugin build failed:" in result.stderr
    assert "Plugin built:" not in result.stdout


def test_plugin_cli_activation_handoff(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "build_plugin.py", "--output", str(tmp_path / "bundle"),
        "--mcp", sys.executable, "--revision", "revision",
    ])
    assert builder.main() == 0
    output = capsys.readouterr().out
    assert "Not registered yet" in output
    assert "install and activate" in output
    assert "verify live skills and MCP tools" in output
    assert "new session or restart only if required" in output
    assert "restart after installation" not in output


def test_bundled_mcp_without_project(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    executable = Path(sysconfig.get_path("scripts")) / (
        "taxmap-mcp.exe" if sys.platform == "win32" else "taxmap-mcp"
    )
    assert executable.is_file(), "Install the package's entry points before running plugin MCP tests"
    plugin = builder.build_plugin(tmp_path / "bundle", executable, "test-revision")
    server = read_json(plugin / "mcp.json")["mcpServers"]["taxmap"]
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    async def connect():
        params = StdioServerParameters(command=server["command"], args=server["args"],
                                       env=server["env"] | {"PYTHONPATH": str(ROOT / "src")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {"project_init", "project_open", "project_info", "app_start", "app_review",
                        "app_check", "app_preview"} <= tools
                assert not list(unrelated.iterdir())

    asyncio.run(connect())
