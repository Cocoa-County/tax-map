"""Interactive installation helper; uses only the Python standard library."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "Cocoa-County/tax-map"
METADATA = "taxmap-install.json"


def prompt(label: str, default: str = "", *, required: bool = False) -> str:
    while True:
        value = input(f"{label}{f' [{default}]' if default else ''}: ").strip() or default
        if any(ord(char) < 32 for char in value):
            print("Use text without control characters.")
        elif value or not required:
            return value
        else:
            print("This field is required.")


def check_destination(root: Path, source: Path | None = None) -> None:
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Choose a new project folder, not your home folder or a filesystem root.")
    if root.is_symlink():
        raise ValueError("The project destination cannot be a symbolic link.")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"Destination is not an empty directory: {root}")
    if source and (root == source or root in source.parents or source in root.parents):
        raise ValueError("Keep the project outside the taxmap source checkout.")


def collect_options(source: Path | None) -> tuple[dict[str, str], list[str]]:
    name = prompt("Project name", required=True)
    folder = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "my-taxmap"
    location = prompt("Exact project folder", str(Path.home() / "taxmap-projects" / folder), required=True)
    raw = Path(location).expanduser()
    if raw.is_symlink():
        raise ValueError("The project destination cannot be a symbolic link.")
    root = raw.resolve()
    check_destination(root, source)
    options = {"name": name, "path": str(root), "jurisdiction": prompt("Jurisdiction (city or district)", required=True)}
    for key, label in [
        ("title", "Map title"), ("county", "County (optional)"), ("state", "State (optional)"),
        ("fiscal-year", "Requested fiscal year (optional; leave blank only if you have no preference)"),
    ]:
        value = prompt(label, name if key == "title" else "")
        if value:
            options[key] = value
    focus = prompt("Public question or initial district focus (optional)")
    if focus:
        options["focus"] = focus
    print("Optional starting documents or official source URLs (one per entry; blank to finish).")
    print("These are unverified references for your agent; setup will not open, upload, or fetch them.")
    references = []
    while value := prompt("Document path or source URL"):
        references.append(value)
    return options, references


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def unpack_source(archive: Path, destination: Path, revision: str) -> None:
    prefix = f"tax-map-{revision}"
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = PurePosixPath(member.filename).parts
            if (not parts or parts[0] != prefix or ".." in parts
                    or "\\" in member.orig_filename or ":" in member.orig_filename
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError(f"Unsafe source archive entry: {member.filename}")
            target = destination.joinpath(*parts[1:])
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError(f"Source archive entry escapes destination: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)


def obtain_source(destination: Path, revision: str | None, source: Path | None) -> None:
    if source:
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", "*.egg-info", ".pytest_cache", "build", "dist",
        ))
    else:
        with tempfile.TemporaryDirectory(prefix="taxmap-source-") as temporary:
            archive = Path(temporary) / "source.zip"
            request = urllib.request.Request(
                f"https://codeload.github.com/{REPOSITORY}/zip/{revision}",
                headers={"User-Agent": "taxmap-installer"},
            )
            with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            unpack_source(archive, destination, str(revision))
    if not (destination / "pyproject.toml").is_file() or not (destination / "src" / "taxmap").is_dir():
        raise ValueError("Downloaded source is not a taxmap source tree.")


def command_text(args: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
    return shlex.join(args)


def install(options: dict[str, str], revision: str | None, source: Path | None,
            starting_resources: list[str] | None = None) -> None:
    root = Path(options["path"])
    check_destination(root, source)
    if not root.exists():
        root.mkdir(parents=True)
    tools = root / ".taxmap-tools"
    obtain_source(tools, revision, source)
    environment = root / ".venv"
    run([sys.executable, "-m", "venv", str(environment)])
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    cli = bin_dir / ("taxmap.exe" if os.name == "nt" else "taxmap")
    mcp = bin_dir / ("taxmap-mcp.exe" if os.name == "nt" else "taxmap-mcp")
    run([str(python), "-m", "pip", "install", str(tools)])
    run([str(python), "-m", "pip", "check"])
    args = [str(cli), "init", str(root)]
    for key, value in options.items():
        if key != "path":
            args.append(f"--{key}={value}")
    for value in starting_resources or []:
        args.append(f"--starting-resource={value}")
    run(args)
    prompt_path = root / "PROJECT-PROMPT.md"
    if not prompt_path.is_file() or not prompt_path.read_text(encoding="utf-8").strip():
        raise ValueError("Project initialization did not generate PROJECT-PROMPT.md")
    ignore = root / ".gitignore"
    with ignore.open("a", encoding="utf-8") as stream:
        stream.write("\n# Local installer tools and environment\n.venv/\n.taxmap-tools/\n")
    run([str(cli), "--help"])
    run([str(mcp), "--help"])
    run([str(python), "-c",
         "import sys; from taxmap.project import Project; "
         "p = Project(sys.argv[1]); assert str(p.root) == sys.argv[1]; print('Project loaded:', p.root)",
         str(root)])
    metadata = {
        "repository": REPOSITORY, "revision": revision,
        "local_source": str(source) if source else None,
        "project": str(root), "tools": str(tools), "python": str(python),
        "bootstrap_python": sys.executable, "cli": str(cli), "mcp": str(mcp),
        "prompt": str(prompt_path),
    }
    (root / METADATA).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"\nSetup complete: {root}\nThis is an empty project, not a researched or published map.")
    print(f"Installation details: {root / METADATA}")
    print(f"Your personalized agent request: {prompt_path}")
    print("\nInstall/sign into your chosen AI CLI separately, then give it this request:")
    print("Read " + str(tools / "SETUP.md")
          + " and follow its plugin steps using " + str(root / METADATA)
          + " for the existing tools checkout and environment. Do not download another checkout, "
          "create another environment, or initialize another project. Ask before changing configuration. "
          "Use this client's supported activation and verify live skills and MCP tools. "
          "Start a new session or restart only if required; save exact resume steps.")
    print("\nOnce live tools are ready, say: Open the existing project at " + str(root)
          + " with project_open, verify its root with project_info, then read "
          + str(prompt_path) + " and follow it.")
    print("If the plugin is already installed and connected, reuse it; do not register independent MCP.")
    print("\nCLI (no activation needed):\n" + command_text([str(cli), "--project", str(root), "validate"]))
    print("\nExisting-project installer handoff guide:\n"
          "https://cocoa-county.github.io/tax-map/docs/community.html#project-install")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--revision", help="Exact GitHub commit SHA")
    origin.add_argument("--source", type=Path, help="Use a local source checkout (development)")
    args = parser.parse_args(argv)
    options = None
    try:
        if sys.version_info < (3, 10):
            raise ValueError("Python 3.10+ is required.")
        if args.revision and not re.fullmatch(r"[0-9a-f]{40}", args.revision):
            raise ValueError("Expected a full 40-character repository commit SHA.")
        source = args.source.resolve() if args.source else None
        if source and not (source / "pyproject.toml").is_file():
            raise ValueError("Local source must contain pyproject.toml.")
        options, starting_resources = collect_options(source)
        print("\nReview setup:")
        for key, value in options.items():
            print(f"  {key}: {value}")
        for value in starting_resources:
            print(f"  Starting reference (unverified): {value}")
        print(f"  Tools source: {source or f'{REPOSITORY}@{args.revision}'}")
        print(f"  Environment: {Path(options['path']) / '.venv'}")
        print("  Install: taxmap + all runtime dependencies; no AI agent or publication.")
        print("  Network: GitHub source download (unless local), PyPI dependencies.")
        if input("Create and install? [y/N]: ").strip().lower() != "y":
            print("Setup cancelled. No project files were created.")
            return 0
        install(options, args.revision, source, starting_resources)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.", file=sys.stderr)
        if options and Path(options["path"]).exists():
            print(f"Inspect any partial files at {options['path']}; use a new empty folder to retry.", file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.CalledProcessError, urllib.error.URLError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        if options:
            print(f"Setup did not complete. Inspect {options['path']} before retrying. "
                  "Existing files are never removed; choose a new empty folder to retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
