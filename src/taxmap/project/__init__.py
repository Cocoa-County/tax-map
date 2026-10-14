"""Project folder layout, loading, path confinement, and model persistence."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from taxmap.domain.models import (
    SOURCE_COLUMNS, District, Formula, Jurisdiction, ProjectConfig, Resource, Roll, Source,
)
from taxmap.project.prompt import PROMPT_FILE, render_prompt
from taxmap.util import read_yaml, write_text, write_yaml

MANIFEST = "taxmap.yaml"
RESOURCE_REGISTRY = "resources.yaml"
RESOURCE_DIRS = {
    "document": "resources/documents",
    "web": "resources/web",
    "image": "resources/images",
    "chat": "resources/chats",
    "data": "resources/data",
    "note": "resources/notes",
    "other": "resources/other",
}

PROJECT_GITIGNORE = """\
# Rebuildable cache and bulky intermediate files
.taxmap/
derived/
gis/*/batches/
"""


class ProjectError(ValueError):
    pass


class Project:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        manifest = self.root / MANIFEST
        if not manifest.is_file():
            raise ProjectError(f"No {MANIFEST} in {self.root}; run project_init first")
        try:
            self.config = ProjectConfig.model_validate(read_yaml(manifest) or {})
        except ValidationError as error:
            raise ProjectError(f"Invalid {MANIFEST}: {error}") from error

    # ------------------------------------------------------------------ paths

    def path(self, relative: str | Path, must_exist: bool = False) -> Path:
        """Resolve a project-relative path and refuse anything outside the project."""
        candidate = Path(relative)
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ProjectError(f"Path escapes the project: {relative}")
        if must_exist and not resolved.exists():
            raise ProjectError(f"Not found: {relative}")
        return resolved

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    @property
    def cache_dir(self) -> Path:
        return self.root / ".taxmap"

    @property
    def model_dir(self) -> Path:
        return self.root / "model"

    def derived_dir(self, resource_id: str) -> Path:
        return self.root / "derived" / resource_id

    def layer_path(self, name: str) -> Path:
        layer = self.config.layers.get(name)
        if layer and layer.path:
            return self.path(layer.path)
        return self.root / "gis" / name / "features.geojson"

    def parcel_layer_name(self) -> str:
        for name, layer in self.config.layers.items():
            if layer.kind == "parcels":
                return name
        raise ProjectError("No layer with kind: parcels is configured in taxmap.yaml")

    def save_config(self) -> None:
        write_yaml(self.root / MANIFEST, self.config.dump())

    # ------------------------------------------------------------------ resources

    def resources(self) -> list[Resource]:
        path = self.root / RESOURCE_REGISTRY
        data = read_yaml(path) if path.exists() else None
        return [Resource.model_validate(r) for r in (data or {}).get("resources", [])]

    def resource(self, resource_id: str) -> Resource:
        for r in self.resources():
            if r.id == resource_id:
                return r
        raise ProjectError(f"Unknown resource: {resource_id}")

    def save_resources(self, resources: Iterable[Resource]) -> None:
        items = sorted(resources, key=lambda r: r.id)
        write_yaml(self.root / RESOURCE_REGISTRY, {"resources": [r.dump() for r in items]})

    def upsert_resource(self, resource: Resource) -> Resource:
        others = [r for r in self.resources() if r.id != resource.id]
        self.save_resources([*others, resource])
        return resource

    # ------------------------------------------------------------------ sources

    @property
    def sources_path(self) -> Path:
        return self.model_dir / "sources.csv"

    def sources(self) -> list[Source]:
        if not self.sources_path.exists():
            return []
        with self.sources_path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        return [Source.model_validate({k: (v or None) for k, v in row.items() if k in SOURCE_COLUMNS})
                for row in rows]

    def save_sources(self, sources: Iterable[Source]) -> None:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=SOURCE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for source in sources:
            row = source.model_dump(mode="json")
            writer.writerow({k: "" if row.get(k) is None else row[k] for k in SOURCE_COLUMNS})
        write_text(self.sources_path, buffer.getvalue())

    # ------------------------------------------------------------------ yaml model entities

    def _entity_dir(self, kind: str) -> Path:
        return self.model_dir / {"district": "districts", "formula": "formulas", "roll": "rolls"}[kind]

    def _load_all(self, kind: str, model):
        directory = self._entity_dir(kind)
        items = []
        for path in sorted(directory.glob("*.yaml")) if directory.exists() else []:
            try:
                items.append(model.model_validate(read_yaml(path)))
            except ValidationError as error:
                raise ProjectError(f"Invalid {self.rel(path)}: {error}") from error
        return items

    def districts(self) -> list[District]:
        return self._load_all("district", District)

    def formulas(self) -> list[Formula]:
        return self._load_all("formula", Formula)

    def rolls(self) -> list[Roll]:
        return self._load_all("roll", Roll)

    def _get(self, kind: str, items, entity_id: str):
        for item in items:
            if item.id == entity_id:
                return item
        raise ProjectError(f"Unknown {kind}: {entity_id}")

    def district(self, district_id: str) -> District:
        return self._get("district", self.districts(), district_id)

    def formula(self, formula_id: str) -> Formula:
        return self._get("formula", self.formulas(), formula_id)

    def roll(self, roll_id: str) -> Roll:
        return self._get("roll", self.rolls(), roll_id)

    def save_entity(self, kind: str, entity) -> Path:
        path = self._entity_dir(kind) / f"{entity.id}.yaml"
        write_yaml(path, entity.dump())
        return path

    def roll_csv_path(self, roll: Roll) -> Path:
        return self.path(Path("model") / "rolls" / roll.csv)

    def roll_rows(self, roll: Roll) -> list[dict[str, str]]:
        with self.roll_csv_path(roll).open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream))


def init_project(root: Path | str, name: str, jurisdiction: str, *, title: str | None = None,
                 county: str | None = None, state: str | None = None,
                 fiscal_year: str | None = None, focus: str | None = None,
                 starting_resources: list[str] | None = None) -> Project:
    root = Path(root).resolve()
    if (root / MANIFEST).exists():
        raise ProjectError(f"{root} already contains {MANIFEST}")
    prompt_path = root / PROMPT_FILE
    if prompt_path.exists() or prompt_path.is_symlink():
        raise ProjectError(f"{root} already contains {PROMPT_FILE}; preserve the existing request")
    config = ProjectConfig(
        name=name, title=title or name, fiscal_year=fiscal_year,
        jurisdiction=Jurisdiction(name=jurisdiction, county=county, state=state),
    )
    try:
        prompt = render_prompt(root, config, focus, starting_resources)
    except ValueError as error:
        raise ProjectError(str(error)) from error
    root.mkdir(parents=True, exist_ok=True)
    for directory in [*RESOURCE_DIRS.values(), "derived", "model/districts", "model/formulas",
                      "model/rolls", "model/notes", "gis", "site"]:
        (root / directory).mkdir(parents=True, exist_ok=True)
    write_yaml(root / MANIFEST, config.dump())
    write_yaml(root / RESOURCE_REGISTRY, {"resources": []})
    if not (root / ".gitignore").exists():
        write_text(root / ".gitignore", PROJECT_GITIGNORE)
    project = Project(root)
    project.save_sources([])
    write_text(prompt_path, prompt)
    return project


def find_project(start: Path | str | None = None) -> Path | None:
    """Locate a project root from TAXMAP_PROJECT or by walking up from ``start``."""
    env = os.environ.get("TAXMAP_PROJECT")
    if env and (Path(env) / MANIFEST).is_file():
        return Path(env).resolve()
    current = Path(start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / MANIFEST).is_file():
            return candidate
    return None
