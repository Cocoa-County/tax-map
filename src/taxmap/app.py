"""Deterministic lifecycle for an assistant-guided app; never an AI runner."""

from __future__ import annotations

from collections import Counter
import hashlib
import html
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import threading

from taxmap import assess
from taxmap.domain.models import (
    AppPreviewHandoff, AppProgress, AppReadiness, AppReview, AppState, Jurisdiction, SiteConfig,
)
from taxmap.gis import geometry_bounds, load_layer
from taxmap.project import MANIFEST, Project, ProjectError, init_project
from taxmap.site import build_site, serve_site
from taxmap.util import (
    normalize_apn, read_json, read_yaml, sha256_file, slugify, write_json, write_yaml,
    unique_id,
)
from taxmap.validate import validate_project

STATE_FILE = "app.yaml"
DISCLAIMER = (
    "Not a full tax bill. An absent charge does not mean zero. "
    "The adopted roll and county tax bill are authoritative; outlines are approximate."
)


def _fresh(project: Project) -> Project:
    return Project(project.root)


def _state(project: Project) -> AppState:
    path = project.path(STATE_FILE)
    if not path.exists():
        raise ProjectError("No guided app state. Call app_start for this project's jurisdiction and path.")
    state = AppState.model_validate(read_yaml(path))
    actual = project.config.jurisdiction
    matches = all((left or "").casefold() == (right or "").casefold() for left, right in [
        (state.jurisdiction.name, actual.name), (state.jurisdiction.state, actual.state),
        (state.jurisdiction.country, actual.country),
    ])
    if state.jurisdiction.county:
        matches = matches and state.jurisdiction.county.casefold() == (actual.county or "").casefold()
    if state.name != project.config.name or not matches:
        raise ProjectError("app.yaml identity differs from taxmap.yaml; resolve the project identity before resuming")
    state.jurisdiction = actual
    return state


def _save(project: Project, state: AppState) -> None:
    write_yaml(project.path(STATE_FILE), state.model_dump(mode="json", exclude_none=True))
    if state.last_report is not None:
        write_json(project.path("derived/app-check.json"), state.last_report.model_dump(mode="json"), indent=2)


def _name(value: str) -> str:
    if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,59}", value)
            or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", value)):
        raise ProjectError("Choose a non-reserved project ID of 1-60 lowercase letters, digits, '-' or '_'")
    return value


def start(jurisdiction: str, *, state: str | None = None, path: str | None = None,
          name: str | None = None, title: str | None = None, county: str | None = None,
          fiscal_year: str | None = None, focus: str | None = None,
          starting_resources: list[str] | None = None) -> tuple[Project, dict]:
    """Create safely or resume a clearly matching project, without changing its request."""
    jurisdiction = jurisdiction.strip()
    if not jurisdiction:
        raise ProjectError("Jurisdiction is required")
    if state is not None:
        state = state.strip() or None
    if fiscal_year is not None and not fiscal_year.strip():
        raise ProjectError("A requested fiscal year cannot be blank")
    project_id = _name(name if name is not None else slugify(jurisdiction, fallback=""))
    root = Path(path).expanduser().resolve() if path else Path.home() / "taxmap-projects" / project_id
    requested = Jurisdiction(name=jurisdiction, state=state, county=county)
    resumed = (root / MANIFEST).is_file()
    if resumed:
        project = Project(root)
        actual = project.config.jurisdiction
        matches = actual.name.casefold() == requested.name.casefold()
        matches = matches and (actual.state or "").casefold() == (requested.state or "").casefold()
        matches = matches and (county is None or (actual.county or "").casefold() == county.casefold())
        matches = matches and (name is None or project.config.name == name)
        matches = matches and (title is None or project.config.title == title)
        if not matches:
            raise ProjectError(f"Existing project at {root} has a different identity; ask before choosing another path")
        guided = _state(project) if project.path(STATE_FILE).exists() else AppState(
            name=project.config.name, jurisdiction=actual,
            requested_fiscal_year=project.config.fiscal_year,
        )
        if fiscal_year is not None:
            years = {year for year in (guided.requested_fiscal_year, project.config.fiscal_year) if year}
            if years - {fiscal_year}:
                raise ProjectError("Requested fiscal year conflicts with the existing project; ask before changing scope")
            guided.requested_fiscal_year = fiscal_year
            if project.config.fiscal_year is None:
                project.config.fiscal_year = fiscal_year
                project.save_config()
    else:
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ProjectError(f"Target is not an empty project directory: {root}; preserve it and ask for another path")
        project = init_project(root, project_id, jurisdiction,
                               title=title or f"{jurisdiction} Tax and Assessment Map",
                               county=county, state=state, fiscal_year=fiscal_year,
                               focus=focus, starting_resources=starting_resources)
        project.config.site.publish_fields = []
        project.save_config()
        guided = AppState(name=project.config.name, jurisdiction=project.config.jurisdiction,
                          requested_fiscal_year=fiscal_year)
    if focus is not None and not resumed:
        guided.progress.scope = focus
    _save(project, guided)
    return project, {
        "root": str(project.root), "resumed": resumed, "config": project.config.dump(),
        "state": guided.model_dump(mode="json"),
        "next_action": "Read PROJECT-PROMPT.md if present; continue official-source research and modeling. "
                       "Do not ask about optional defaults. Record adoption evidence and gaps with app_status.",
    }


