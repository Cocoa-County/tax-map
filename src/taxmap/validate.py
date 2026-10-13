"""Project integrity checks: registry, model cross-references, formulas, evidence."""

from __future__ import annotations

from taxmap.formula import FormulaError, compile_formula
from taxmap.project import Project, ProjectError
from taxmap.util import sha256_file


def validate_project(project: Project, check_hashes: bool = True) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        resources = project.resources()
    except Exception as error:
        return {"ok": False, "errors": [f"resources.yaml: {error}"], "warnings": []}
    resource_ids = {r.id for r in resources}
    for resource in resources:
        if not resource.path:
            if not resource.origin_url:
                errors.append(f"resource {resource.id}: neither path nor origin_url")
            continue
        try:
            path = project.path(resource.path)
        except ProjectError as error:
            errors.append(f"resource {resource.id}: {error}")
            continue
        if not path.exists():
            errors.append(f"resource {resource.id}: missing file {resource.path}")
        elif check_hashes and resource.sha256 and sha256_file(path) != resource.sha256:
            errors.append(f"resource {resource.id}: file changed since registration (sha256 mismatch)")

    def check_evidence(owner: str, evidence) -> None:
        for item in evidence:
            if item.resource not in resource_ids:
                errors.append(f"{owner}: evidence cites unknown resource {item.resource!r}")

    try:
        sources = project.sources()
        districts = project.districts()
        formulas = project.formulas()
        rolls = project.rolls()
    except ProjectError as error:
        return {"ok": False, "errors": errors + [str(error)], "warnings": warnings}

    for source in sources:
        if source.resource and source.resource not in resource_ids:
            errors.append(f"source {source.id}: unknown resource {source.resource!r}")
        if source.status in {"screened", "verified"} and not (source.resource or source.url):
            warnings.append(f"source {source.id}: {source.status} without a resource or URL")
        if source.status == "verified" and not source.evidence_location:
            warnings.append(f"source {source.id}: verified without evidence_location")
    if len({s.id for s in sources}) != len(sources):
        errors.append("sources.csv: duplicate source ids")

    roll_ids = {r.id for r in rolls}
    district_ids = {d.id for d in districts}
    group_ids = set(district_ids)
    formula_ids = {f.id for f in formulas}
    for district in districts:
        check_evidence(f"district {district.id}", district.evidence)
        for zone in district.zones:
            if zone.id in group_ids:
                errors.append(f"district {district.id}: zone id {zone.id!r} is not unique")
            group_ids.add(zone.id)
            check_evidence(f"zone {zone.id}", zone.evidence)
            if zone.membership and zone.membership.roll and zone.membership.roll not in roll_ids:
                errors.append(f"zone {zone.id}: unknown roll {zone.membership.roll!r}")
        if district.membership and district.membership.roll and district.membership.roll not in roll_ids:
            errors.append(f"district {district.id}: unknown roll {district.membership.roll!r}")
        if district.membership is None and not district.zones:
            warnings.append(f"district {district.id}: no membership rule; map cannot show members")
        for formula_id in district.formulas:
            if formula_id not in formula_ids:
                errors.append(f"district {district.id}: unknown formula {formula_id!r}")
        if not district.evidence:
            warnings.append(f"district {district.id}: no evidence citations")
        if district.notes and not (project.model_dir / "notes" / district.notes).exists():
            errors.append(f"district {district.id}: notes file model/notes/{district.notes} not found")
    for roll in rolls:
        if roll.district not in district_ids:
            errors.append(f"roll {roll.id}: unknown district {roll.district!r}")
        if not project.roll_csv_path(roll).exists():
            errors.append(f"roll {roll.id}: missing CSV {roll.csv}")
        check_evidence(f"roll {roll.id}", roll.evidence)
    for formula in formulas:
        if formula.district not in district_ids:
            errors.append(f"formula {formula.id}: unknown district {formula.district!r}")
        try:
            compile_formula(formula)
        except FormulaError as error:
            errors.append(str(error))
        check_evidence(f"formula {formula.id}", formula.evidence)
        for name, constant in formula.constants.items():
            check_evidence(f"formula {formula.id} constant {name}", constant.evidence)
            if not constant.evidence and not formula.evidence:
                warnings.append(f"formula {formula.id}: constant {name} has no evidence")
        for name, lookup in formula.lookups.items():
            check_evidence(f"formula {formula.id} lookup {name}", lookup.evidence)
        for spec in formula.inputs:
            source = spec.source
            if source and source.member and source.member not in group_ids:
                errors.append(f"formula {formula.id}: input {spec.name} references unknown group {source.member!r}")
            if source and source.roll and source.roll not in roll_ids:
                errors.append(f"formula {formula.id}: input {spec.name} references unknown roll {source.roll!r}")
    for name, layer in project.config.layers.items():
        if not project.layer_path(name).exists():
            warnings.append(f"layer {name}: no data yet at {project.rel(project.layer_path(name))}")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "counts": {"resources": len(resources), "sources": len(sources), "districts": len(districts),
                       "formulas": len(formulas), "rolls": len(rolls), "layers": len(project.config.layers)}}
