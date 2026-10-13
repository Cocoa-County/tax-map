"""Assessment logic: rolls, district/zone membership, input resolution, validation."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from taxmap.domain.models import District, Evidence, Formula, Roll
from taxmap.formula import Compiled, compile_formula, evaluate, text_value
from taxmap.gis import parcel_index
from taxmap.project import Project, ProjectError
from taxmap.util import normalize_apn, to_number, write_text


# --------------------------------------------------------------------------- rolls

def import_roll(project: Project, roll_id: str, district: str, source: str | Path | None = None, *,
                rows: list[dict] | None = None, apn_column: str = "apn", amount_column: str = "assessment",
                fiscal_year: str | None = None, stated_total: float | None = None,
                stated_count: int | None = None, columns: dict[str, str] | None = None,
                evidence: list[dict] | None = None, notes: str | None = None) -> dict:
    """Store a roll as model/rolls/<id>.csv + .yaml from a CSV file or a list of row dicts."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", roll_id):
        raise ProjectError("Roll ids must be lowercase letters, digits, '-' or '_'")
    if rows is None:
        if source is None:
            raise ProjectError("Provide a CSV path or rows")
        path = project.path(source) if not Path(source).is_absolute() else Path(source)
        with path.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
    if not rows:
        raise ProjectError("Roll has no rows")
    fieldnames = list(rows[0].keys())
    for column in (apn_column, amount_column):
        if column not in fieldnames:
            raise ProjectError(f"Roll is missing column {column!r}; columns are {fieldnames}")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    write_text(project.model_dir / "rolls" / f"{roll_id}.csv", buffer.getvalue())
    roll = Roll(id=roll_id, district=district, fiscal_year=fiscal_year, csv=f"{roll_id}.csv",
                apn_column=apn_column, amount_column=amount_column, stated_total=stated_total,
                stated_count=stated_count, columns=columns or {}, notes=notes,
                evidence=[Evidence.model_validate(e) for e in evidence or []])
    project.save_entity("roll", roll)
    return roll_summary(project, roll_id)


def roll_summary(project: Project, roll_id: str) -> dict:
    roll = project.roll(roll_id)
    rows = project.roll_rows(roll)
    apns = [normalize_apn(r.get(roll.apn_column), project.config.apn.normalize) for r in rows]
    amounts = [to_number(r.get(roll.amount_column)) for r in rows]
    duplicates = [a for a, n in Counter(apns).items() if n > 1]
    total = round(sum(a for a in amounts if a is not None), 2)
    summary = {
        "roll": roll.id, "district": roll.district, "rows": len(rows), "total": total,
        "blank_apns": sum(1 for a in apns if not a), "unparsed_amounts": sum(1 for a in amounts if a is None),
        "duplicate_apns": duplicates[:20], "duplicate_apn_count": len(duplicates),
    }
    checks = []
    if roll.stated_total is not None:
        checks.append({"check": "total", "stated": roll.stated_total, "computed": total,
                       "ok": abs(roll.stated_total - total) < 0.005})
    if roll.stated_count is not None:
        checks.append({"check": "count", "stated": roll.stated_count, "computed": len(rows),
                       "ok": roll.stated_count == len(rows)})
    summary["checks"] = checks
    return summary


def roll_join_parcels(project: Project, roll_id: str) -> dict:
    roll = project.roll(roll_id)
    _, parcels = parcel_index(project)
    unmatched = []
    rows = project.roll_rows(roll)
    for row in rows:
        apn = normalize_apn(row.get(roll.apn_column), project.config.apn.normalize)
        if apn not in parcels:
            unmatched.append(row.get(roll.apn_column))
    return {"roll": roll_id, "rows": len(rows), "matched": len(rows) - len(unmatched),
            "unmatched_count": len(unmatched), "unmatched": unmatched[:50]}


# --------------------------------------------------------------------------- context