def check(project: Project, site: SiteConfig | None = None) -> AppReadiness:
    """Compose integrity and data reconciliation; approval is checked separately."""
    project = _fresh(project)
    if site is not None:
        project.config.site = site
    state = _state(project)
    integrity = validate_project(project)
    blockers = list(integrity["errors"])
    caveats = list(state.progress.issues)
    if state.progress.coverage == "partial":
        caveats.append("Partial assessment coverage; not a complete inventory or tax bill.")
    caveats.extend(f"Unmodeled: {item}" for item in state.progress.unmodeled)
    year = project.config.fiscal_year
    if not year:
        blockers.append("Select a fiscal year from verified adopted records before previewing.")
    if state.requested_fiscal_year and year != state.requested_fiscal_year:
        blockers.append(f"Selected fiscal year must remain the requested {state.requested_fiscal_year}.")
    resources = {r.id for r in project.resources()}
    if not state.progress.year_evidence:
        blockers.append("Record adoption evidence for the selected fiscal year with app_status.")
    for evidence in state.progress.year_evidence:
        if evidence.resource not in resources:
            blockers.append(f"Adoption evidence references unknown resource {evidence.resource}.")
    reports, formulas = [], []
    coverage = {"scope": state.progress.scope, "coverage": state.progress.coverage,
                "unmodeled": state.progress.unmodeled, "fiscal_year": year,
                "parcels": 0, "modeled_parcels": 0, "districts": 0}
    if integrity["ok"]:
        districts, rolls = project.districts(), project.rolls()
        coverage["districts"] = len(districts)
        if not districts:
            blockers.append("No supported districts are modeled; an empty map is not a completed app.")
        try:
            for district in districts:
                if district.notes:
                    project.path(Path("model/notes") / district.notes, must_exist=True)
            for note in (project.model_dir / "notes").glob("*.md"):
                project.path(note, must_exist=True)
            project.path("gis/districts/outlines.geojson")
        except ProjectError as error:
            blockers.append(f"Public model paths: {error}")
        for entity in [*districts, *rolls, *project.formulas()]:
            if not entity.evidence:
                blockers.append(f"Modeled entity {entity.id} needs supporting evidence.")
            if entity.fiscal_year and entity.fiscal_year != year:
                blockers.append(f"Modeled entity {entity.id} uses {entity.fiscal_year}, not selected year {year}.")
        try:
            layer = project.parcel_layer_name()
            project.path(project.layer_path(layer))
            features = load_layer(project, layer)["features"]
            bounds = [geometry_bounds(f.get("geometry")) for f in features]
            if not any(bounds):
                raise ProjectError("No usable parcel geometry to map")
            if not all(bounds):
                caveats.append(f"{sum(b is None for b in bounds)} background parcels have no mappable geometry.")
            context = assess.AssessmentContext(project)
        except (ProjectError, OSError, ValueError) as error:
            blockers.append(f"Parcel/model context: {error}")
        else:
            coverage["parcels"] = len(context.parcels)
            counts = Counter(normalize_apn((f.get("properties") or {}).get(context.apn_field),
                                           context.mode) for f in features)
            if "" in counts or any(n > 1 for n in counts.values()):
                blockers.append("Parcel APNs are blank or collide after normalization; resolve the join identity.")
            supported = set().union(*(context.members.get(d.id, set()) for d in districts))
            coverage["modeled_parcels"] = len(supported & context.parcels.keys())
            if not coverage["modeled_parcels"]:
                blockers.append("No modeled district members join to the parcel layer.")
            for roll in rolls:
                summary = assess.roll_summary(project, roll.id)
                rows = project.roll_rows(roll)
                unmatched = [row.get(roll.apn_column) for row in rows
                             if normalize_apn(row.get(roll.apn_column), context.mode) not in context.parcels]
                summary["join"] = {"matched": len(rows) - len(unmatched), "rows": len(rows),
                                   "unmatched_count": len(unmatched), "unmatched": unmatched[:50]}
                reports.append(summary)
                if summary["blank_apns"] or summary["duplicate_apn_count"] or summary["unparsed_amounts"]:
                    blockers.append(f"Roll {roll.id} has blank/duplicate APNs or unparsed amounts; resolve before export.")
                for result in summary["checks"]:
                    if not result["ok"]:
                        caveats.append(f"Roll {roll.id} {result['check']}: source states {result['stated']}; "
                                       f"rows compute {result['computed']}. Source values preserved.")
                if not summary["checks"]:
                    caveats.append(f"Roll {roll.id} has no stated total/count for independent reconciliation.")
                if unmatched:
                    caveats.append(f"Roll {roll.id}: {len(unmatched)} of {len(rows)} rows do not join to parcels.")
            for district in districts:
                own_rolls = [r for r in rolls if r.district == district.id]
                seen = set()
                for roll in own_rolls:
                    apns = set(context.roll_rows[roll.id])
                    if seen & apns:
                        blockers.append(f"District {district.id} has overlapping rolls; select a single supported "
                                        "charge per parcel before export.")
                    seen.update(apns)
            for formula in project.formulas():
                applicable = [r for r in rolls if r.district == formula.district]
                for roll_id in [r.id for r in applicable] or [None]:
                    result = assess.validate_formula(project, formula.id, roll_id, context=context)
                    formulas.append(result)
                    if result["status"] != "pass":
                        caveats.append(f"Calculator {formula.id}, roll {roll_id or 'unavailable'}: "
                                       f"{result['status']}; matches {result.get('match', 0)} of "
                                       f"{result.get('rows', 0)} rows. See formula validation report.")
                caveats.extend(f"Calculator {formula.id}: {text}" for text in formula.caveats)
            for district in districts:
                district_rolls = [r for r in reports if r["district"] == district.id]
                if district_rolls:
                    for label, stated, computed in [
                        ("total", district.total, round(sum(r["total"] for r in district_rolls), 2)),
                        ("parcel count", district.parcel_count, len(context.members.get(district.id, ()))),
                    ]:
                        if stated is not None and abs(stated - computed) >= 0.005:
                            caveats.append(f"District {district.id} {label}: source states {stated}; "
                                           f"model computes {computed}. Source values preserved.")
                if not district.formulas:
                    caveats.append(f"District {district.id}: no independently supported calculator is linked.")
    return AppReadiness(
        status="blocked" if blockers else (
            "ready_with_caveats" if caveats or integrity["warnings"] else "ready"),
        integrity=integrity, blockers=blockers, caveats=caveats,
        rolls=reports, formulas=formulas, coverage=coverage,
    )


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _export_manifest(root: Path) -> dict[str, str]:
    """Hash actual exported bytes, except the two generated build timestamps."""
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if name in {"data/config.json", "data/manifest.json"}:
            data = read_json(path)
            data.pop("built_at", None)
            files[name] = _digest(data)
        else:
            files[name] = sha256_file(path)
    return files


