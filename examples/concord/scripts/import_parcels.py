"""Build gis/parcels/features.geojson from a concord-lnl checkout (offline shortcut).

The normal route is ``taxmap gis download parcels`` (or the ``arcgis_download_layer``
MCP tool), which pages the City's ArcGIS layer configured in taxmap.yaml. This script
reuses the tiles and attributes already published by concord-lnl instead:

    python scripts/import_parcels.py path/to/concord-lnl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(clone: Path) -> None:
    data = clone / "public" / "data"
    if not (data / "tiles").is_dir():
        sys.exit(f"{data / 'tiles'} not found; pass the concord-lnl repository root")
    attributes: dict[str, dict] = {}
    for path in sorted((data / "attributes").glob("*.json")):
        attributes.update(json.loads(path.read_text(encoding="utf-8")))
    features, seen = [], set()
    for path in sorted((data / "tiles").glob("*.geojson")):
        for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
            object_id = str(feature.get("id", feature["properties"].get("OBJECTID")))
            if object_id in seen:
                continue
            seen.add(object_id)
            properties = attributes.get(object_id) or feature["properties"]
            features.append({"type": "Feature", "id": int(object_id), "properties": properties,
                             "geometry": feature["geometry"]})
    target = ROOT / "gis" / "parcels" / "features.geojson"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")),
                      encoding="utf-8")
    print(f"Wrote {len(features)} parcels to {target.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
