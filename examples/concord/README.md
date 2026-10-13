# Example: City of Concord, California

This is a port of [concord-lnl](https://github.com/Spinnernicholas/concord-lnl),
the City of Concord's Landscaping & Lighting assessment districts for
FY 2026-27. It is the acceptance test for taxmap: four districts,
three rolls, formulas that reproduce or approximate the Engineer's Reports,
and a published map.

## Contents

| Path | What it is |
| --- | --- |
| `taxmap.yaml` | Jurisdiction, APN format, the parcel layer (City ArcGIS), and site settings |
| `resources.yaml` | Links to the Engineer's Reports and council minutes |
| `model/sources.csv` | How each source was screened |
| `model/districts/` | Pine Hollow, District 3 (Kirkwood and Ygnacio Woods zones), Downtown (four zones), Citywide Lighting |
| `model/formulas/` | One formula per district, with cited constants and lookups |
| `model/rolls/` | The adopted rolls transcribed from the reports, with stated totals |
| `model/notes/` | Notes on each district shown in the map |
| `gis/districts/outlines.geojson` | Approximate district and zone outlines (generated) |

Parcel geometry (`gis/parcels/`, about 120 MB) and the built site (`site/`)
are not committed.

## Rebuilding

From the repository root, after `pip install -e .`:

```powershell
cd examples/concord
taxmap gis download parcels      # pages the City's ArcGIS parcel layer
# or, offline from a concord-lnl checkout:
python scripts/import_parcels.py path\to\concord-lnl

taxmap validate
taxmap outlines                  # regenerate gis/districts/outlines.geojson
taxmap build
taxmap serve --port 8765         # open http://localhost:8765
```

## Validation results

| Check | Result |
| --- | --- |
| Roll joins to parcels | Pine Hollow 401/401, District 3 503/503, Downtown 767/767 |
| `taxmap check district-3` | 503/503 match the roll |
| `taxmap check pine-hollow` | 375/401 match. The report spreads cost by original floor area, and the formula uses the assessor's current living area as a stand-in. |
| `taxmap check downtown` | About 16% match. Frontage inputs default to 0, so map values are a lower bound unless the user enters frontage in the calculator. |
| Citywide Lighting | Evaluated from land-use code through the 55-row EDU table; there is no parcel roll in the report |
| Outlines | Parcel counts per district and zone match the concord-lnl reference |

The adopted roll is always the official amount. The calculators explain
the amount and let people explore "what if" inputs.