def _coverage_page(report: AppReadiness) -> dict:
    coverage = report.coverage
    lines = [DISCLAIMER, f"Fiscal year: {coverage['fiscal_year']}.",
             f"Scope: {coverage['scope']}. Coverage designation: {coverage['coverage']}.",
             f"{coverage['districts']} modeled districts; {coverage['modeled_parcels']} modeled parcel joins "
             f"within {coverage['parcels']} background parcels.",
             *report.caveats]
    if report.integrity["warnings"]:
        lines.append(f"{len(report.integrity['warnings'])} additional integrity warnings are recorded in app.yaml.")
    return {"id": "taxmap-app-coverage", "title": "Coverage and known limitations",
            "html": "<h1>Coverage and known limitations</h1>" +
                    "".join(f"<p>{html.escape(line)}</p>" for line in lines)}


def _build_export(project: Project, output: Path, report: AppReadiness) -> dict:
    result = build_site(project, project.rel(output))
    config_path = output / "data" / "config.json"
    config = read_json(config_path)
    config["disclaimer"] = (config.get("disclaimer") or "") + " " + DISCLAIMER
    if report.coverage["coverage"] == "partial":
        config["disclaimer"] = "Partial assessment coverage. " + config["disclaimer"]
    coverage = _coverage_page(report)
    coverage["id"] = unique_id(coverage["id"], {page["id"] for page in config["pages"]})
    config["pages"].append(coverage)
    write_json(config_path, config)
    return result


