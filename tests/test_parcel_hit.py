import json
import shutil
import subprocess
from pathlib import Path

import pytest

HIT_JS = Path(__file__).resolve().parents[1] / "src" / "taxmap" / "site" / "static" / "parcel-hit.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def ring(x, y, size):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]


def feature(fid, apn, rings, kind="Polygon"):
    return {"type": "Feature", "id": fid, "properties": {"apn": apn},
            "geometry": {"type": kind, "coordinates": rings}}


def run_hits(tmp_path, features, points):
    path = tmp_path / "hits.json"
    path.write_text(json.dumps({"features": features, "points": points}), encoding="utf-8")
    script = """
const hit = require(process.argv[1]);
const data = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const entries = data.features.map((f,i) => hit.prepare(f, `tile-${i}`));
const normalize = x => x == null ? '' : String(x).replace(/[^0-9A-Za-z]/g,'').toUpperCase();
console.log(JSON.stringify({
  areas: entries.map(e => e.area),
  representatives: entries.map(e => {
    const point = hit.representativePoint(e.feature.geometry);
    return {point, inside: point ? hit.contains(point, e.feature.geometry) : false};
  }),
  hits: data.points.map(p => hit.find(p, entries, normalize).map(e => e.feature.id))
}));
"""
    return json.loads(subprocess.run(["node", "-e", script, str(HIT_JS), str(path)],
                                    capture_output=True, text=True, encoding="utf-8", check=True).stdout)


def test_overlaps_sorted_smallest_first_across_tiles(tmp_path):
    features = [
        feature(1, "001", [ring(0, 0, 10)]),
        feature(3, "003", [ring(1, 1, 2)]),
        feature(2, "002", [ring(1, 1, 2)]),
        feature(4, "004", [ring(20, 20, 1)]),
    ]
    result = run_hits(tmp_path, features, [[2, 2], [5, 5], [30, 30]])
    assert result["hits"] == [[2, 3, 1], [1], []]
    assert result["areas"] == [100, 4, 4, 1]


def test_holes_multipolygons_and_boundaries(tmp_path):
    features = [
        feature(1, "001", [ring(0, 0, 10), ring(2, 2, 4)]),
        feature(2, "002", [[ring(20, 20, 2)], [ring(30, 30, 2)]], "MultiPolygon"),
    ]
    result = run_hits(tmp_path, features, [[1, 1], [3, 3], [0, 5], [2, 3], [21, 21], [31, 31], [25, 25]])
    assert result["hits"] == [[1], [], [1], [], [2], [2], []]
    assert result["areas"] == [84, 8]
    assert all(item["inside"] for item in result["representatives"])


def test_duplicate_apns_normalized_but_missing_apns_not_merged(tmp_path):
    features = [
        feature(1, "001-001", [ring(0, 0, 5)]),
        feature(2, "001001", [ring(1, 1, 2)]),
        feature(3, None, [ring(1, 1, 2)]),
        feature(4, "", [ring(1, 1, 2)]),
    ]
    assert run_hits(tmp_path, features, [[2, 2]])["hits"] == [[2, 3, 4]]


def test_small_real_world_footprints_keep_area_order(tmp_path):
    features = [
        feature(1, "001", [ring(-122.03, 37.97, 0.0005)]),
        feature(2, "002", [ring(-122.03, 37.97, 0.0001)]),
    ]
    result = run_hits(tmp_path, features, [[-122.02995, 37.97005]])
    assert result["hits"] == [[2, 1]]
    assert result["areas"][1] == pytest.approx(1e-8)


def test_null_or_unsupported_geometry_does_not_hit(tmp_path):
    missing = feature(1, "001", [])
    missing["geometry"] = None
    point = feature(2, "002", [1, 1], "Point")
    assert run_hits(tmp_path, [missing, point], [[1, 1]])["hits"] == [[]]


def test_representative_point_is_inside_concave_polygon(tmp_path):
    concave = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4], [0, 0]]
    result = run_hits(tmp_path, [feature(1, "001", [concave])], [[2, 2]])
    assert result["hits"] == [[]]
    assert result["representatives"][0]["inside"]
