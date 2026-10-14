"""Typed domain model for taxmap projects.

Everything here is serialized to human-reviewable YAML/CSV files inside a project
folder; the SQLite index is only a cache derived from these files.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True, exclude_defaults=True, by_alias=True)


# --------------------------------------------------------------------------- evidence

class Evidence(Model):
    """A pointer from a model value to the document location that supports it."""

    resource: str = Field(description="Resource id from resources.yaml")
    page: int | None = Field(None, description="1-based PDF page (not the printed page number)")
    locator: str | None = Field(None, description="Human locator, e.g. 'Part D, Table 2, printed p. 10'")
    quote: str | None = Field(None, description="Short verbatim quote supporting the value")
    note: str | None = None


# --------------------------------------------------------------------------- project config

class Jurisdiction(Model):
    name: str
    county: str | None = None
    state: str | None = None
    country: str = "US"
    agency: str | None = None


class ArcGISSource(Model):
    type: Literal["arcgis"] = "arcgis"
    url: str
    where: str = "1=1"
    batch_size: int = 1000
    delay: float = 1.0


class LayerConfig(Model):
    title: str | None = None
    kind: Literal["parcels", "overlay", "boundary"] = "overlay"
    path: str | None = Field(None, description="GeoJSON path relative to project; default gis/<name>/features.geojson")
    source: ArcGISSource | None = None
    id_field: str | None = None
    apn_field: str | None = None
    address_field: str | None = None
    label_fields: list[str] = Field(default_factory=list)


class ApnConfig(Model):
    normalize: Literal["alnum", "digits", "none"] = "alnum"
    display_pattern: str | None = Field(None, description="e.g. '###-###-###' for display formatting")


class ParcelLink(Model):
    label: str = Field(min_length=1)
    url_template: str = Field(description="HTTP(S) URL with {apn} (normalized) or {apn_display} placeholders")

    @field_validator("url_template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.netloc or "{" in parts.netloc or "}" in parts.netloc:
            raise ValueError("Parcel links must use an absolute HTTP(S) URL with a fixed host")
        stripped = re.sub(r"\{apn(?:_display)?\}", "", value)
        if "{" in stripped or "}" in stripped:
            raise ValueError("Parcel link placeholders must be {apn} or {apn_display}")
        if stripped == value:
            raise ValueError("Parcel links must include an APN placeholder")
        return value


class SiteConfig(Model):
    title: str | None = None
    subtitle: str | None = None
    jurisdiction_boundary: str | None = Field(None, description="Name of the boundary layer for the main jurisdiction outline and initial view")
    minimum_zoom: int = 15
    chunk_degrees: float = 0.005
    publish_fields: list[str] | None = Field(None, description="Allow-list of parcel attributes to publish (None = all)")
    hide_fields: list[str] = Field(default_factory=list, description="Deny-list applied after the allow-list")
    publish_resources: list[str] = Field(default_factory=list, description="Approved resource IDs for public metadata, citations and document copies (default: none)")
    publish_roll_fields: dict[str, list[str]] = Field(default_factory=dict, description="Approved extra columns by roll ID; assessment amounts and APN joins are always public")
    copy_documents: bool = Field(False, description="Copy only approved resources into the site for offline citations")
    disclaimer: str | None = None
    base_url: str | None = None
    parcel_links: list[ParcelLink] = Field(default_factory=list, description="External official parcel links")


class ProjectConfig(Model):
    name: str
    title: str | None = None
    description: str | None = None
    jurisdiction: Jurisdiction
    fiscal_year: str | None = None
    projected_crs: int | None = Field(None, description="EPSG code in meters for geometry ops (default: UTM from data)")
    apn: ApnConfig = Field(default_factory=ApnConfig)
    layers: dict[str, LayerConfig] = Field(default_factory=dict)
    site: SiteConfig = Field(default_factory=SiteConfig)
    version: int = 1


# --------------------------------------------------------------------------- guided app lifecycle

class AppProgress(Model):
    scope: str = "Initial inventory of supported adopted assessments"
    coverage: Literal["partial", "complete"] = "partial"
    unmodeled: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list, description="Public, reviewed research caveats")
    year_evidence: list[Evidence] = Field(default_factory=list, description="Evidence of adoption for the selected year")
    artifacts: list[str] = Field(default_factory=list, description="Project-relative research handoff paths")


class AppReadiness(Model):
    status: Literal["blocked", "needs_approval", "ready", "ready_with_caveats"]
    integrity: dict[str, Any]
    blockers: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    rolls: list[dict[str, Any]] = Field(default_factory=list)
    formulas: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)


class AppReview(Model):
    digest: str
    site: SiteConfig
    manifest: dict[str, Any]


class AppPreviewHandoff(Model):
    url: str
    site: str
    restart_command: str
    stop: str
    lifetime: str


class AppState(Model):
    version: Literal[1] = 1
    jurisdiction: Jurisdiction
    name: str
    requested_fiscal_year: str | None = None
    progress: AppProgress = Field(default_factory=AppProgress)
    review: AppReview | None = None
    approved_digest: str | None = None
    last_report: AppReadiness | None = None
    last_preview: AppPreviewHandoff | None = Field(None, description="Last handoff, not proof a server is still running")


# --------------------------------------------------------------------------- resources

ResourceKind = Literal["document", "web", "image", "chat", "data", "note", "other"]


class Resource(Model):
    id: str
    kind: ResourceKind
    path: str | None = Field(None, description="Path relative to project root; None for link-only resources")
    title: str
    origin_url: str | None = None
    media_type: str | None = None
    sha256: str | None = None
    size: int | None = None
    retrieved_at: str | None = None
    added_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    pages: int | None = None


# --------------------------------------------------------------------------- sources

SOURCE_STATUSES = ("lead", "screened", "verified", "rejected", "unreadable")
SOURCE_COLUMNS = [
    "id", "title", "jurisdiction", "district", "category", "year", "url", "resource",
    "source_type", "status", "notes", "finding", "evidence_location",
]


class Source(Model):
    """One row of the research inventory (model/sources.csv)."""

    id: str
    title: str
    jurisdiction: str | None = None
    district: str | None = None
    category: str | None = None
    year: str | None = None
    url: str | None = None
    resource: str | None = None
    source_type: str | None = None
    status: Literal["lead", "screened", "verified", "rejected", "unreadable"] = "lead"
    notes: str | None = None
    finding: str | None = None
    evidence_location: str | None = None


# --------------------------------------------------------------------------- districts and rolls

class Membership(Model):
    """How parcels are assigned to a district or zone.

    Exactly one approach should be used: an assessment roll (optionally filtered by
    column values), a parcel attribute match, or every parcel of the parent district.
    """

    roll: str | None = Field(None, description="Roll id; members are the roll's APNs")
    where: dict[str, str] = Field(default_factory=dict, description="Roll column filters, e.g. {zone: Kirkwood}")
    attribute: str | None = Field(None, description="Parcel attribute for attribute-based membership")
    values: list[str] = Field(default_factory=list, description="Accepted attribute values (string compare)")
    parent: bool = Field(False, description="Zone includes every parcel of its district")

    @model_validator(mode="after")
    def validate_rule(self) -> Membership:
        if self.where and not self.roll:
            raise ValueError("Membership.where requires an explicit roll ID; filters do not inherit a roll")
        if self.values and not self.attribute:
            raise ValueError("Membership.values requires an attribute")
        if sum([bool(self.roll), bool(self.attribute), self.parent]) != 1:
            raise ValueError("Membership requires exactly one of roll, attribute or parent")
        if self.attribute and not self.values:
            raise ValueError("Attribute membership requires non-empty values")
        return self


class OutlineOptions(Model):
    gap_m: float = 30
    tolerance_m: float = 1.5
    fill_holes_m2: float = 30000
    enabled: bool = True


class Zone(Model):
    id: str
    name: str
    color: str | None = None
    description: str | None = None
    membership: Membership | None = None
    outline: OutlineOptions = Field(default_factory=OutlineOptions)
    evidence: list[Evidence] = Field(default_factory=list)


class BillLabel(Model):
    """Verified county bill wording, not a parcel-level charge or membership rule."""

    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    fiscal_year: str | None = None
    note: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class District(Model):
    id: str
    name: str
    short_name: str | None = None
    fiscal_year: str | None = None
    agency: str | None = None
    color: str | None = None
    description: str | None = None
    membership: Membership | None = None
    bill_labels: list[BillLabel] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    formulas: list[str] = Field(default_factory=list)
    total: float | None = Field(None, description="Total levy stated by the source document")
    parcel_count: int | None = Field(None, description="Assessed parcel count stated by the source document")
    notes: str | None = Field(None, description="Path to a Markdown explainer, relative to model/notes")
    outline: OutlineOptions = Field(default_factory=OutlineOptions)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_membership(self) -> District:
        if self.membership and self.membership.parent:
            raise ValueError("Parent membership is only valid for zones, not districts")
        return self


class Roll(Model):
    """Metadata for an assessment roll stored as model/rolls/<id>.csv."""

    id: str
    district: str
    fiscal_year: str | None = None
    csv: str = Field(description="CSV path relative to model/rolls")
    apn_column: str = "apn"
    amount_column: str = "assessment"
    columns: dict[str, str] = Field(default_factory=dict, description="Column descriptions")
    stated_total: float | None = None
    stated_count: int | None = None
    notes: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


# --------------------------------------------------------------------------- formulas

class InputSource(Model):
    parcel: str | list[str] | None = Field(None, description="Parcel attribute name(s); first non-empty wins")
    roll: str | None = Field(None, description="Roll id to read a column from")
    column: str | None = Field(None, description="Roll column (with roll)")
    member: str | None = Field(None, description="District or zone id; value is 1 if the parcel is a member")


class FormulaInput(Model):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str
    description: str | None = None
    unit: str | None = None
    type: Literal["number", "text"] = "number"
    source: InputSource | None = Field(None, description="Where the value comes from; None = user-entered")
    default: float | str | None = None
    caveat: str | None = Field(None, description="Known limits of the data source for this input")


class Constant(Model):
    value: float
    label: str | None = None
    unit: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class LookupEntry(Model):
    keys: str | list[str | int] = Field(description="Keys such as '11, 12, 62-69' or a list")
    value: float
    label: str | None = None
    details: dict[str, float | str] = Field(default_factory=dict)


class Lookup(Model):
    label: str | None = None
    key_type: Literal["int", "str"] = "int"
    default: float | None = None
    entries: list[LookupEntry]
    evidence: list[Evidence] = Field(default_factory=list)


class Term(Model):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str | None = None
    expression: str
    description: str | None = None


class Formula(Model):
    id: str
    name: str
    district: str
    fiscal_year: str | None = None
    description: str | None = None
    inputs: list[FormulaInput] = Field(default_factory=list)
    constants: dict[str, Constant] = Field(default_factory=dict)
    lookups: dict[str, Lookup] = Field(default_factory=dict)
    terms: list[Term] = Field(default_factory=list, description="Named sub-expressions evaluated in order")
    expression: str = Field(description="Result expression; may reference inputs, constants and terms")
    applies_when: str | None = Field(None, description="Optional expression; formula is 0/not applicable when false")
    tolerance: float = Field(0.01, description="Allowed absolute difference when validating against a roll")
    caveats: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
