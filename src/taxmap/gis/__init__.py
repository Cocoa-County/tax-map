"""GeoJSON layer loading, inspection, querying, and import."""

from __future__ import annotations

import json
import math
import re
import shutil
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from taxmap.domain.models import LayerConfig
from taxmap.project import Project, ProjectError
from taxmap.util import normalize_apn, read_json

APN_FIELD_PATTERN = re.compile(r"^(apn|parcel_?(no|num|number|id)?|pin|ain|apn_?\w*)$", re.I)
ADDRESS_FIELD_PATTERN = re.compile(r"(full_?address|site_?addr|situs|address)", re.I)


@lru_cache(maxsize=8)
def _load(path: str, mtime_ns: int) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ProjectError(f"{path} is not a GeoJSON FeatureCollection")
    return data


def load_layer(project: Project, name: str) -> dict:
    path = project.layer_path(name)
    if not path.exists():
        raise ProjectError(f"Layer {name!r} has no data at {project.rel(path)}; download or import it first")
    return _load(str(path), path.stat().st_mtime_ns)


def layer_config(project: Project, name: str) -> LayerConfig:
    return project.config.layers.get(name) or LayerConfig()


def detect_field(features: list[dict], pattern: re.Pattern) -> str | None:
    if not features:
        return None
    for key in (features[0].get("properties") or {}):
        if pattern.search(key):
            return key
    return None


def apn_field(project: Project, name: str) -> str:
    config = layer_config(project, name)
    if config.apn_field:
        return config.apn_field
    field = detect_field(load_layer(project, name)["features"], APN_FIELD_PATTERN)
    if not field:
        raise ProjectError(f"Layer {name!r} has no apn_field configured and none was detected")
    return field


def geometry_bounds(geometry: dict | None) -> list[float] | None:
    if not geometry:
        return None
    xs, ys = [], []

    def visit(value):
        if value and isinstance(value[0], (int, float)):
            x, y = value[0], value[1]
            if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
                raise ProjectError("Coordinates must be finite WGS84 longitude/latitude")
            xs.append(x)
            ys.append(y)
        else:
            for child in value or []:
                visit(child)

    if geometry.get("type") == "GeometryCollection":
        for child in geometry.get("geometries", []):
            bounds = geometry_bounds(child)
            if bounds:
                xs += [bounds[0], bounds[2]]
                ys += [bounds[1], bounds[3]]
    else:
        visit(geometry.get("coordinates"))
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def combined_bounds(bounds: list[list[float]]) -> list[float] | None:
    bounds = [b for b in bounds if b]
    if not bounds:
        return None
    return [min(b[0] for b in bounds), min(b[1] for b in bounds),
            max(b[2] for b in bounds), max(b[3] for b in bounds)]


def jurisdiction_boundary(project: Project) -> tuple[dict, list[float]] | None:
    name = project.config.site.jurisdiction_boundary
    if name is None:
        return None
    config = project.config.layers.get(name)
    if config is None or config.kind != "boundary":
        raise ProjectError(f"site.jurisdiction_boundary: {name!r} must name a configured boundary layer")
    from shapely.errors import GEOSException
    from shapely.geometry import shape

    features, bounds = [], []
    for position, feature in enumerate(load_layer(project, name)["features"], start=1):
        geometry = feature.get("geometry")
        if not geometry or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ProjectError(f"Boundary {name!r} feature {position}: expected Polygon or MultiPolygon")
        geometry = {"type": geometry["type"], "coordinates": geometry.get("coordinates")}
        try:
            extent = geometry_bounds(geometry)
            polygon = shape(geometry)
        except (KeyError, TypeError, ValueError, IndexError, GEOSException) as error:
            raise ProjectError(f"Boundary {name!r} feature {position}: invalid geometry ({error})") from error
        if extent is None or polygon.is_empty or not polygon.is_valid:
            raise ProjectError(f"Boundary {name!r} feature {position}: empty or invalid polygon")
        bounds.append(extent)
        features.append({"type": "Feature", "geometry": geometry, "properties": {}})
    extent = combined_bounds(bounds)
    if extent is None:
        raise ProjectError(f"Boundary {name!r} has no polygon features")
    return {"type": "FeatureCollection", "features": features}, extent


