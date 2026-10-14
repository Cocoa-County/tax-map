import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from taxmap.project import Project, init_project

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("setup_project", ROOT / "scripts" / "setup_project.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
SHA = "a" * 40


def answers(monkeypatch, values):
    responses = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(responses))


def test_options_custom_location_and_optional_fields(tmp_path, monkeypatch):
    root = tmp_path / "City's map \u00e9"
    answers(monkeypatch, ["My City", str(root), "City of Example", "Public Map", "Example", "CA", "2026-27",
                         "Explain downtown", "C:\\Records\\City's report.pdf", "https://example.org/final", ""])
    options, references = setup.collect_options(None)
    assert options == {
        "name": "My City", "path": str(root), "jurisdiction": "City of Example",
        "title": "Public Map", "county": "Example", "state": "CA", "fiscal-year": "2026-27",
        "focus": "Explain downtown",
    }
    assert references == ["C:\\Records\\City's report.pdf", "https://example.org/final"]
    assert not root.exists()


def test_options_defaults_and_home_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("USERPROFILE" if os.name == "nt" else "HOME", str(tmp_path))
    answers(monkeypatch, ["Example City", "", "Example", "", "", "", "", "", ""])
    options, references = setup.collect_options(None)
    assert options["path"] == str(tmp_path / "taxmap-projects" / "example-city")
    assert options["title"] == "Example City"
    assert "fiscal-year" not in options
    assert "focus" not in options and references == []
    answers(monkeypatch, ["Example", "~/custom", "Example", "", "", "", "", "", ""])
    assert setup.collect_options(None)[0]["path"] == str(tmp_path / "custom")


def test_required_and_invalid_text(monkeypatch):
    answers(monkeypatch, ["", "\x00bad", "Valid"])
    assert setup.prompt("Name", required=True) == "Valid"


@pytest.mark.parametrize("kind", ["nonempty", "manifest", "file"])
def test_existing_destination_is_preserved(tmp_path, kind):
    root = tmp_path / "existing"
    if kind == "file":
        root.write_text("keep")
        existing = root
    else:
        root.mkdir()
        existing = root / ("taxmap.yaml" if kind == "manifest" else "private.txt")
        existing.write_text("keep")
    with pytest.raises(ValueError, match="not an empty"):
        setup.check_destination(root)
    assert existing.read_text() == "keep"


def test_unsafe_destination_relationships(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="outside"):
        setup.check_destination(source / "project", source)
    with pytest.raises(ValueError):
        setup.check_destination(Path.home().resolve())
    with pytest.raises(ValueError):
        setup.check_destination(Path(tmp_path.anchor))


def test_confirm_declined_does_not_write(tmp_path, monkeypatch, capsys):
    root = tmp_path / "declined"
    answers(monkeypatch, ["Name", str(root), "City", "", "", "", "", "", "", "n"])
    assert setup.main(["--revision", SHA]) == 0
    assert not root.exists()
    assert "No project files" in capsys.readouterr().out