class AssessmentContext:
    """Everything needed to resolve memberships and formula inputs for parcels."""

    def __init__(self, project: Project, require_parcels: bool = True):
        self.project = project
        self.mode = project.config.apn.normalize
        try:
            self.apn_field, self.parcels = parcel_index(project)
        except ProjectError:
            if require_parcels:
                raise
            self.apn_field, self.parcels = None, {}
        self.districts = project.districts()
        self.rolls = {r.id: r for r in project.rolls()}
        self.roll_rows: dict[str, dict[str, dict]] = {}
        for roll in self.rolls.values():
            index = {}
            for row in project.roll_rows(roll):
                index.setdefault(normalize_apn(row.get(roll.apn_column), self.mode), row)
            self.roll_rows[roll.id] = index
        self.members = self._memberships()
        self.by_apn: dict[str, set[str]] = defaultdict(set)
        for group, apns in self.members.items():
            for apn in apns:
                self.by_apn[apn].add(group)
        self._compiled: dict[str, Compiled] = {}

    def _resolve(self, membership, parent: set[str] | None) -> set[str]:
        if membership is None:
            return set()
        if membership.parent:
            return set(parent or ())
        if membership.roll:
            if membership.roll not in self.roll_rows:
                raise ProjectError(f"Membership references unknown roll {membership.roll}")
            return {apn for apn, row in self.roll_rows[membership.roll].items()
                    if apn and all(str(row.get(k, "")).strip() == str(v) for k, v in membership.where.items())}
        if membership.attribute:
            accepted = {str(v) for v in membership.values}
            return {apn for apn, props in self.parcels.items()
                    if str(props.get(membership.attribute)) in accepted}
        return set()

    def _memberships(self) -> dict[str, set[str]]:
        members: dict[str, set[str]] = {}
        seen_ids: set[str] = set()
        for district in self.districts:
            if district.id in seen_ids:
                raise ProjectError(f"Duplicate district/zone id {district.id}")
            seen_ids.add(district.id)
            own = self._resolve(district.membership, None)
            zone_sets = {}
            for zone in district.zones:
                if zone.id in seen_ids:
                    raise ProjectError(f"Zone id {zone.id} must be unique across the project")
                seen_ids.add(zone.id)
                zone_sets[zone.id] = self._resolve(zone.membership, own) if not (
                    zone.membership and zone.membership.parent) else None
            if district.membership is None:
                own = set().union(*(s for s in zone_sets.values() if s is not None)) if zone_sets else set()
            for zone in district.zones:
                members[zone.id] = zone_sets[zone.id] if zone_sets[zone.id] is not None else set(own)
            members[district.id] = own
        return members

    def compiled(self, formula: Formula) -> Compiled:
        if formula.id not in self._compiled:
            self._compiled[formula.id] = compile_formula(formula)
        return self._compiled[formula.id]

    def district_of(self, district_id: str) -> District:
        for district in self.districts:
            if district.id == district_id:
                return district
        raise ProjectError(f"Unknown district {district_id}")

    def resolve_inputs(self, formula: Formula, apn: str, overrides: dict | None = None) -> dict[str, Any]:
        apn = normalize_apn(apn, self.mode)
        props = self.parcels.get(apn, {})
        values: dict[str, Any] = {}
        for spec in formula.inputs:
            value: Any = None
            source = spec.source
            if source is not None:
                if source.parcel:
                    for field in [source.parcel] if isinstance(source.parcel, str) else source.parcel:
                        if props.get(field) not in (None, ""):
                            value = props[field]
                            break
                elif source.roll:
                    row = self.roll_rows.get(source.roll, {}).get(apn)
                    value = row.get(source.column) if row and source.column else None
                elif source.member:
                    value = 1.0 if source.member in self.by_apn.get(apn, ()) else 0.0
            if value in (None, ""):
                value = spec.default
            if spec.type == "number" and value is not None:
                value = to_number(value)
            elif value is not None:
                value = text_value(value)
            values[spec.name] = value
        if overrides:
            unknown = set(overrides) - set(values)
            if unknown:
                raise ProjectError(f"Unknown input(s): {sorted(unknown)}")
            values.update(overrides)
        return values

    def official_amounts(self, apn: str) -> list[dict]:
        apn = normalize_apn(apn, self.mode)
        out = []
        for roll in self.rolls.values():
            row = self.roll_rows[roll.id].get(apn)
            if row:
                out.append({"roll": roll.id, "district": roll.district, "fiscal_year": roll.fiscal_year,
                            "amount": to_number(row.get(roll.amount_column)), "row": row})
        return out