def layer_info(project: Project, name: str, sample_values: int = 4) -> dict:
    data = load_layer(project, name)
    features = data["features"]
    counts: Counter = Counter()
    samples: dict[str, list] = {}
    types: Counter = Counter()
    for feature in features:
        geometry = feature.get("geometry")
        types[geometry.get("type") if geometry else None] += 1
        for key, value in (feature.get("properties") or {}).items():
            if value not in (None, ""):
                counts[key] += 1
                bucket = samples.setdefault(key, [])
                if len(bucket) < sample_values and value not in bucket:
                    bucket.append(value)
            else:
                counts.setdefault(key, 0)
    bounds = combined_bounds([geometry_bounds(f.get("geometry")) for f in features[:200000]])
    config = layer_config(project, name)
    return {
        "layer": name, "path": project.rel(project.layer_path(name)), "kind": config.kind,
        "feature_count": len(features), "geometry_types": {str(k): v for k, v in types.items()},
        "bounds": bounds, "apn_field": config.apn_field or detect_field(features, APN_FIELD_PATTERN),
        "address_field": config.address_field or detect_field(features, ADDRESS_FIELD_PATTERN),
        "fields": [{"name": k, "filled": counts[k], "fill_rate": round(counts[k] / max(1, len(features)), 4),
                    "samples": samples.get(k, [])} for k in counts],
    }


def _matches(properties: dict, where: dict[str, Any]) -> bool:
    for key, expected in where.items():
        actual = properties.get(key)
        if isinstance(expected, list):
            if str(actual) not in {str(e) for e in expected}:
                return False
        elif str(actual) != str(expected):
            return False
    return True


def layer_query(project: Project, name: str, *, apn: str | None = None, where: dict | None = None,
                text: str | None = None, point: tuple[float, float] | None = None,
                fields: list[str] | None = None, limit: int = 20, include_geometry: bool = False) -> dict:
    """Find features by APN, exact attribute values, address/text substring, or containing point."""
    data = load_layer(project, name)
    features = data["features"]
    wanted_apn = normalize_apn(apn, project.config.apn.normalize) if apn else None
    key = apn_field(project, name) if apn else None
    needle = text.lower() if text else None
    contains = None
    if point is not None:
        from shapely.geometry import Point, shape

        target = Point(point[0], point[1])

        def contains(feature):
            geometry = feature.get("geometry")
            bounds = geometry_bounds(geometry)
            if not bounds or not (bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]):
                return False
            return shape(geometry).covers(target)
    results, total = [], 0
    for feature in features:
        properties = feature.get("properties") or {}
        if wanted_apn and normalize_apn(properties.get(key), project.config.apn.normalize) != wanted_apn:
            continue
        if where and not _matches(properties, where):
            continue
        if needle and not any(needle in str(v).lower() for v in properties.values() if v is not None):
            continue
        if contains and not contains(feature):
            continue
        total += 1
        if len(results) < limit:
            props = {k: properties.get(k) for k in fields} if fields else properties
            item = {"properties": props, "bounds": geometry_bounds(feature.get("geometry"))}
            if include_geometry:
                item["geometry"] = feature.get("geometry")
            results.append(item)
    return {"layer": name, "matched": total, "returned": len(results), "features": results}


def import_layer(project: Project, name: str, source: str | Path, *, kind: str = "overlay",
                 apn_field_name: str | None = None, id_field: str | None = None,
                 address_field: str | None = None, title: str | None = None) -> dict:
    """Copy a WGS84 GeoJSON FeatureCollection into gis/<name>/ and register the layer."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ProjectError("Layer names must be lowercase letters, digits, '-' or '_'")
    source = Path(source).expanduser().resolve()
    data = read_json(source)
    if data.get("type") != "FeatureCollection":
        raise ProjectError("Expected a GeoJSON FeatureCollection")
    target = project.root / "gis" / name / "features.geojson"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target:
        shutil.copyfile(source, target)
    config = project.config.layers.get(name) or LayerConfig()
    update = {"kind": kind, "title": title or config.title or name}
    for field, value in (("apn_field", apn_field_name), ("id_field", id_field), ("address_field", address_field)):
        if value:
            update[field] = value
    project.config.layers[name] = config.model_copy(update=update)
    project.save_config()
    return layer_info(project, name)


def parcel_index(project: Project) -> tuple[str, dict[str, dict]]:
    """Map normalized APN -> parcel properties for the project's parcel layer."""
    name = project.parcel_layer_name()
    data = load_layer(project, name)
    field = apn_field(project, name)
    index: dict[str, dict] = {}
    for feature in data["features"]:
        properties = feature.get("properties") or {}
        apn = normalize_apn(properties.get(field), project.config.apn.normalize)
        if apn and apn not in index:
            index[apn] = properties
    return field, index
