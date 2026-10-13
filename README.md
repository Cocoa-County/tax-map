# taxmap

Build transparent tax and special-assessment maps from public documents,
with AI agents doing the research.

Load a project with resources such as Engineer's Reports, resolutions,
assessment rolls, web pages, meeting chats, photos and GIS data. An agent
using the **taxmap MCP server** and **agent skills** turns them into:

- **A cited model:** districts, benefit zones, rates, lookup schedules and
  formulas. Every number points to the page it came from.
- **Verified rolls:** per-parcel amounts, checked against the document's
  stated totals and joined to parcels.
- **GIS layers:** parcels and approximate district outlines.
- **A static map site:** parcel search, per-parcel districts and amounts,
  calculator modals with typeset equations, live numeric substitutions,
  color-coded symbol explanations and calculation steps, district explainers and a
  source list. It needs no backend.

The map stays full-width until a parcel is selected. Parcel details can be
hidden and reopened without losing the selection. Calculators keep the
estimate visible while you edit, group public-data inputs and district
membership into expandable sections, and offer a reset for what-if scenarios.
Clicking overlapping parcel footprints (such as condominium units or shared
areas) opens a property chooser. Smaller footprints are selected first;
clicking the same location again cycles through the properties. Each selection
keeps its own APN, official assessments, calculator inputs, and shareable link.
Search results and APN links also show the chooser when other properties
overlap an interior point of the selected footprint.

Version 1 ships the MCP server, the skills, a CLI and the site generator.
The server is deterministic: it stores, extracts, searches, computes and
checks, while the agent reads, decides and writes.

## Concepts

| Concept | Where it lives |
|---|---|
| Project | A folder with `taxmap.yaml`; plain files are the source of truth |
| Resource | Any input: document, web page, chat, image, data, note (`resources.yaml`) |
| Source | Research-inventory row: lead → screened → verified (`model/sources.csv`) |
| District / zone | Membership rule plus evidence (`model/districts/*.yaml`) |
| Roll | Authoritative per-parcel amounts (`model/rolls/*.csv`) |
| Formula | Inputs, constants, lookups, terms and an expression (`model/formulas/*.yaml`) |
| Evidence | `{resource, page, locator, quote}` attached to any model value |
| Site | Static Leaflet app in `site/` |

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

On macOS or Linux use `.venv/bin/pip`. Requires Python 3.10 or newer.

## Use with an agent (MCP)

`.vscode/mcp.json` registers the server for VS Code. For other MCP clients,
run:

```text
taxmap-mcp --project path/to/project
```

or set `TAXMAP_PROJECT`. The agent can also call `project_open` or
`project_init`.

Point your agent at [AGENTS.md](AGENTS.md) and the [skills](skills/).

| Skill | Purpose |
|---|---|
| [taxmap-project-setup](skills/taxmap-project-setup/SKILL.md) | Create and configure a project |
| [ingest-resources](skills/ingest-resources/SKILL.md) | Add documents, links, chats, images |
| [screen-sources](skills/screen-sources/SKILL.md) | Find and verify authoritative documents |
| [extract-assessment-roll](skills/extract-assessment-roll/SKILL.md) | Transcribe and prove per-parcel rolls |
| [transcribe-assessment-formula](skills/transcribe-assessment-formula/SKILL.md) | Turn methods into executable formulas |
| [acquire-parcel-gis](skills/acquire-parcel-gis/SKILL.md) | Download parcels from ArcGIS |
| [build-districts](skills/build-districts/SKILL.md) | Membership rules, notes, outlines |
| [validate-formulas](skills/validate-formulas/SKILL.md) | Check formulas against rolls |
| [build-map-site](skills/build-map-site/SKILL.md) | Build, preview, publish the map |
| [write-transparency-brief](skills/write-transparency-brief/SKILL.md) | Plain-language, cited explanations |

## CLI

```text
taxmap init my-city --name my-city --jurisdiction "City of Example" --state CA
cd my-city
taxmap add report.pdf --title "FY 2026-27 Engineer's Report"
taxmap extract <resource-id>
taxmap search "assessment roll"
taxmap gis download parcels --url https://.../MapServer/9 --kind parcels
taxmap roll import <roll-id> roll.csv --district <id> --stated-total 12345.67
taxmap check <formula-id>
taxmap outlines
taxmap validate
taxmap build
taxmap serve
```

Run `taxmap <command> --help` for options.

## Example: Concord, CA

[examples/concord](examples/concord) ports
[concord-lnl](https://github.com/Spinnernicholas/concord-lnl) to the
platform. It has four FY 2026-27 districts, three verified rolls (1,671
parcels), formulas with full EDU schedules, and outlines that reproduce the
original project exactly. See its [README](examples/concord/README.md).

## Development

```powershell
.\.venv\Scripts\python -m pytest
```

- The formula language is implemented twice: in
  [src/taxmap/formula](src/taxmap/formula/__init__.py) (Python, used for
  validation) and in
  [src/taxmap/site/static/formula.js](src/taxmap/site/static/formula.js)
  (browser calculators). The parity tests run both on the same cases (they
  need Node.js).
- Leaflet 1.9.4 is vendored under `src/taxmap/site/static/vendor/leaflet`
  (BSD-2-Clause, see its LICENSE).
