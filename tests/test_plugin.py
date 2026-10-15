import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
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


@pytest.mark.parametrize("client", ["codex", "claude", "copilot"])
def test_plugin_bundle(tmp_path, client):
    executable = tmp_path / "tools with spaces" / "taxmap-mcp"
    executable.parent.mkdir()
    executable.touch()
    output = tmp_path / "City's tools \u00e9"
    plugin = builder.build_plugin(output, executable, "a" * 40, client)

    source = read_json(ROOT / "plugin.json")
    if client == "codex":
        assert not (plugin / "plugin.json").exists()
        assert not (plugin / "mcp.json").exists()
        manifest = read_json(plugin / ".codex-plugin" / "plugin.json")
        assert manifest == {
            **{key: value for key, value in source.items() if key != "$schema"},
            "skills": "./skills/", "mcpServers": "./.mcp.json",
        }
        assert (plugin / manifest["skills"]).is_dir()
        assert (plugin / manifest["mcpServers"]).is_file()
    else:
        manifest = read_json(plugin / "plugin.json")
        assert manifest == source
        assert not (plugin / ".codex-plugin").exists()
    from taxmap import __version__
    assert manifest["version"] == __version__
    assert source["$schema"].endswith("/1.0.0/plugin.schema.json")
    claude = read_json(plugin / ".claude-plugin" / "plugin.json")
    assert claude["name"] == manifest["name"] == "taxmap"
    assert claude["version"] == manifest["version"]
    mcp = read_json(plugin / ".mcp.json")
    if client != "codex":
        portable = read_json(plugin / "mcp.json")
        assert portable["$schema"].endswith("/1.0.0/mcp.schema.json")
        assert portable["mcpServers"] == mcp["mcpServers"]
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
    assert context["client"] == client
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
        builder.build_plugin(output, Path(sys.executable), "revision", "codex")
    assert sorted((str(p), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()) == before


def test_plugin_refuses_missing_executable(tmp_path):
    with pytest.raises(ValueError, match="executable not found"):
        builder.build_plugin(tmp_path / "bundle", tmp_path / "missing", "revision", "codex")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("entry_point", [
    "build-taxmap-app", "acquire-parcel-gis", "screen-sources", "build-map-site",
])
def test_catalog_discovery_entry_points(entry_point):
    text = (ROOT / "skills" / entry_point / "SKILL.md").read_text(encoding="utf-8")
    assert "`find-jurisdiction-resources`" in text


def test_bundled_standard_community_contribution_offer(tmp_path):
    plugin = builder.build_plugin(tmp_path / "bundle", Path(sys.executable), "test-revision", "codex")
    expectations = {
        "build-taxmap-app": [
            "## Standard community contribution offer", "two independent questions",
            "no preselected consent", 'ambiguous combined "yes"',
            "derived/catalog-contribution.json", "declined/deferred",
            "fresh sharing approval", "preview first", "failures never block the app",
        ],
        "find-jurisdiction-resources": [
            "## Standard offer, optional submission",
            "without waiting for a separate user request",
            "separate explicit sharing choice", "when nothing eligible changed",
            "declined/deferred", "never require a contribution",
        ],
        "contribute-jurisdiction-resources": [
            "standard contribution offer", "separate explicit sharing choice",
            "no preselected consent", 'ambiguous combined "yes"',
            "derived/catalog-contribution.json", "declined/deferred",
            "fresh approval", "preview first", "failures must not block",
        ],
    }
    for name, phrases in expectations.items():
        text = " ".join((plugin / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8",
        ).split())
        for phrase in ["whole community", *phrases]:
            assert phrase in text, (name, phrase)


@pytest.mark.parametrize("client", ["codex", "claude", "copilot"])
def test_bundled_guidance_resolves_links_and_checkout_assets(tmp_path, client):
    plugin = builder.build_plugin(tmp_path / "bundle", Path(sys.executable), "test-revision", client)
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
        builder.build_plugin(output, Path(sys.executable), "revision", "codex")
    assert not output.exists()


@pytest.mark.parametrize("revision", ["", " ", "rev\ninjected", "rev\x00"])
def test_plugin_refuses_invalid_revision(tmp_path, revision):
    with pytest.raises(ValueError, match="revision"):
        builder.build_plugin(tmp_path / "bundle", Path(sys.executable), revision, "codex")
    assert not (tmp_path / "bundle").exists()


def test_plugin_cli_reports_failure(tmp_path):
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "build_plugin.py"), "--client", "codex",
        "--output", str(tmp_path / "bundle"), "--mcp", str(tmp_path / "missing"),
        "--revision", "revision",
    ], text=True, capture_output=True)
    assert result.returncode == 1
    assert "Plugin build failed:" in result.stderr
    assert "Plugin built:" not in result.stdout


