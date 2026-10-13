"""Read-only, rate-limited, resumable ArcGIS REST feature-layer downloader.

Generalized from concord-lnl's ``download_parcels.py``: census all object IDs, fetch
every field in bounded batches as WGS84 GeoJSON, verify IDs and field coverage, cache
batches for resume, recheck the census, and publish exports with a summary written last.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from taxmap.domain.models import ArcGISSource, LayerConfig
from taxmap.project import Project, ProjectError
from taxmap.util import now_iso, read_json, sha256_file, write_json

LOG = logging.getLogger(__name__)
USER_AGENT = "taxmap/0.1 ArcGIS downloader (public records; read-only)"


class Client:
    """Sequential client with a minimum interval between request starts and bounded retries."""

    def __init__(self, delay: float = 1.0, attempts: int = 5):
        self.delay = max(0.0, delay)
        self.attempts = attempts
        self.last_request = 0.0

    def request(self, url: str, params: dict | None = None) -> dict:
        if urlparse(url).scheme not in {"http", "https"}:
            raise ProjectError("Only http(s) ArcGIS URLs are supported")
        for attempt in range(self.attempts):
            time.sleep(max(0.0, self.last_request + self.delay - time.monotonic()))
            self.last_request = time.monotonic()
            request = Request(
                url if params is not None else url + ("&" if "?" in url else "?") + "f=json",
                data=urlencode(params).encode() if params is not None else None,
                headers={"User-Agent": USER_AGENT},
            )
            try:
                with urlopen(request, timeout=120) as response:
                    result = json.load(response)
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.attempts - 1:
                    raise
                retry_after = error.headers.get("Retry-After", "")
                pause = max(2 ** (attempt + 1), float(retry_after) if retry_after.isdigit() else 0)
                LOG.warning("HTTP %s; retrying in %ss", error.code, pause)
                time.sleep(pause)
                continue
            except (URLError, TimeoutError) as error:
                if attempt == self.attempts - 1:
                    raise
                pause = 2 ** (attempt + 1)
                LOG.warning("Network failure %s; retrying in %ss", error, pause)
                time.sleep(pause)
                continue
            if not isinstance(result, dict):
                raise ProjectError("ArcGIS response is not a JSON object")
            if "error" in result:
                raise ProjectError(f"ArcGIS query failed: {result['error']}")
            return result
        raise ProjectError("Request attempts exhausted")


def describe(url: str, client: Client | None = None) -> dict:
    """Summarize a layer (or list a service's layers) without downloading features."""
    client = client or Client(delay=0.5)
    metadata = client.request(url.rstrip("/"))
    if "fields" not in metadata:
        layers = [{"id": l.get("id"), "name": l.get("name"), "geometryType": l.get("geometryType")}
                  for l in metadata.get("layers", [])]
        return {"url": url, "service": True, "layers": layers,
                "hint": "Append /<layer id> to the URL to describe a layer"}
    count = None
    try:
        count = client.request(url.rstrip("/") + "/query",
                               {"f": "json", "where": "1=1", "returnCountOnly": "true"}).get("count")
    except Exception as error:  # count is helpful but optional
        LOG.warning("Count failed: %s", error)
    return {
        "url": url, "name": metadata.get("name"), "type": metadata.get("type"),
        "geometryType": metadata.get("geometryType"), "feature_count": count,
        "maxRecordCount": metadata.get("maxRecordCount"),
        "spatialReference": (metadata.get("extent") or {}).get("spatialReference"),
        "objectIdField": metadata.get("objectIdField"),
        "fields": [{"name": f["name"], "type": f.get("type"), "alias": f.get("alias")}
                   for f in metadata.get("fields", [])],
        "description": (metadata.get("description") or "")[:1000],
        "copyrightText": metadata.get("copyrightText"),
    }


def _validate_batch(response: dict, ids: list[int], oid_field: str, fields: list[str]) -> None:
    if response.get("exceededTransferLimit"):
        raise ProjectError("ArcGIS truncated a batch; use a smaller batch_size")
    if response.get("type") != "FeatureCollection" or not isinstance(response.get("features"), list):
        raise ProjectError("ArcGIS did not return a GeoJSON FeatureCollection")
    actual = []
    for feature in response["features"]:
        properties = feature.get("properties")
        if not isinstance(properties, dict) or set(properties) != set(fields):
            raise ProjectError("Feature has missing or unexpected attributes")
        actual.append(properties[oid_field])
    if len(actual) != len(ids) or set(actual) != set(ids):
        raise ProjectError("Batch object IDs do not match the requested census")


def download_layer(project: Project, name: str, source: ArcGISSource | None = None, *,
                   kind: str | None = None, client: Client | None = None,
                   max_features: int | None = None) -> dict:
    """Download an ArcGIS layer to gis/<name>/features.geojson (resumable)."""
    config = project.config.layers.get(name) or LayerConfig(kind=kind or "overlay")
    source = source or config.source
    if source is None:
        raise ProjectError(f"Layer {name!r} has no ArcGIS source; pass url")
    client = client or Client(source.delay)
    output = project.root / "gis" / name
    cache = output / "batches"
    cache.mkdir(parents=True, exist_ok=True)
    started = now_iso()
    url = source.url.rstrip("/")
    metadata = client.request(url)
    if "fields" not in metadata:
        raise ProjectError("URL is a service, not a layer; append /<layer id>")
    batch_size = max(1, min(source.batch_size, metadata.get("maxRecordCount") or source.batch_size))
    fields = [f["name"] for f in metadata["fields"] if f["type"] != "esriFieldTypeGeometry"]
    census = client.request(url + "/query", {"f": "json", "where": source.where, "returnIdsOnly": "true"})
    ids = census.get("objectIds")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise ProjectError("Invalid or empty object-ID census")
    ids.sort()
    if max_features:
        ids = ids[:max_features]
    oid_field = census["objectIdFieldName"]
    if oid_field not in fields:
        raise ProjectError("Census object-ID field not found in layer schema")
    (output / "summary.json").unlink(missing_ok=True)
    write_json(output / "layer_metadata.json", metadata)
    cache_paths = []
    for offset in range(0, len(ids), batch_size):
        wanted = ids[offset:offset + batch_size]
        path = cache / f"{offset:07d}.json"
        identity = {"layer": url, "where": source.where, "ids": wanted}
        saved = read_json(path) if path.exists() else None
        if saved and all(saved.get(k) == v for k, v in identity.items()):
            _validate_batch(saved["geojson"], wanted, oid_field, fields)
        else:
            params = {"f": "geojson", "objectIds": ",".join(map(str, wanted)), "outFields": "*",
                      "returnGeometry": "true", "outSR": 4326}
            geojson = client.request(url + "/query", params)
            _validate_batch(geojson, wanted, oid_field, fields)
            write_json(path, {**identity, "retrieved_at": now_iso(), "geojson": geojson})
        cache_paths.append(path)
        LOG.info("%s / %s features verified", min(offset + batch_size, len(ids)), len(ids))
    if not max_features:
        final = client.request(url + "/query", {"f": "json", "where": source.where, "returnIdsOnly": "true"})
        if sorted(final.get("objectIds") or []) != ids:
            raise ProjectError("Object-ID census changed during download; rerun to reconcile")
    null_ids, seen = [], set()
    geo_path, csv_path = output / "features.geojson", output / "attributes.csv"
    tmp_geo, tmp_csv = geo_path.with_suffix(".tmp"), csv_path.with_suffix(".tmp")
    with tmp_geo.open("w", encoding="utf-8") as geo, tmp_csv.open("w", encoding="utf-8", newline="") as table:
        geo.write('{"type":"FeatureCollection","features":[')
        writer = csv.DictWriter(table, fieldnames=fields)
        writer.writeheader()
        first = True
        for path in cache_paths:
            for feature in read_json(path)["geojson"]["features"]:
                oid = feature["properties"][oid_field]
                if oid in seen:
                    raise ProjectError(f"Duplicate object ID {oid}")
                seen.add(oid)
                if feature.get("geometry") is None:
                    null_ids.append(oid)
                if not first:
                    geo.write(",")
                json.dump(feature, geo, ensure_ascii=False, allow_nan=False)
                writer.writerow(feature["properties"])
                first = False
        geo.write("]}\n")
    tmp_geo.replace(geo_path)
    tmp_csv.replace(csv_path)
    summary = {
        "complete": not max_features, "layer_url": url, "where": source.where, "started_at": started,
        "completed_at": now_iso(), "feature_count": len(seen), "attribute_count": len(fields),
        "object_id_field": oid_field, "geojson_crs": "EPSG:4326", "null_geometry_object_ids": null_ids,
        "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in (geo_path, csv_path)},
        "notes": ["Public GIS data is a snapshot, not a legal survey or certified roll.",
                  "Cached batches keep their own retrieval times; this is not a transactional snapshot."],
    }
    if max_features:
        summary["notes"].append(f"Partial sample limited to {max_features} features.")
    write_json(output / "summary.json", summary, indent=2)
    update = {"source": source, "id_field": config.id_field or oid_field, "kind": kind or config.kind,
              "title": config.title or metadata.get("name")}
    project.config.layers[name] = config.model_copy(update=update)
    project.save_config()
    return summary
