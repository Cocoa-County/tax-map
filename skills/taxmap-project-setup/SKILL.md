---
name: taxmap-project-setup
description: Create or open a taxmap project folder and configure jurisdiction, fiscal year, APN format and the parcel layer. Use when starting a new tax map, assessment-district or special-tax transparency project, or when project_open fails.
---

# Set up a taxmap project

A project is a folder with `taxmap.yaml`. The folder is the source of truth;
`.taxmap/` is a rebuildable search cache. Everything else is plain YAML, CSV,
Markdown and GeoJSON, so commits and diffs show exactly what changed.

## Steps

1. **Open or create.** Call `project_open` with the folder. If it has no
   `taxmap.yaml`, call `project_init` with `path`, `name` (lowercase id),
   `jurisdiction` (e.g. "City of Concord"), `county`, `state`, and
   `fiscal_year` (e.g. "2026-27").
2. **Configure.** Call `project_configure` with top-level keys to replace.
   Nested objects are replaced as a whole, so send the full object:
   - `apn`: `{normalize: alnum, display_pattern: "###-###-###"}`. `alnum`
     (default) makes `112-138-003` and `112138003` match. Use `digits` or
     `none` only when letters or separators are significant.
   - `projected_crs`: a meter-based EPSG code for the area (e.g. 26910 for
     UTM zone 10N). If unset, a UTM zone is derived from the data.
   - `site`: `{title, subtitle, disclaimer, publish_fields}`. Use
     `publish_fields` as an allow-list. Leave out owner names, mailing
     addresses and other personal data unless the user decides otherwise.
3. **Parcel layer.** If you know the parcel service, run the
   `acquire-parcel-gis` skill now. Every map feature depends on it.
4. Call `project_info` and `project_validate` to confirm.

## Folder layout

```text
taxmap.yaml            project config
resources.yaml         registry of documents, web pages, chats, images, data
resources/<kind>/      stored resource files
derived/<id>/          extracted text, tables, page images (rebuildable)
model/sources.csv      research inventory (lead -> screened -> verified)
model/districts/*.yaml districts, zones, membership rules, evidence
model/formulas/*.yaml  assessment formulas with constants and lookups
model/rolls/*.csv|yaml assessment rolls (authoritative amounts)
model/notes/*.md       plain-language explainers (become site pages)
gis/<layer>/features.geojson
site/                  built static map (output)
```

## Rules

- Ids are lowercase `[a-z0-9_-]`. Don't rename an id after other files
  reference it.
- The MCP server does no reasoning. You read documents, decide, and record
  decisions through tools. Every number needs a citation.
- Ask the user before publishing personal data.
