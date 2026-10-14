"""Render the default project request without interpreting user-provided text."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from string import Template
from textwrap import indent

from taxmap.domain.models import ProjectConfig

PROMPT_FILE = "PROJECT-PROMPT.md"


def render_prompt(root: Path, config: ProjectConfig, focus: str | None,
                  starting_resources: list[str] | None) -> str:
    if focus is not None and (not isinstance(focus, str) or not focus.strip()):
        raise ValueError("Project focus must be non-empty text when supplied")
    if starting_resources is not None and not isinstance(starting_resources, list):
        raise ValueError("Starting resources must be a list of paths or URLs")
    references = starting_resources or []
    if any(not isinstance(value, str) or not value.strip() for value in references):
        raise ValueError("Each starting resource must be a non-empty path or URL")
    if any(ord(char) < 32 for value in [focus or "", *references] for char in value):
        raise ValueError("Project focus and starting resources cannot contain control characters")
    context = {
        "project_path": str(root),
        "name": config.name,
        "map_title": config.title,
        "jurisdiction": config.jurisdiction.dump(),
        "fiscal_year": config.fiscal_year,
        "focus": focus,
        "starting_resources": references,
    }
    year_instruction = (
        f"Use the requested fiscal year {json.dumps(config.fiscal_year, ensure_ascii=False)}. "
        "Verify final/adopted records for that year. If unavailable, report the gap and ask "
        "before substituting another year."
        if config.fiscal_year else
        "Use the latest fiscal year for which you can verify adopted records. "
        "State the chosen year before modeling."
    )
    template = files("taxmap.project").joinpath("templates", "project-prompt.md").read_text(encoding="utf-8")
    return Template(template).substitute(
        context=indent(json.dumps(context, ensure_ascii=False, indent=2), "    "),
        fiscal_year_instruction=year_instruction,
    )
