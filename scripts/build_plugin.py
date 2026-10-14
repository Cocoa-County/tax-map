"""Build a local plugin marketplace with machine-specific, projectless MCP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build_plugin(output: Path, mcp_executable: Path, revision: str) -> Path:
    output = output.expanduser().resolve()
    mcp_executable = mcp_executable.expanduser().resolve()
    if output.exists():
        raise ValueError(f"Destination already exists; choose a new bundle directory: {output}")
    if ROOT in output.parents and output.relative_to(ROOT).parts[0] != ".taxmap-plugins":
        raise ValueError("Inside the checkout, build only under .taxmap-plugins to preserve source files")
    if not mcp_executable.is_file():
        raise ValueError(f"MCP executable not found: {mcp_executable}")
    if not revision.strip() or any(ord(char) < 32 for char in revision):
        raise ValueError("Provide the tools revision without control characters")

    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    skills = sorted((ROOT / "skills").iterdir())
    for skill in skills:
        if not skill.is_dir() or not (skill / "SKILL.md").is_file():
            raise ValueError(f"Invalid skill directory: {skill}")
        if any(path.is_symlink() for path in [skill, *skill.rglob("*")]):
            raise ValueError(f"Symlinks are not supported in bundled skills: {skill}")

    output.mkdir(parents=True, exist_ok=False)
    plugin = output / "taxmap"
    plugin.mkdir()
    _write_json(plugin / "plugin.json", manifest)
    # Claude uses its compatibility manifest and MCP layout.
    _write_json(plugin / ".claude-plugin" / "plugin.json", {
        key: value for key, value in manifest.items() if key != "$schema"
    })
    server = {"type": "stdio", "command": str(mcp_executable), "args": [],
              "env": {"TAXMAP_PROJECT": ""}}
    _write_json(plugin / "mcp.json", {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"taxmap": server},
    })
    _write_json(plugin / ".mcp.json", {"mcpServers": {"taxmap": server}})
    for skill in skills:
        shutil.copytree(skill, plugin / "skills" / skill.name)
    for name in ["AGENTS.md", "SETUP.md", "UNINSTALL.md"]:
        shutil.copy2(ROOT / name, plugin / name)

    context = {
        "plugin": "taxmap", "marketplace": "taxmap-local",
        "tools_root": str(ROOT), "bundle_root": str(output),
        "mcp_executable": str(mcp_executable), "tools_revision": revision,
        "handoff": str(ROOT / "TAXMAP-SETUP.md"),
    }
    _write_json(plugin / "taxmap-plugin.json", context)
    supporting = (
        "# Local taxmap tools\n\n"
        "Read this context and the bundled AGENTS.md before project work or cleanup.\n"
        "The plugin manager owns the skills and MCP entry; do not register them separately.\n\n"
        "```json\n" + json.dumps(context, indent=2) + "\n```\n"
    )
    for name in ["taxmap-project-setup", "taxmap-uninstall"]:
        (plugin / "skills" / name / "TAXMAP-TOOLS.md").write_text(supporting, encoding="utf-8")

    entry = {"name": "taxmap", "source": "./taxmap", "version": manifest["version"],
             "description": manifest["description"]}
    marketplace = {"name": "taxmap-local", "owner": {"name": "Cocoa County"},
                   "plugins": [entry]}
    _write_json(output / ".claude-plugin" / "marketplace.json", marketplace)
    _write_json(output / ".agents" / "plugins" / "marketplace.json", {
        "name": "taxmap-local",
        "plugins": [{
            "name": "taxmap", "source": {"source": "local", "path": "./taxmap"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    })
    return plugin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New local marketplace directory; existing destinations are refused")
    parser.add_argument("--mcp", type=Path, required=True, help="Absolute installed taxmap-mcp executable")
    parser.add_argument("--revision", required=True, help="Tools checkout commit or archive revision")
    args = parser.parse_args()
    try:
        plugin = build_plugin(args.output, args.mcp, args.revision)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Plugin build failed: {error}\n")
    print(f"Plugin built: {plugin}\nMarketplace: {plugin.parent}\n"
          "Not registered yet. Follow SETUP.md for your CLI; restart after installation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