def _proposal(project: Project, site: SiteConfig, output: Path) -> tuple[AppReview, AppReadiness, dict]:
    candidate = _fresh(project)
    candidate.config.site = site
    if site.publish_fields is None:
        raise ProjectError("Guided apps require an explicit publish_fields allow-list, including the parcel APN.")
    report = check(project, site)
    if report.blockers:
        raise ProjectError("App is blocked: " + "; ".join(report.blockers))
    built = _build_export(candidate, output, report)
    approved_urls = {r.origin_url for r in candidate.resources() if r.id in site.publish_resources}
    withheld_urls = {r.origin_url for r in candidate.resources()
                     if r.origin_url and r.origin_url not in approved_urls}
    for path in (output / "data").rglob("*.json"):
        text = html.unescape(path.read_text(encoding="utf-8"))
        if any(url in text for url in withheld_urls):
            raise ProjectError("Public prose contains a withheld resource URL; remove the inline link "
                               "or request approval for that exact resource.")
    fields = read_json(output / "data" / "manifest.json")["fields"]
    data = load_layer(candidate, candidate.parcel_layer_name())["features"]
    samples = {field: [] for field in fields}
    for feature in data:
        for field in fields:
            value = (feature.get("properties") or {}).get(field)
            if value is not None and value not in samples[field] and len(samples[field]) < 3:
                samples[field].append(value)
    config = read_json(output / "data" / "config.json")
    manifest = {
        "files": _export_manifest(output), "parcel_fields": fields, "samples": samples,
        "resources": read_json(output / "data" / "sources.json")["resources"],
        "roll_fields": site.publish_roll_fields, "copy_documents": site.copy_documents,
        "public_config": {key: value for key, value in config.items() if key != "built_at"},
        "public_model_files": ["data/districts.json", "data/formulas.json", "data/sources.json"],
        "coverage": report.coverage, "caveats": report.caveats,
        "warnings": built["warnings"],
        "notice": "Review all public model prose in public_model_files, including inventory rows, formula labels "
                  "and roll notes. Withheld resources must not be reintroduced through prose or inline links. "
                  "Approval permits local preview only, not upload, commit or deployment.",
    }
    # Warnings are handoff details, not export content; permission covers exported content and policy.
    digest = _digest({"files": manifest["files"], "site": site.model_dump(mode="json")})
    return AppReview(digest=digest, site=site, manifest=manifest), report, built


def review(project: Project, site: dict | None = None, approve: str | None = None) -> dict:
    """Prepare exact content privately; approve a digest only after the user's review."""
    project = _fresh(project)
    state = _state(project)
    if approve is not None and site is not None:
        raise ProjectError("Approve an existing review digest, or propose settings; do not combine them.")
    if approve is not None:
        if state.review is None or state.review.digest != approve:
            raise ProjectError("Unknown review digest; prepare and review the current export first.")
        settings = state.review.site
    else:
        settings = SiteConfig.model_validate(project.config.site.model_dump(mode="json") | (site or {}))
    review_root = project.path(".taxmap/app-reviews")
    review_root.mkdir(parents=True, exist_ok=True)
    # Fresh directories prevent stale files/document copies from entering an approved export.
    with tempfile.TemporaryDirectory(dir=review_root) as temporary:
        proposed, report, _ = _proposal(project, settings, Path(temporary))
        if approve is not None and proposed.digest != approve:
            raise ProjectError("Export content changed since review; request approval for the updated review.")
        previous = state.review.manifest["files"] if state.review else {}
        current = proposed.manifest["files"]
        changed = [p for p in sorted(set(previous) | set(current)) if previous.get(p) != current.get(p)]
        state.review = proposed
        if approve is not None:
            project.config.site = settings
            project.save_config()
            state.approved_digest = approve
        state.last_report = report
        _save(project, state)
        write_json(project.path("derived/app-review.json"), proposed.manifest, indent=2)
        # Retain the reviewed public model files for exact prose inspection without starting a server.
        for name in proposed.manifest["public_model_files"]:
            target = project.path(Path("derived/app-review") / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(temporary) / name, target)
    return {"digest": proposed.digest, "approved": state.approved_digest == proposed.digest,
            "manifest": proposed.manifest, "changed_files": changed,
            "review_file": "derived/app-review.json",
            "public_model_root": "derived/app-review",
            "next_action": "Present exact content to the user; call app_review(approve=digest) only after approval. "
                           "For a denied subset, propose reduced settings and review the resulting export."}


