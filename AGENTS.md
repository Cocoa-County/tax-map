# Agent guide

These instructions are for AI agents working with taxmap: building a tax or
assessment map project for a user, or changing this repository.

## Building a project

For first-time CLI/tool installation, follow [SETUP.md](SETUP.md).
That stage installs one persistent skills/MCP plugin and verifies live readiness
after client-supported activation. A tools-only request must not create a city
project. If the user already requested an app, continue when the skills and MCP
tools are live; require a new session/restart only when the client needs it
(current Codex installation requires a new session). See SETUP.md for client details.
For disabling or removing the bundle, use `taxmap-uninstall` and
[UNINSTALL.md](UNINSTALL.md). Preserve city projects and shared tools.

You do the reasoning; the `taxmap` MCP server does deterministic work
(storage, extraction, search, GIS, computation and checks). Record each
decision through a tool so it lands in reviewable project files.

For "Build a taxmap app for <city>, <state>", start with `build-taxmap-app`.
Use `app_start` defaults and continue through research without an optional
settings questionnaire or a second build request. Record adopted-year evidence,
scope and gaps in app.yaml via `app_status`. Use `app_check` for aggregate data
reconciliation, `app_review` for one exact content approval, and `app_preview`
for a checked loopback URL. Partial results must remain visibly partial.
Only interrupt for genuine ambiguity, permissions or essential blockers.
Do not infer approval or bypass guided checks with low-level build tools.
An explicit request to walk through setup still uses `taxmap-project-setup`.

For manual or step-by-step work, the individual skills in [skills/](skills/)
remain available. `build-taxmap-app` coordinates the relevant stages for a
one-request build:

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
  so and record it as a caveat or open question. Continue with a useful supported
  partial scope; ask only when an essential blocker needs a user decision.
- **Final documents only.** Model numbers come from final or adopted
  documents. Label preliminary or proposal material as such.
- **Privacy.** Don't publish owner names or private correspondence unless
  the user explicitly decides to. Use `site.publish_fields` as an
  allow-list, including the APN needed for public map joins. Resources are
  private by default, even when cited: get approval for specific IDs in
  `site.publish_resources` before serving or publicly exporting metadata,
  citations or copies. `app_review` may prepare an unserved private proposal;
  that is not permission to serve, upload or deploy it.
  Review linked research inventory rows, extra roll columns
  (`site.publish_roll_fields`), model prose and published notes separately.
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
  - `app.py`: deterministic guided lifecycle, exact content review and preview.
- The formula semantics must stay identical in `src/taxmap/formula` and
  `src/taxmap/site/static/formula.js`. Add a parity case to
  the shared `CASES` in `tests/test_formula.py` whenever you touch either one;
  `test_js_parity` compares those cases against the browser evaluator.
- Project files are the source of truth. Anything under `.taxmap/` or
  `derived/` must be rebuildable.
- The MCP server must stay deterministic: no LLM calls, and no network
  access except explicit fetch tools and the requested preview's loopback-only
  HTTP health check. Research remains in the host assistant.
- Run the tests with `python -m pytest`. The Concord acceptance tests run
  when `examples/concord/gis/parcels/features.geojson` exists (see
  `examples/concord/README.md`).
