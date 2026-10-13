---
name: acquire-parcel-gis
description: Find and download the parcel polygons (and optional boundary or overlay layers) for a jurisdiction from public ArcGIS REST services or GeoJSON files, then identify the APN, address, land-use and area fields. Use before building districts, outlines or the map site.
---

# Acquire parcel GIS

## 1. Find a service

Look for the city or county GIS portal, an open-data hub, or the ArcGIS REST
directory (`.../arcgis/rest/services`). Good signs: a layer named
"Parcels" or "Assessor Parcels", polygon geometry, and an APN field.

Call `arcgis_describe` on the service or layer URL. Check:

- `geometryType` is polygon.
- The record count is plausible for the jurisdiction.
- There is an APN field, plus use-code, units, area and address fields.
- `maxRecordCount` (the download pages automatically).

Record the service as a source (`source_upsert`, category `GIS`).

## 2. Download

`arcgis_download_layer` with `name: parcels`, `url`, `kind: parcels`, and
optionally a `where` clause to limit to the jurisdiction. It saves
`gis/parcels/features.geojson` in WGS84 and registers the layer. Downloads
are paged, rate-limited and resumable; re-run the call after an interruption.

For a local file use `layer_import` instead. It must be WGS84 GeoJSON;
convert shapefiles first.

## 3. Configure fields

1. Run `layer_info` and read field fill rates and samples.
2. Call `layer_configure` with `apn_field`, `id_field` (a stable unique id
   such as OBJECTID), `address_field`, and `label_fields`.
3. Run `layer_query` with a known APN and address to confirm.
4. After rolls exist, run `roll_join_parcels`. A low match rate usually
   means the wrong APN field or `apn.normalize` mode.

## Notes

- Parcel layers usually include condominium stacks (identical polygons).
  This is expected.
- Respect service terms. Don't hammer servers; the default delay is 1 s
  between pages.
- Large layers (>100 MB) should be gitignored; document how to rebuild them.