def test_cancelled_input(monkeypatch, capsys):
    def interrupt(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", interrupt)
    assert setup.main(["--revision", SHA]) == 130
    assert "cancelled" in capsys.readouterr().err


def test_invalid_revision(capsys):
    assert setup.main(["--revision", "not-a-sha"]) == 1
    assert "40-character" in capsys.readouterr().err


@pytest.mark.parametrize("failed_stage", ["download", "pip", "verify"])
def test_failure_never_reports_success(tmp_path, monkeypatch, capsys, failed_stage):
    root = tmp_path / "partial"
    answers(monkeypatch, ["Name", str(root), "City", "", "", "", "", "", "", "y"])

    def obtain(destination, *_):
        if failed_stage == "download":
            raise OSError("network unavailable")
        destination.mkdir()

    def run(args):
        if failed_stage == "verify" and "init" in args:
            init_project(root, "Name", "City")
        if ((failed_stage == "pip" and "install" in args)
                or (failed_stage == "verify" and "--help" in args)):
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(setup, "obtain_source", obtain)
    monkeypatch.setattr(setup, "run", run)
    assert setup.main(["--revision", SHA]) == 1
    output = capsys.readouterr()
    assert "Setup complete" not in output.out
    assert "Setup did not complete" in output.err
    assert root.exists()


def test_install_initializes_exact_project(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project with spaces"
    calls = []
    monkeypatch.setattr(setup, "obtain_source", lambda destination, *_: destination.mkdir())

    def run(args):
        calls.append(args)
        if "init" in args:
            values = dict(arg[2:].split("=", 1) for arg in args[3:])
            references = [arg.split("=", 1)[1] for arg in args[3:] if arg.startswith("--starting-resource=")]
            init_project(args[2], values["name"], values["jurisdiction"],
                         title=values.get("title"), county=values.get("county"),
                         state=values.get("state"), fiscal_year=values.get("fiscal-year"),
                         focus=values.get("focus"), starting_resources=references)

    monkeypatch.setattr(setup, "run", run)
    setup.install({"path": str(root), "name": "-Example", "jurisdiction": "City",
                   "state": "CA", "fiscal-year": "2026-27", "focus": "-${context}"}, SHA, None,
                  ["-document.pdf", "https://example.org/adopted?a=1,b=2"])
    project = Project(root)
    assert project.config.name == "-Example"
    assert project.config.jurisdiction.state == "CA"
    assert project.config.fiscal_year == "2026-27"
    metadata = json.loads((root / setup.METADATA).read_text())
    assert metadata["revision"] == SHA
    assert metadata["prompt"] == str(root / "PROJECT-PROMPT.md")
    request = (root / "PROJECT-PROMPT.md").read_text(encoding="utf-8")
    assert "-${context}" in request
    assert "-document.pdf" in request
    assert "https://example.org/adopted?a=1,b=2" in request
    assert Path(metadata["mcp"]).is_relative_to(root / ".venv")
    assert ".venv/" in (root / ".gitignore").read_text()
    assert ".taxmap-tools/" in (root / ".gitignore").read_text()
    assert any(args[-1] == "check" for args in calls)
    assert not any("[dev]" in part for args in calls for part in args)
    output = capsys.readouterr().out
    assert str(root) in output
    assert "connection not yet configured" in output
    assert "Read PROJECT-PROMPT.md in this project and follow it." in output
    assert "## Research and build" not in output


def test_installer_requires_generated_request_before_success(tmp_path, monkeypatch):
    root = tmp_path / "missing-request"
    monkeypatch.setattr(setup, "obtain_source", lambda destination, *_: destination.mkdir())
    monkeypatch.setattr(setup, "run", lambda _: None)
    with pytest.raises(ValueError, match="did not generate PROJECT-PROMPT.md"):
        setup.install({"path": str(root), "name": "Name", "jurisdiction": "City"}, SHA, None)
    assert not (root / setup.METADATA).exists()


@pytest.mark.parametrize("entry", ["../escape", f"tax-map-{SHA}/../escape",
                                  f"tax-map-{SHA}/C:/escape", f"tax-map-{SHA}/x\\escape"])
def test_archive_rejects_unsafe_entries(tmp_path, entry):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo()
        info.filename = entry
        bundle.writestr(info, "no")
    with pytest.raises(ValueError, match="Unsafe"):
        setup.unpack_source(archive, tmp_path / "tools", SHA)


def test_archive_extracts_matching_revision(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"tax-map-{SHA}/src/taxmap/__init__.py", "value = 1")
    destination = tmp_path / "tools"
    setup.unpack_source(archive, destination, SHA)
    assert (destination / "src" / "taxmap" / "__init__.py").read_text() == "value = 1"


def test_printed_command_quotes_paths():
    result = setup.command_text(["C:\\City's map\\taxmap.exe", "--project", "city map"])
    assert "city map" in result
    if os.name == "nt":
        assert "City''s map" in result
        assert result.startswith("& ")
    else:
        import shlex
        assert shlex.split(result) == ["C:\\City's map\\taxmap.exe", "--project", "city map"]


def launcher_command():
    if os.name == "nt":
        return ["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "install.ps1")]
    return ["bash", str(ROOT / "scripts" / "install.sh")]


@pytest.mark.parametrize("shell", ["native", "git-bash"])
def test_launcher_preserves_stdin_and_exit_code(tmp_path, shell):
    command = launcher_command()
    if shell == "git-bash":
        bash = Path("C:\\Program Files\\Git\\bin\\bash.exe")
        if os.name != "nt" or not bash.exists():
            pytest.skip("Optional Windows Git Bash check")
        command = [str(bash), str(ROOT / "scripts" / "install.sh")]
    source = tmp_path / "local checkout"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / "setup_project.py").write_text(
        "import sys\nprint('wizard input:', input())\nsys.exit(7)\n", encoding="utf-8")
    result = subprocess.run(command, input="interactive value\n", text=True,
                            capture_output=True, env={**os.environ, "TAXMAP_SOURCE_DIR": str(source)})
    assert result.returncode == 7, result.stdout + result.stderr
    assert "wizard input: interactive value" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="PowerShell bootstrap branch")
@pytest.mark.parametrize("answer, message", [("n", "bootstrap cancelled"), ("y", "download blocked")])
def test_powershell_missing_python_handles_decline_and_download_failure(answer, message):
    script = ROOT / "scripts" / "install.ps1"
    wrapper = (
        "function python { $global:LASTEXITCODE = 1 }; "
        "function python3 { $global:LASTEXITCODE = 1 }; "
        "function py { $global:LASTEXITCODE = 1 }; "
        f"function Read-Host {{ '{answer}' }}; "
        "function Invoke-WebRequest { throw 'download blocked' }; "
        f"& '{str(script).replace(chr(39), chr(39) * 2)}'"
    )
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", wrapper], text=True, capture_output=True)
    assert result.returncode == 1
    assert message in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="PowerShell download failure branch")