def test_plugin_cli_activation_handoff(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "build_plugin.py", "--client", "codex", "--output", str(tmp_path / "bundle"),
        "--mcp", sys.executable, "--revision", "revision",
    ])
    assert builder.main() == 0
    output = capsys.readouterr().out
    assert "Not registered yet" in output
    assert "install and activate" in output
    assert "verify live skills and MCP tools" in output
    assert "fully quit/reopen the desktop app or exit/relaunch the CLI" in output
    assert "fresh conversation" in output


@pytest.mark.parametrize("client", ["codex", "claude", "copilot"])
def test_bundled_mcp_without_project(tmp_path, monkeypatch, client):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    executable = Path(sysconfig.get_path("scripts")) / (
        "taxmap-mcp.exe" if sys.platform == "win32" else "taxmap-mcp"
    )
    assert executable.is_file(), "Install the package's entry points before running plugin MCP tests"
    plugin = builder.build_plugin(tmp_path / "bundle", executable, "test-revision", client)
    server = read_json(plugin / ".mcp.json")["mcpServers"]["taxmap"]
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    async def connect():
        params = StdioServerParameters(command=server["command"], args=server["args"],
                                       env=server["env"] | {"PYTHONPATH": str(ROOT / "src")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                instructions = " ".join(initialized.instructions.split())
                assert "offer eligible catalog additions/corrections" in instructions
                assert "whole community" in instructions
                assert "separate explicit sharing choice" in instructions
                assert "preview approval never permits sharing" in instructions
                assert "failures never block the app" in instructions
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert {"project_init", "project_open", "project_info", "app_start", "app_review",
                        "app_check", "app_preview"} <= tools
                assert not list(unrelated.iterdir())

    asyncio.run(connect())


def test_plugin_requires_explicit_client(tmp_path):
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "build_plugin.py"),
        "--output", str(tmp_path / "bundle"), "--mcp", sys.executable,
        "--revision", "test-revision",
    ], text=True, capture_output=True)
    assert result.returncode == 2
    assert "--client" in result.stderr
    assert not (tmp_path / "bundle").exists()
    with pytest.raises(ValueError, match="Unsupported client"):
        builder.build_plugin(tmp_path / "bundle", Path(sys.executable), "revision", "unknown")
    assert not (tmp_path / "bundle").exists()


@pytest.mark.skipif(os.environ.get("TAXMAP_CODEX_SMOKE") != "1",
                    reason="Opt-in Codex plugin-manager/app-server integration")
