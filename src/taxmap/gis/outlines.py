"""Approximate district/zone outlines built from member parcels.

Generalized from concord-lnl's ``build_district_outlines.py``: merge member parcels in
a projected (meter) CRS, close gaps (buffer out then in), fill small holes, simplify,
and patch so every member parcel stays inside. These are *not* official boundary
diagrams and are labeled as approximate.
"""

from __future__ import annotations

import json

import numpy as np
from pyproj import Transformer
from shapely import STRtree, transform
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from taxmap.assess import AssessmentContext
from taxmap.gis import apn_field, load_layer
from taxmap.project import Project, ProjectError
from taxmap.util import normalize_apn, now_iso, write_json

SQ_M_PER_ACRE = 4046.8564224


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _reproject(geom, transformer):
    return transform(geom, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def _polygons(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


def _fill_small_holes(geom, max_area):
    out = []
    for polygon in _polygons(geom):
        if polygon.geom_type != "Polygon":
            continue
        keep = [h for h in polygon.interiors if Polygon(h).area >= max_area]
        out.append(Polygon(polygon.exterior, keep))
    return unary_union(out)


def outline(members, gap: float, tolerance: float, fill_holes: float):
    merged = unary_union(members)
    closed = merged.buffer(gap + tolerance, quad_segs=8).buffer(-gap, quad_segs=8)
    closed = _fill_small_holes(closed, fill_holes)
    result = closed.simplify(tolerance)
    missing = merged.difference(result)
    if missing.area > 0:
        result = result.union(missing.buffer(tolerance, quad_segs=2)).simplify(tolerance / 4)
    return merged, result


def build_outlines(project: Project, only: list[str] | None = None) -> dict:
    """Write gis/districts/outlines.geojson with one feature per district and zone."""
    context = AssessmentContext(project)
    layer = project.parcel_layer_name()
    data = load_layer(project, layer)
    field = apn_field(project, layer)
    mode = project.config.apn.normalize
    sample = next((f for f in data["features"] if f.get("geometry")), None)
    if sample is None:
        raise ProjectError("Parcel layer has no geometry")
    epsg = project.config.projected_crs
    if not epsg:
        centroid = shape(sample["geometry"]).centroid
        epsg = utm_epsg(centroid.x, centroid.y)
    to_m = Transformer.from_crs(4326, epsg, always_xy=True)
    to_ll = Transformer.from_crs(epsg, 4326, always_xy=True)
    parcels = {}
    for feature in data["features"]:
        apn = normalize_apn((feature.get("properties") or {}).get(field), mode)
        if not apn or not feature.get("geometry"):
            continue
        geom = _reproject(shape(feature["geometry"]), to_m).buffer(0)
        parcels[apn] = parcels[apn].union(geom) if apn in parcels else geom
    apns = list(parcels)
    tree = STRtree([parcels[a] for a in apns])

    jobs = []
    for district in context.districts:
        jobs.append((district.id, district, None, district.outline))
        for zone in district.zones:
            jobs.append((zone.id, district, zone, zone.outline))
    features, report = [], []
    for layer_id, district, zone, options in jobs:
        if only and layer_id not in only and district.id not in only:
            continue
        member_apns = context.members.get(layer_id, set())
        if not options.enabled or not member_apns:
            report.append({"id": layer_id, "skipped": "disabled" if not options.enabled else "no members"})
            continue
        missing = sorted(a for a in member_apns if a not in parcels)
        present = [parcels[a] for a in member_apns if a in parcels]
        if not present:
            report.append({"id": layer_id, "skipped": "no member geometry", "missing": missing[:10]})
            continue
        merged, geom = outline(present, options.gap_m, options.tolerance_m, options.fill_holes_m2)
        if not geom.buffer(0.01).covers(merged):
            raise ProjectError(f"{layer_id}: outline does not contain every member parcel")
        others = [apns[i] for i in tree.query(geom)
                  if apns[i] not in member_apns
                  and parcels[apns[i]].intersection(geom).area > 0.5 * parcels[apns[i]].area]
        props = {
            "id": layer_id, "district": district.id, "district_name": district.name,
            "zone": zone.id if zone else None, "zone_name": zone.name if zone else None,
            "fiscal_year": district.fiscal_year, "member_parcels": len(member_apns),
            "members_without_geometry": len(missing), "gap_distance_m": options.gap_m,
            "tolerance_m": options.tolerance_m, "area_acres": round(geom.area / SQ_M_PER_ACRE, 2),
            "member_parcel_acres": round(merged.area / SQ_M_PER_ACRE, 2), "parts": len(_polygons(geom)),
            "non_member_parcels_inside": len(others), "approximate": True,
        }
        geometry = json.loads(json.dumps(mapping(_reproject(geom, to_ll))),
                              parse_float=lambda s: round(float(s), 6))
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
        report.append({k: props[k] for k in ("id", "member_parcels", "parts", "area_acres",
                                             "non_member_parcels_inside", "members_without_geometry")})
    output = project.root / "gis" / "districts" / "outlines.geojson"
    write_json(output, {"type": "FeatureCollection", "features": features,
                        "properties": {"built_at": now_iso(), "projected_crs": f"EPSG:{epsg}",
                                       "note": "Approximate outlines from member parcels; not official diagrams."}})
    return {"path": project.rel(output), "projected_crs": epsg, "layers": report}
