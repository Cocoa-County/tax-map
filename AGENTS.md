# Agent guide

These instructions are for AI agents working with taxmap: building a tax or
assessment map project for a user, or changing this repository.

## Building a project

For first-time CLI/tool installation, follow [SETUP.md](SETUP.md).
That stage installs one persistent skills/MCP plugin without a city project, then
stops for the user to restart their CLI. Create projects only in the fresh
session after verifying the registered tools and project-setup skill.
For disabling or removing the bundle, use `taxmap-uninstall` and
[UNINSTALL.md](UNINSTALL.md). Preserve city projects and shared tools.

You do the reasoning; the `taxmap` MCP server does deterministic work
(storage, extraction, search, GIS, computation and checks). Record each
decision through a tool so it lands in reviewable project files.

The usual flow uses these skills in [skills/](skills/):

1. `taxmap-project-setup`: open or create the project and set the APN format
   and site settings.
2. `ingest-resources`: add the documents, links, chats and images the user
   provides.
3. `screen-sources`: find authoritative documents and move them from lead to
   verified.
4. `acquire-parcel-gis`: get the parcel polygons and configure the fields.
5. `extract-assessment-roll`: transcribe rolls, then prove the totals and
   the parcel join.
6. `build-districts`: write membership rules, notes and outlines.
7. `transcribe-assessment-formula`: write the method as executable formulas.
8. `validate-formulas`: compare against the rolls and run `project_validate`.
9. `build-map-site`: build, preview and publish.
10. `write-transparency-brief`: produce plain-language, cited explanations.

Steps 4–7 can run in any order once sources are verified.

### Non-negotiables

- **Cite everything.** Every rate, total, zone and roll needs `evidence` with
  the resource id and 1-based PDF page. Put printed page numbers in
  `locator`.
- **Rolls beat formulas.** The adopted roll is the authoritative amount. A
  formula explains the amount and lets people explore it; never adjust
  constants to force a match.
- **Never invent data.** If a page is unreadable or a value is missing, say
  so, record it as a caveat or open question, and ask the user.
- **Final documents only.** Model numbers come from final or adopted
  documents. Label preliminary or proposal material as such.
- **Privacy.** Don't publish owner names or private correspondence unless
  the user explicitly decides to. Use `site.publish_fields` as an
  allow-list.
- **Be honest about approximations.** Outlines are approximate. Calculators
  with missing inputs give estimates. Say so in caveats and notes.

### Useful MCP resources

- `taxmap://project`: config and counts.
- `taxmap://resource/{id}` and `taxmap://resource/{id}/page/{n}`: metadata
  and page text.
- `taxmap://model/{kind}/{id}`: a district, formula or roll.

## Working on this repository

- Python package: `src/taxmap`.
  - `project`: folder model.
  - `ingest`, `extract`, `index`: resources.
  - `gis`: layers, ArcGIS and outlines.
  - `assess`: rolls, memberships, evaluation.
  - `formula`: expression language.
  - `site`: site generator and static app.
  - `mcp/server.py`: tools.
  - `cli.py`.
- The formula semantics must stay identical in `src/taxmap/formula` and
  `src/taxmap/site/static/formula.js`. Add a parity case to
  `tests/test_formula_parity.py` whenever you touch either one.
- Project files are the source of truth. Anything under `.taxmap/` or
  `derived/` must be rebuildable.
- The MCP server must stay deterministic: no LLM calls, and no network
  access except explicit fetch tools.
- Run the tests with `python -m pytest`. The Concord acceptance tests run
  when `examples/concord/gis/parcels/features.geojson` exists (see
  `examples/concord/README.md`).