def test_codex_loader(tmp_path):
    """Exercise the real loader without touching the user's Codex installation."""
    codex = shutil.which("codex")
    assert codex, "Install Codex before opting into TAXMAP_CODEX_SMOKE"
    home = tmp_path / "isolated codex home"
    home.mkdir()
    unrelated = tmp_path / "unrelated working directory"
    unrelated.mkdir()
    # Only child processes receive the isolated home. Never copy auth or config.
    env = {**os.environ, "CODEX_HOME": str(home), "TAXMAP_PROJECT": "",
           "PYTHONPATH": str(ROOT / "src")}
    executable = Path(sysconfig.get_path("scripts")) / (
        "taxmap-mcp.exe" if sys.platform == "win32" else "taxmap-mcp"
    )
    plugin = builder.build_plugin(tmp_path / "City's bundle \u00e9", executable,
                                  "codex-loader-test", "codex")
    for args in [
        ["plugin", "marketplace", "add", str(plugin.parent), "--json"],
        ["plugin", "add", "taxmap@taxmap-local", "--json"],
    ]:
        result = subprocess.run([codex, *args], cwd=unrelated, env=env,
                                text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr

    expected_skills = {f"taxmap:{p.name}" for p in (ROOT / "skills").iterdir()}
    expected_tools = {"app_start", "app_status", "app_check", "app_review", "app_preview",
                      "project_init", "project_open", "project_info"}
    config = home / "config.toml"
    installed_config = config.read_text(encoding="utf-8")
    assert installed_config.count("enabled = true") == 1, installed_config

    async def inspect(enabled):
        config.write_text(installed_config.replace("enabled = true", f"enabled = {str(enabled).lower()}"),
                          encoding="utf-8")
        # stderr goes to disk so diagnostics cannot block the protocol pipe.
        with (tmp_path / f"app-server-{enabled}.log").open("wb") as diagnostics:
            process = await asyncio.create_subprocess_exec(
                codex, "app-server", "--stdio",
                cwd=unrelated, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=diagnostics,
            )
            request_id = 0

            async def rpc(method, params):
                nonlocal request_id
                request_id += 1
                process.stdin.write((json.dumps({"id": request_id, "method": method,
                                                 "params": params}) + "\n").encode())
                await process.stdin.drain()
                while True:
                    line = await process.stdout.readline()
                    assert line, "Codex exited before responding; inspect app-server log"
                    message = json.loads(line)
                    if message.get("id") == request_id:
                        assert "error" not in message, message
                        return message["result"]

            try:
                await asyncio.wait_for(rpc("initialize", {
                    "clientInfo": {"name": "taxmap_packaging_test", "version": "1.0.0"},
                    "capabilities": {"experimentalApi": True},
                }), timeout=45)
                process.stdin.write(b'{"method":"initialized","params":{}}\n')
                await process.stdin.drain()
                detail = await asyncio.wait_for(rpc("plugin/read", {
                    "pluginName": "taxmap", "marketplacePath": str(
                        plugin.parent / ".agents" / "plugins" / "marketplace.json"
                    ),
                }), timeout=45)
                detail = detail["plugin"]
                assert detail["summary"]["installed"]
                assert detail["summary"]["enabled"] == enabled
                status = await asyncio.wait_for(rpc("mcpServerStatus/list", {}), timeout=45)
                inventory = await asyncio.wait_for(rpc("skills/list", {
                    "cwds": [str(unrelated)], "forceReload": True,
                }), timeout=45)
                runtime_skills = {s["name"] for entry in inventory["data"] for s in entry["skills"]
                                  if s["enabled"] and s["name"].startswith("taxmap:")}
                if enabled:
                    assert {s["name"] for s in detail["skills"] if s["enabled"]} == expected_skills
                    assert runtime_skills == expected_skills
                    assert detail["mcpServers"] == ["taxmap"]
                    servers = [s for s in status["data"] if s.get("pluginId") == "taxmap@taxmap-local"]
                    assert len(servers) == 1, status
                    server = servers[0]
                    assert not server.get("toolsError"), server
                    assert expected_tools <= {t["name"] for t in server["tools"].values()}, server
                else:
                    # plugin/read describes package contents even when disabled.
                    assert not runtime_skills
                    assert not status["data"], status
            finally:
                if process.returncode is None:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    # New processes model restart, plugin disable, and re-enable.
    for enabled in [True, False, True]:
        asyncio.run(inspect(enabled))
    assert not (unrelated / "taxmap.yaml").exists()
    assert not (unrelated / "app.yaml").exists()