def status(project: Project, progress: dict | None = None) -> dict:
    project = _fresh(project)
    state = _state(project)
    if progress is not None:
        state.progress = AppProgress.model_validate(state.progress.model_dump(mode="json") | progress)
        for relative in state.progress.artifacts:
            project.path(relative, must_exist=True)
        _save(project, state)
    report = check(project)
    approval_current = False
    if not report.blockers and state.approved_digest:
        project.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=project.cache_dir) as temporary:
                proposed, _, _ = _proposal(project, project.config.site, Path(temporary))
                approval_current = state.approved_digest == proposed.digest
        except ProjectError as error:
            report.blockers.append(f"Export configuration: {error}")
            report.status = "blocked"
    if not report.blockers and not approval_current:
        report.status = "needs_approval"
    state.last_report = report
    _save(project, state)
    return {"root": str(project.root), **report.model_dump(mode="json"),
            "approval_current": approval_current,
            "requested_fiscal_year": state.requested_fiscal_year,
            "progress": state.progress.model_dump(mode="json"),
            "last_preview": state.last_preview.model_dump(mode="json") if state.last_preview else None,
            "next_action": "Resolve blockers" if report.blockers else (
                "Review exact export content with app_review" if not approval_current else "Run app_preview")}


class Preview:
    """One explicitly owned, loopback-only preview within the MCP process."""

    def __init__(self):
        self.server = None
        self.root: Path | None = None
        self.output: Path | None = None

    def stop(self, project: Project) -> dict:
        if self.server is not None:
            if self.root != project.root:
                raise ProjectError("A different project's preview is running; open that project before stopping it.")
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.root = None
            self.output = None
        return {"stopped": True}

    def start(self, project: Project, port: int = 0) -> dict:
        project = _fresh(project)
        state = _state(project)
        if self.server is not None and self.root != project.root:
            raise ProjectError("A different project's guided preview is running; stop it explicitly or use another MCP session.")
        if not 0 <= port <= 65535:
            raise ProjectError("Port must be between 0 (automatically assigned) and 65535")
        if not state.approved_digest:
            raise ProjectError("Content approval is required; prepare and approve app_review first.")
        preview_root = project.path(".taxmap/app-previews")
        preview_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=preview_root) as temporary:
            output = Path(temporary)
            proposed, report, built = _proposal(project, project.config.site, output)
            if proposed.digest != state.approved_digest:
                raise ProjectError("Export content changed; review and approve it with app_review before previewing.")
            # Copy only the freshly reviewed export; never serve the project root or old site extras.
            target = project.path(preview_root / proposed.digest)
            if target.exists():
                if _export_manifest(target) != proposed.manifest["files"]:
                    raise ProjectError(f"Existing preview files were edited; preserve and inspect {target} before retrying.")
            else:
                shutil.copytree(output, target)
        if self.server is not None and self.output != target:
            self.stop(project)
        if self.server is None:
            server = serve_site(project, project.rel(target), port, host="127.0.0.1")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.server, self.root, self.output = server, project.root, target
        url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request("GET", "/data/config.json")
            response = connection.getresponse()
            if response.status != 200:
                raise ProjectError(f"Preview HTTP status is {response.status}, not 200")
            actual = json.loads(response.read())
            if (actual["fiscal_year"] != project.config.fiscal_year
                    or actual["jurisdiction"] != project.config.jurisdiction.dump()):
                raise ProjectError("Preview health check returned another project's content")
        except (HTTPException, OSError, ValueError, KeyError) as error:
            self.stop(project)
            raise ProjectError(f"Local preview health check failed: {error}") from error
        finally:
            connection.close()
        command = [sys.executable, "-m", "taxmap.cli", "--project", str(project.root),
                   "serve", "--output", project.rel(target), "--port", "0"]
        restart = shlex.join(command)
        if sys.platform == "win32":
            restart = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in command)
        state.last_report = report
        state.last_preview = AppPreviewHandoff(
            url=url, restart_command=restart, site=project.rel(target),
            stop="Call app_preview(stop=true) for this project; Ctrl+C stops a standalone restart.",
            lifetime="The MCP process owns this preview; ending it stops the server.",
        )
        _save(project, state)
        return {"url": url, "status": report.status, "coverage": report.coverage,
                "caveats": report.caveats, "warnings": built["warnings"], "restart_command": restart,
                "restart_argv": command,
                "site": project.rel(target),
                "stop": "Call app_preview(stop=true) for this project; Ctrl+C stops a standalone restart.",
                "lifetime": "The MCP process owns this preview; ending it stops the server."}