# --------------------------------------------------------------------------- operations

def evaluate_parcel(project: Project, formula_id: str, apn: str | None = None,
                    inputs: dict | None = None, context: AssessmentContext | None = None) -> dict:
    """Evaluate a formula for a parcel (inputs resolved from data) and/or explicit inputs."""
    formula = project.formula(formula_id)
    if apn:
        context = context or AssessmentContext(project)
        values = context.resolve_inputs(formula, apn, inputs)
        result = evaluate(context.compiled(formula), values)
        result["apn"] = apn
        result["parcel_found"] = normalize_apn(apn, context.mode) in context.parcels
        result["memberships"] = sorted(context.by_apn.get(normalize_apn(apn, context.mode), ()))
        official = [o for o in context.official_amounts(apn) if o["district"] == formula.district]
        if official:
            result["official"] = official[0]["amount"]
            if result.get("value") is not None and official[0]["amount"] is not None:
                result["difference"] = round(result["value"] - official[0]["amount"], 6)
        return result
    compiled = compile_formula(formula)
    values = {i.name: i.default for i in formula.inputs}
    values.update(inputs or {})
    return evaluate(compiled, values)


def validate_formula(project: Project, formula_id: str, roll_id: str | None = None,
                     max_examples: int = 15, context: AssessmentContext | None = None) -> dict:
    """Compare formula results with official roll amounts, parcel by parcel."""
    formula = project.formula(formula_id)
    context = context or AssessmentContext(project)
    rolls = [r for r in context.rolls.values() if r.district == formula.district]
    if roll_id:
        rolls = [context.rolls[roll_id]] if roll_id in context.rolls else []
    if not rolls:
        return {"formula": formula_id, "status": "no-roll",
                "message": f"No roll for district {formula.district}; cannot validate against official amounts"}
    roll = rolls[0]
    compiled = context.compiled(formula)
    stats = Counter()
    computed_total = official_total = 0.0
    mismatches, missing = [], Counter()
    for apn, row in context.roll_rows[roll.id].items():
        official = to_number(row.get(roll.amount_column))
        stats["rows"] += 1
        if apn not in context.parcels:
            stats["parcel_not_found"] += 1
        result = evaluate(compiled, context.resolve_inputs(formula, apn))
        value = result.get("value")
        if value is None:
            stats["not_computable"] += 1
            missing[result.get("missing") or result.get("error") or "unknown"] += 1
            continue
        official_total += official or 0
        computed_total += value
        if official is not None and abs(value - official) <= formula.tolerance + 1e-9:
            stats["match"] += 1
        else:
            stats["mismatch"] += 1
            if len(mismatches) < max_examples:
                mismatches.append({"apn": row.get(roll.apn_column), "official": official, "computed": value,
                                   "inputs": result["inputs"]})
    computable = stats["rows"] - stats["not_computable"]
    return {
        "formula": formula_id, "roll": roll.id, "tolerance": formula.tolerance, **dict(stats),
        "match_rate": round(stats["match"] / computable, 4) if computable else None,
        "computed_total": round(computed_total, 2), "official_total_of_computable": round(official_total, 2),
        "missing_reasons": dict(missing.most_common(10)), "mismatch_examples": mismatches,
        "status": "pass" if stats["rows"] and stats["match"] == stats["rows"] else "review",
    }


def district_summary(project: Project, context: AssessmentContext | None = None) -> list[dict]:
    context = context or AssessmentContext(project, require_parcels=False)
    out = []
    for district in context.districts:
        rolls = [r for r in context.rolls.values() if r.district == district.id]
        total = sum(to_number(row.get(r.amount_column)) or 0 for r in rolls for row in context.roll_rows[r.id].values())
        out.append({
            "id": district.id, "name": district.name, "fiscal_year": district.fiscal_year,
            "members": len(context.members.get(district.id, ())), "rolls": [r.id for r in rolls],
            "roll_total": round(total, 2), "stated_total": district.total,
            "zones": [{"id": z.id, "name": z.name, "members": len(context.members.get(z.id, ()))}
                      for z in district.zones],
            "formulas": district.formulas,
        })
    return out
