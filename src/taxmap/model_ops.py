"""Model write operations shared by the MCP server and CLI (validated upserts, evidence)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from taxmap.domain.models import District, Evidence, Formula, Roll, Source
from taxmap.formula import FormulaError, compile_formula
from taxmap.project import Project, ProjectError
from taxmap.util import slugify

MODELS = {"district": District, "formula": Formula, "roll": Roll}
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _validate(kind: str, data: dict):
    try:
        return MODELS[kind].model_validate(data)
    except ValidationError as error:
        raise ProjectError(f"Invalid {kind}: {error}") from error


def upsert_entity(project: Project, kind: str, data: dict, merge: bool = False) -> dict:
    """Create or replace (or shallow-merge) a district/formula/roll YAML definition."""
    if kind not in MODELS:
        raise ProjectError(f"kind must be one of {sorted(MODELS)}")
    entity_id = data.get("id")
    if not entity_id or not ID_PATTERN.fullmatch(str(entity_id)):
        raise ProjectError("id is required: lowercase letters, digits, '-' or '_'")
    existing = None
    try:
        existing = project._get(kind, project._load_all(kind, MODELS[kind]), entity_id)
    except ProjectError:
        pass
    if merge and existing is not None:
        data = existing.model_dump(mode="json") | data
    entity = _validate(kind, data)
    result: dict[str, Any] = {"kind": kind, "id": entity.id, "created": existing is None}
    if kind == "formula":
        try:
            compiled = compile_formula(entity)
        except FormulaError as error:
            raise ProjectError(f"Formula does not compile: {error}") from error
        result["inputs"] = [i["name"] for i in compiled.data["inputs"]]
        if entity.district not in {d.id for d in project.districts()}:
            result["warning"] = f"District {entity.district!r} does not exist yet"
    if kind == "district":
        group_ids = [entity.id] + [z.id for z in entity.zones]
        for other in project.districts():
            if other.id == entity.id:
                continue
            clash = set(group_ids) & ({other.id} | {z.id for z in other.zones})
            if clash:
                raise ProjectError(f"Ids {sorted(clash)} already used by district {other.id}")
        if len(set(group_ids)) != len(group_ids):
            raise ProjectError("District and zone ids must be unique")
    path = project.save_entity(kind, entity)
    result["path"] = project.rel(path)
    return result


def add_evidence(project: Project, kind: str, entity_id: str, evidence: dict, target: str | None = None) -> dict:
    """Attach a citation to a district (or zone via target='zone:<id>'), formula (or
    'constant:<name>' / 'lookup:<name>'), or roll."""
    if kind not in MODELS:
        raise ProjectError(f"kind must be one of {sorted(MODELS)}")
    item = Evidence.model_validate(evidence)
    project.resource(item.resource)
    entity = project._get(kind, project._load_all(kind, MODELS[kind]), entity_id)
    holder = entity
    if target:
        part, _, name = target.partition(":")
        if kind == "district" and part == "zone":
            holder = next((z for z in entity.zones if z.id == name), None)
        elif kind == "formula" and part == "constant":
            holder = entity.constants.get(name)
        elif kind == "formula" and part == "lookup":
            holder = entity.lookups.get(name)
        else:
            holder = None
        if holder is None:
            raise ProjectError(f"Unknown target {target!r} on {kind} {entity_id}")
    if item not in holder.evidence:
        holder.evidence.append(item)
    project.save_entity(kind, entity)
    return {"kind": kind, "id": entity_id, "target": target, "evidence_count": len(holder.evidence)}


def upsert_source(project: Project, data: dict) -> dict:
    sources = project.sources()
    if not data.get("id"):
        data = dict(data, id=slugify(data.get("title") or "source", "source"))
    by_id = {s.id: s for s in sources}
    current = by_id.get(data["id"])
    merged = (current.model_dump(mode="json") if current else {}) | {k: v for k, v in data.items() if v is not None}
    try:
        source = Source.model_validate(merged)
    except ValidationError as error:
        raise ProjectError(f"Invalid source: {error}") from error
    if source.resource:
        project.resource(source.resource)
    if current:
        sources = [source if s.id == source.id else s for s in sources]
    else:
        sources.append(source)
    project.save_sources(sources)
    return {"id": source.id, "created": current is None, "status": source.status, "total_sources": len(sources)}
