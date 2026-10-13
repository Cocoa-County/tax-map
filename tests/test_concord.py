"""Acceptance tests against the Concord example (needs the parcel layer; see examples/concord/README.md)."""

import json
from pathlib import Path

import pytest

from taxmap import assess
from taxmap.project import Project
from taxmap.validate import validate_project

CONCORD = Path(__file__).resolve().parents[1] / "examples" / "concord"
HAS_PARCELS = (CONCORD / "gis" / "parcels" / "features.geojson").exists()


@pytest.fixture(scope="module")
def project():
    return Project(CONCORD)


def test_model_validates_without_parcels(project):
    report = validate_project(project, check_hashes=False)
    assert report["ok"], report["errors"]


def test_roll_totals(project):
    for roll in project.rolls():
        summary = assess.roll_summary(project, roll.id)
        assert all(check["ok"] for check in summary["checks"]), summary


def test_verified_county_bill_labels(project):
    expected = {
        "downtown": ("MH", "CONCORD DNTWN LMD"),
        "citywide-lighting": ("JC", "CONCORD STREET LTS"),
        "pine-hollow": ("MU", "PINE HOLLOW LMD"),
        "district-3": ("JV", "KIRKWOOD LMD"),
    }
    for district in project.districts():
        label, = district.bill_labels
        assert (label.code, label.label) == expected[district.id]
        assert label.fiscal_year == "2026-27" and label.evidence
        for citation in label.evidence:
            assert project.resource(citation.resource).origin_url.startswith("https://taxcolp.cccttc.us/")
        if district.id == "district-3":
            assert "Ygnacio Woods" in label.note and len(label.evidence) == 2


def test_committed_outlines_cover_every_district_and_zone(project):
    outlines = json.loads((CONCORD / "gis" / "districts" / "outlines.geojson").read_text(encoding="utf-8"))
    ids = {f["properties"]["id"] for f in outlines["features"]}
    for district in project.districts():
        if district.outline.enabled:
            assert district.id in ids
        for zone in district.zones:
            if zone.outline.enabled:
                assert zone.id in ids


needs_parcels = pytest.mark.skipif(not HAS_PARCELS, reason="Concord parcel layer not downloaded")


@needs_parcels
def test_roll_joins(project):
    for roll in project.rolls():
        join = assess.roll_join_parcels(project, roll.id)
        assert join["matched"] == join["rows"], join


@needs_parcels
def test_district_3_reproduces_roll(project):
    assert assess.validate_formula(project, "district-3")["status"] == "pass"


@needs_parcels
def test_pine_hollow_mostly_matches(project):
    result = assess.validate_formula(project, "pine-hollow")
    assert result["match"] >= 370 and result["rows"] == 401


@needs_parcels
def test_citywide_lighting_lookup(project):
    result = assess.evaluate_parcel(project, "citywide-lighting", "112101016")
    assert result["value"] == pytest.approx(62.5)
