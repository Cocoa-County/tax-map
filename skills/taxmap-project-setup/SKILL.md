---
name: taxmap-project-setup
description: Walk through taxmap project settings or open/configure an existing project when explicitly requested. Use for a setup walkthrough or project_open failure; for "build an app" intent use build-taxmap-app to derive defaults and continue automatically.
---

# Set up a taxmap project

A project is a folder with `taxmap.yaml`. The folder is the source of truth;
`.taxmap/` is a rebuildable search cache. Everything else is plain YAML, CSV,
Markdown and GeoJSON, so commits and diffs show exactly what changed.

## Steps

For a request to research or build an app, load `build-taxmap-app` instead of
this questionnaire. Preserve an explicit request for a guided walkthrough.

1. **Check the tools.** If an installed `TAXMAP-TOOLS.md` exists beside this
   skill, read its tools context and bundled `../../AGENTS.md`. Otherwise
   use the known repo tools location and its AGENTS.md. Verify live `project_init`/`project_open`
   tools; copying skills or saving configuration is not proof of connection.
   If missing, follow the repo's SETUP.md with permission, use client-supported
   activation and verify live readiness before creating a project. Continue
   in-session when ready; save resume steps if a new session/restart is required.
2. **Walk through the choices.** Ask one question at a time: jurisdiction
   and state, project name (lowercase id), exact location, then optional
   county/title/fiscal year, research focus, and starting document paths or
   official URLs. Preserve a requested fiscal year as the research target,
   even before its adopted records are verified; do not silently substitute
   the latest year. Leave year unset only when the user has no preference.
   References are
   unverified context, not facts or permission to upload documents.
   Show the resolved path and settings and obtain confirmation. The agent
   creates the directory; do not require the user to run a mkdir command.
3. **Open or create.** Inspect the chosen location before writes. Open an
   intended existing project with `project_open`; do not reinitialize it.
   For a new project, choose a new/empty location and call `project_init`
   with agreed `path`, `name`, `jurisdiction`, optional `title`, `county`,
   `state`, `fiscal_year`, `focus`, and `starting_resources`. Preserve
   existing files and never use the Concord example as a new city's project.
   Verify the returned root with `project_info`. New projects include
   PROJECT-PROMPT.md; read it when the user asks to proceed with research
   and a local preview. Older projects may lack it; do not reinitialize them.
4. **Configure.** Call `project_configure` with top-level keys to replace.
   Nested objects are replaced as a whole, so send the full object:
   - `apn`: `{normalize: alnum, display_pattern: "###-###-###"}`. `alnum`
     (default) removes punctuation and uppercases letters, making
     `112-138-003` and `112138003` match while preserving letter identifiers.
     `digits` removes letters and separators; use it only when both are
     insignificant. `none` preserves letters and separators (trimming outer
     whitespace). Choose from actual parcel and roll identifiers and check
     for normalization collisions before joining. Do not assume the example
     display pattern fits the jurisdiction.
   - `projected_crs`: a meter-based EPSG code for the area (e.g. 26910 for
     UTM zone 10N). If unset, a UTM zone is derived from the data.
   - `site`: `{title, subtitle, disclaimer, publish_fields, publish_resources}`.
     Use `publish_fields` as a strict allow-list including the parcel APN
     field (required for the public map). Leave out owner names, mailing
     addresses and other personal data unless the user decides otherwise.
     `publish_resources` defaults to `[]`; ask before adding approved IDs.
     Only approved resources' metadata, citations and linked inventory rows
     are exported. Approve extra roll columns with `publish_roll_fields`
     keyed by roll ID; assessment amounts and APN joins are always public.
5. **Parcel layer.** When the user asks to continue building and you know
   the parcel service, run `acquire-parcel-gis`. Every map feature depends on it.
6. Call `project_info` and `project_validate` to confirm. Report empty-project
   validation gaps without inventing GIS or evidence. Project creation is not
   completion of research or permission to publish.

## Folder layout

```text
taxmap.yaml            project config
PROJECT-PROMPT.md      generated local research request (not a published note)
app.yaml              guided app intent, progress, approvals and handoff (when used)
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