def test_powershell_download_failure_is_explicit(monkeypatch):
    monkeypatch.delenv("TAXMAP_SOURCE_DIR", raising=False)
    script = ROOT / "scripts" / "install.ps1"
    wrapper = (
        "function Invoke-RestMethod { throw 'network unavailable' }; "
        f"& '{str(script).replace(chr(39), chr(39) * 2)}'"
    )
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", wrapper], text=True, capture_output=True)
    assert result.returncode == 1
    assert "network unavailable" in result.stderr
    assert "Setup complete" not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Bash bootstrap branch")
def test_bash_missing_python_bootstraps_without_consuming_wizard_input(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ["mktemp", "rm", "rmdir", "sh", "mkdir", "chmod", "dirname"]:
        (bin_dir / command).symlink_to(shutil.which(command))
    for command in ["python", "python3"]:
        file = bin_dir / command
        file.write_text("#!/bin/sh\nexit 1\n")
        file.chmod(0o755)
    # A controlled substitute for the official uv installer and Python download.
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\nwhile [ \"$1\" != '-o' ]; do shift; done\n"
        "cat > \"$2\" <<'UV_INSTALL'\n#!/bin/sh\nmkdir -p \"$UV_UNMANAGED_INSTALL\"\n"
        "printf '#!/bin/sh\\nif [ \"$2\" = \"find\" ]; then printf \"%%s\\\\n\" "
        f"\"{sys.executable}\"; fi\\n' > \"$UV_UNMANAGED_INSTALL/uv\"\n"
        "chmod +x \"$UV_UNMANAGED_INSTALL/uv\"\nUV_INSTALL\n"
    )
    (bin_dir / "cat").symlink_to(shutil.which("cat"))
    curl.chmod(0o755)
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / "setup_project.py").write_text("print('wizard:', input())\n")
    result = subprocess.run([shutil.which("bash"), str(ROOT / "scripts" / "install.sh")],
                            input="y\nstill interactive\n", text=True, capture_output=True,
                            env={**os.environ, "PATH": str(bin_dir), "HOME": str(tmp_path),
                                 "TAXMAP_SOURCE_DIR": str(source)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "wizard: still interactive" in result.stdout


@pytest.mark.skipif(os.environ.get("TAXMAP_INSTALL_SMOKE") != "1", reason="Opt-in network/install smoke test")
def test_real_install(tmp_path):
    root = tmp_path / "Installed city's map \u00e9"
    result = subprocess.run(launcher_command(),
                            input=f"Smoke Project\n{root}\nExample City\n\n\nCA\n\nExplain downtown\n"
                                  "https://example.org/adopted\n\ny\n",
                            text=True, capture_output=True, timeout=600,
                            env={**os.environ, "TAXMAP_SOURCE_DIR": str(ROOT)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Setup complete" in result.stdout
    project = Project(root)
    assert project.config.name == "Smoke Project"
    metadata = json.loads((root / setup.METADATA).read_text(encoding="utf-8"))
    assert metadata["local_source"] == str(ROOT)
    request = Path(metadata["prompt"]).read_text(encoding="utf-8")
    assert "Explain downtown" in request
    assert "https://example.org/adopted" in request
    assert "latest fiscal year" in request
    assert "## Research and build" not in result.stdout
    assert subprocess.run([metadata["python"], "-m", "pip", "check"]).returncode == 0
    assert subprocess.run([metadata["mcp"], "--help"]).returncode == 0

    async def connect():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=metadata["mcp"], args=["--project", str(root)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert "project_info" in {tool.name for tool in (await session.list_tools()).tools}
                info = await session.call_tool("project_info", {})
                payload = info.model_dump(by_alias=True)
                assert not payload["isError"]
                content = payload.get("structuredContent") or json.loads(payload["content"][0]["text"])
                assert content["config"]["name"] == "Smoke Project"
                assert Path(content["root"]) == root

    asyncio.run(connect())
