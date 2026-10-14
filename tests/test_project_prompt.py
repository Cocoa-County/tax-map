import asyncio
import json
from pathlib import Path

import pytest

from taxmap.cli import main
from taxmap.project import ProjectError, init_project
from taxmap.project.prompt import PROMPT_FILE


def context_from(request):
    start = request.index("    {")
    end = request.index("\n\n", start)
    return json.loads(request[start:end])


@pytest.mark.parametrize("year", [None, "2026-27"])
def test_default_prompt_context_and_fiscal_year(tmp_path, year):
    root = tmp_path / "city"
    init_project(root, "city", "City of Example", title="Assessment Map",
                 county="Example County", state="CA", fiscal_year=year)
    request = (root / PROMPT_FILE).read_text(encoding="utf-8")
    context = context_from(request)
    assert context == {
        "project_path": str(root.resolve()), "name": "city", "map_title": "Assessment Map",
        "jurisdiction": {"name": "City of Example", "county": "Example County", "state": "CA"},
        "fiscal_year": year, "focus": None, "starting_resources": [],
    }
    if year:
        assert 'Use the requested fiscal year "2026-27"' in request
        assert "before substituting another year" in request
        assert "latest fiscal year" not in request
    else:
        assert "latest fiscal year" in request
    assert "This project is already initialized" in request
    assert "Do not commit, push" in request
    assert "1-based PDF page" in request
    assert "localhost-only" in request
    assert "excluding owner names" in request
    assert "Reuse the live plugin-managed MCP connection" in request
    assert "supported activation and verify live readiness" in request
    assert "continue in-session when ready" in request
    assert "If a new session/restart is required" in request
    assert "Call project_open" in request and "root with project_info" in request
    assert "Do not register a separate" in request
    assert "Launch taxmap-mcp with --project" not in request
    assert "site.publish_resources" in request
    assert "before building even a local preview" in request
    assert "Content approval is not permission to commit, upload" in request
    assert "keep resources withheld" in request
    assert "explain missing citations in the preview handoff" in request
    assert "completing the build is not a reason to stop it" in request
    assert "finish reviewing or request shutdown" in request
    assert "one consolidated review" in request
    assert "app_check" in request and "app_preview" in request
    assert "find-jurisdiction-resources" in request
    assert "site.parcel_links" in request
    assert "fork-based PR" in request
    assert "$context" not in request and "$fiscal_year_instruction" not in request
    assert not (root / "model" / "notes" / PROMPT_FILE).exists()


def test_prompt_values_are_not_recursively_expanded(tmp_path):
    root = tmp_path / "City's map \u00e9"
    focus = "-Explain ${context} and $fiscal_year_instruction, {name}, ```"
    references = ["C:\\My files\\report,final.pdf", "https://example.org/a?x=1,y=2", "-draft.pdf"]
    init_project(root, "City \u00e9", "City", focus=focus, starting_resources=references)
    context = context_from((root / PROMPT_FILE).read_text(encoding="utf-8"))
    assert context["focus"] == focus
    assert context["starting_resources"] == references
    assert context["name"] == "City \u00e9"


@pytest.mark.parametrize("kwargs", [
    {"focus": ""}, {"focus": "bad\x00text"}, {"starting_resources": [""]},
    {"starting_resources": ["path\nextra"]}, {"starting_resources": "not a list"},
])
def test_invalid_prompt_inputs_do_not_create_project(tmp_path, kwargs):
    root = tmp_path / "invalid"
    with pytest.raises(ProjectError):
        init_project(root, "city", "City", **kwargs)
    assert not root.exists()


def test_prompt_conflict_preserves_existing_files(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    prompt = root / PROMPT_FILE
    prompt.write_text("User-edited request")
    with pytest.raises(ProjectError, match="preserve"):
        init_project(root, "city", "City")
    assert prompt.read_text() == "User-edited request"
    assert not (root / "taxmap.yaml").exists()


def test_configuration_changes_do_not_regenerate_request(tmp_path):
    project = init_project(tmp_path / "city", "city", "City")
    prompt = project.root / PROMPT_FILE
    prompt.write_text("User-edited request")
    project.config.fiscal_year = "2027-28"
    project.save_config()
    assert prompt.read_text() == "User-edited request"
    with pytest.raises(ProjectError, match="taxmap.yaml"):
        init_project(project.root, "city", "City")
    assert prompt.read_text() == "User-edited request"


def test_cli_initialization_personalizes_prompt(tmp_path, capsys):
    root = tmp_path / "cli"
    assert main(["init", str(root), "--name=city", "--jurisdiction=City", "--focus=-question",
                 "--starting-resource=-report.pdf", "--starting-resource=https://example.org/final"]) == 0
    context = context_from((root / PROMPT_FILE).read_text(encoding="utf-8"))
    assert context["focus"] == "-question"
    assert context["starting_resources"] == ["-report.pdf", "https://example.org/final"]
    assert str(root / PROMPT_FILE) in capsys.readouterr().out


def test_mcp_initialization_generates_same_context(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import sys

    async def initialize():
        root = tmp_path / "mcp"
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "taxmap.mcp.server"],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("project_init", {
                    "path": str(root), "name": "city", "jurisdiction": "City",
                    "focus": "Explain downtown", "starting_resources": ["local report.pdf"],
                })
                assert not result.model_dump(by_alias=True)["isError"]
        context = context_from((root / PROMPT_FILE).read_text(encoding="utf-8"))
        assert context["focus"] == "Explain downtown"
        assert context["starting_resources"] == ["local report.pdf"]
        assert Path(context["project_path"]) == root

    asyncio.run(initialize())
