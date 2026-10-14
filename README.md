# taxmap

Build transparent tax and special-assessment maps from public documents,
with AI agents doing the research.

Load a project with resources such as Engineer's Reports, resolutions,
assessment rolls, web pages, meeting chats, photos and GIS data. An agent
using the **taxmap MCP server** and **agent skills** turns them into:

- **A cited model:** districts, benefit zones, rates, lookup schedules and
  formulas. Every number points to the page it came from.
- **Checked rolls:** per-parcel amounts compared with the document's stated
  totals and joined to parcels, with unresolved discrepancies disclosed.
- **GIS layers:** parcels and approximate district outlines.
- **A static map site:** parcel search, per-parcel districts and amounts,
  calculator modals with typeset equations, live numeric substitutions,
  color-coded symbol explanations and calculation steps, district explainers and a
  source list. It needs no backend. Generated apps include a linked
  "taxmap by Cocoa County" footer attribution with the Cocoa County mug logo,
  alongside the project's disclaimer. Rebuild existing sites to add it.
  On every load, a title modal shows the app title, subtitle, disclaimer,
  powered-by attribution and "Created by Nick Spinner". Choose "Explore the
  map" or press Escape to dismiss it; any deep link then opens without a tour.
  Credits and LinkedIn, GitHub and taxmap source-code icon buttons sit below a
  horizontal divider, separate from the map information and Explore button.

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

Configure official per-parcel links under `site.parcel_links`:

```yaml
site:
  parcel_links:
    - label: County tax bill (PDF)
      url_template: https://taxcolp.cccttc.us/PTS_TaxbillsPdfCY?APN={apn_display}
```

Templates support `{apn}` (normalized APN) and `{apn_display}` (project display
pattern); values are URL-encoded. Only absolute HTTP(S) links with fixed hosts
are allowed. Concord includes current county PDF and accessible HTML bill
links, verified against the county lookup API. Bills open on the county site;
availability and the current bill year may differ from the project's fiscal
year. No bills or payment information are copied into the map.

Use `find-jurisdiction-resources` to discover matching county bill templates
and GIS/meeting portals in the shared catalog. Reverify parcel identity, APN
format and county year behavior before proposing links; include the exact
links in content approval. If none can be included, record why rather than
silently omitting them because the assessment model is incomplete.

Districts can define `bill_labels` to connect their full names with the exact
description and code used in the county bill's **Special taxes and assessments**
section:

```yaml
bill_labels:
  - code: MH
    label: CONCORD DNTWN LMD
    fiscal_year: 2026-27
    evidence:
      - resource: county-bill-example
        locator: Special taxes and assessments
```

Cards show the code and label; district details include verification year and
citations. Optional `note` explains ambiguous wording. Concord's verified labels
are `MH` / `CONCORD DNTWN LMD`, `JC` / `CONCORD STREET LTS`,
`MU` / `PINE HOLLOW LMD`, and `JV` / `KIRKWOOD LMD`. The last label is used for
both Kirkwood and Ygnacio Woods Unit II. Match **both** code and description;
these reference labels do not prove a charge or change parcel membership.
The map covers only its modeled districts, not every special tax on the bill.

taxmap ships the MCP server, the skills, a CLI and the site generator.
The server is deterministic: it stores, extracts, searches, computes and
checks, while the agent reads, decides and writes.

## Concepts

| Concept | Where it lives |
|---|---|
| Project | A folder with `taxmap.yaml`; plain files are the source of truth |
| Guided app state | Optional `app.yaml`: intent, progress, approval and handoff |
| Resource | Any input: document, web page, chat, image, data, note (`resources.yaml`) |
| Source | Research-inventory row: lead → screened → verified (`model/sources.csv`) |
| District / zone | Membership rule plus evidence (`model/districts/*.yaml`) |
| Roll | Authoritative per-parcel amounts (`model/rolls/*.csv`) |
| Formula | Inputs, constants, lookups, terms and an expression (`model/formulas/*.yaml`) |
| Evidence | `{resource, page, locator, quote}` attached to any model value |
| Site | Static Leaflet app: manual builds in `site/`; guided preview path returned by `app_preview` |

## Install

### Start with your AI CLI

Install and sign into Codex CLI, Claude Code, or GitHub Copilot CLI, then paste
this into the agent:

> Download https://github.com/Cocoa-County/tax-map into a separate local folder
> and follow SETUP.md at the repository root to install taxmap and set up its
> skills and MCP for this CLI. Ask before installing software or changing my
> configuration. Do not create a project yet. Activate the plugin using this
> client's supported method and verify live skills and MCP tools. Save a handoff
> with any required session steps and how to request an app for my city and state.

[SETUP.md](SETUP.md) gives the agent the technical workflow: install runtime
requirements, build/install a persistent user-scope skills/MCP plugin, check
live readiness, and save a handoff. **No blanket CLI restart is required.**
Current Copilot supports in-session refresh; Claude can use `/reload-plugins`.
Current Codex requires a new session after plugin installation. Follow the
client-specific activation checks in SETUP.md, then say:

> Build a taxmap app for Martinez, CA.

Replace the city and state with yours. The agent derives the name/title and
uses `~/taxmap-projects/<city-id>`, then continues directly into official-source
research, modeling and a local preview. Include a fiscal year, folder or focus
in the request if desired; otherwise it verifies the latest adopted year.
It safely resumes matching projects and asks about conflicting folders or
essential blockers, not every optional setting.

Review one consolidated proposal of exact fields, resource IDs, extra roll
columns and public explanations before previewing. Incomplete coverage and
source discrepancies stay visible; missing evidence is never filled with
invented values. Nothing is uploaded or deployed automatically. Host tool
permissions remain separate. For a deliberate setup-only walkthrough, say
**"Create a new taxmap project. Walk me through the setup."**
Individual skills, CLI/MCP operations and editable project files remain
available for a hands-on process; using the CLI directly does not require AI.
Follow the [non-technical guide](website/docs/community.html) for installation,
sign-in, permissions, and review.

### One plugin, all taxmap skills

[plugin.json](plugin.json) and [skills/](skills/) are the shared source bundle.
Setup runs [scripts/build_plugin.py](scripts/build_plugin.py) to generate a
local marketplace with every skill, the uninstall skill, and projectless MCP
using the installed environment's absolute executable path. It includes the
portable Agent Plugins format for compatible Copilot/Codex versions and a
Claude adapter. Install the generated bundle, not the raw source manifest,
to get the machine-specific MCP connection.

Use the chosen CLI's plugin manager to **disable, enable, or uninstall taxmap
as one unit**. Apply the client's supported refresh/session step and verify the
effective result; restart only when needed. These actions preserve
city projects; uninstalling a plugin does not remove the separate Python
environment. Do not also register MCP or copy loose skills, since those would
remain active independently.

Ask your agent: **"Disable the taxmap plugin, keeping my projects and data."**
For guided removal, ask it to use `taxmap-uninstall`. If already disabled,
ask it to follow [UNINSTALL.md](UNINSTALL.md) in the tools checkout.
That fallback also covers legacy skill/server cleanup, confirmations,
cross-client dependencies, and optional tool-only cleanup.

Bundle builds refuse existing destinations. Updates require a new build,
approved marketplace refresh/reinstallation, and verification; they do not
automatically migrate project data. See [SETUP.md](SETUP.md) for exact steps.

### Shared jurisdiction resources and contributions

[Cocoa-County/taxmap-resources](https://github.com/Cocoa-County/taxmap-resources)
is a separate, public catalog of reusable official tax-bill URL templates,
parcel/assessment/boundary GIS services, city/county websites and agenda/minute
portals. It does **not** store specific meetings, reports, individual bill URLs,
downloaded data or private project evidence.

The plugin includes two additional skills:

- `find-jurisdiction-resources`: consult a recorded catalog revision before
  research, inherit matching county resources, recheck endpoints and record
  limitations. Missing/unreachable entries do not block official research.
- `contribute-jurisdiction-resources`: prepare a minimal public-safe correction
  or addition and, with approval, submit a fork-based pull request using the
  user's authorized GitHub tools. Maintainers approve and merge; the skill
  never self-approves, auto-merges or pushes contributions directly to main.

Reading needs no GitHub login. Submitting needs authorized GitHub tools and
publication permission, separate from local preview approval. No credentials
are stored in taxmap and no additional MCP registration is required. If tools
are unavailable, the skill provides a patch/manual PR handoff, not a false
submission claim. Catalog verification is a dated endpoint check, not proof of
document adoption, parcel applicability or tax-year matching. Project evidence
and content approvals remain in the project.

### Optional: one-command project installer

Download and run the interactive installer; no repository clone, Git, or
administrator terminal is required.

**Windows PowerShell:**

```powershell
$p = Join-Path $env:TEMP ("taxmap-" + [guid]::NewGuid() + ".ps1"); try { Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/Cocoa-County/tax-map/main/scripts/install.ps1 -OutFile $p -ErrorAction Stop; & $p } finally { if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p } }
```

**macOS/Linux Bash:**

```bash
( f="$(mktemp)" || exit; trap 'rm -f "$f"' EXIT; curl -fsSL https://raw.githubusercontent.com/Cocoa-County/tax-map/main/scripts/install.sh -o "$f" && bash "$f" )
```

These URLs work after the scripts are merged to `main`. The installer asks
for project name, exact folder location, and jurisdiction, with optional map
title, county, state, and fiscal year. The default is a new folder under
`~/taxmap-projects`; existing non-empty folders are refused. Review the
settings and confirm before project files are created. Optional research focus
and starting document paths or official URLs personalize the generated request;
references are not opened, uploaded, or fetched by setup.
Run the installer from any folder; it creates your selected project directory
and any missing parent folders. No separate directory-creation command is
needed.

taxmap and **all runtime dependencies** are installed in the project's
`.venv`; a separate `.taxmap-tools` folder holds the source and agent skills.
If Python 3.10+ with venv/pip is missing, a separate confirmation offers
user-scoped [uv](https://docs.astral.sh/uv/reference/installer/) and managed
Python 3.13 under `~/.local/share/taxmap/bootstrap`. No global Python or shell
profiles are changed. Downloads require internet access to GitHub, PyPI,
and (for bootstrap) Astral's official distribution services.

**Only run downloaded code you trust.** To inspect first, download
[the PowerShell script](scripts/install.ps1) or [the Bash script](scripts/install.sh)
to a file, review it and [the shared wizard](scripts/setup_project.py), then
run `& .\install.ps1` or `bash ./install.sh`. The launcher also downloads
matching setup/source files and may run Astral's installer. To pin the
repository revision, replace `main` in the download URL with a full commit
SHA and set `TAXMAP_REF` to that same SHA before running.

If your organization's PowerShell execution policy blocks scripts, follow
its approved process; the installer does not bypass policy or request
elevation. Download failures and installation errors stop setup explicitly.
Any partial project is left for inspection: retry in a new empty folder
instead of overwriting it. uv/Python may remain in the user bootstrap folder
if project setup is cancelled; do not remove managed Python while an
installed environment depends on it.

Success creates an **empty project**, records installation paths and the
tools revision in `taxmap-install.json`, and prints an exact CLI command and
an existing-project plugin handoff. It also generates `PROJECT-PROMPT.md`.
Follow the printed setup request using the existing checkout/environment,
activate and verify live tools, then open the existing project and read the
generated request. Change sessions or restart only when the client requires it.
If a plugin is already connected, reuse it; do not register independent MCP
or create another project.
The request includes evidence, privacy, and local-preview safeguards; it is
editable and is not included in published site notes.
Setup does not install/sign into an AI agent, register MCP, research
records, or publish anything. Keep the environment and tools folders in
place; they are excluded from project Git tracking.

### Manual developer setup

**New to terminals or building with AI?** Follow
[non-technical guide](website/docs/community.html): install and sign into
Codex, Claude Code, or GitHub Copilot CLI, ask it to follow SETUP.md and verify
live readiness, then create a project. The direct installer above is an alternative, not a
required first step. Permissions, missing data, and publication approval
remain explicit. No hosted backend is required. For repository development:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

On macOS or Linux use `.venv/bin/pip`. Requires Python 3.10 or newer.

## Use with an agent (MCP)

### Recommended: plugin-managed connection

Follow [SETUP.md](SETUP.md) to install and activate the single skills/MCP plugin,
verify live readiness, and use `build-taxmap-app` with its live `app_start` tool. Expert
projects can still use `project_open` or `project_init`. The plugin
owns the connection: do not also register an independent MCP server or copy
loose skills. Its server starts without a pinned project.

### Manual alternative: independently managed MCP

For development or clients without the plugin format, explicitly choose and
configure a separate stdio connection. This is not controlled by the plugin:
disabling or uninstalling the plugin will not stop this server or remove
independently installed skills. Avoid running both setups for the same work;
review legacy entries with [UNINSTALL.md](UNINSTALL.md) before migrating.

[.vscode/mcp.json](.vscode/mcp.json) is a Windows-specific development example
pinned to Concord, not generic project setup. For another manual MCP client,
configure its command and arguments to launch:

```text
taxmap-mcp --project path/to/project
```

or set `TAXMAP_PROJECT`. The agent can also call `project_open` or
`project_init`.

Point your agent at [AGENTS.md](AGENTS.md) and the [skills](skills/).

### Bundled skills

| Skill | Purpose |
|---|---|
| [build-taxmap-app](skills/build-taxmap-app/SKILL.md) | One request through research, exact content review and a checked local app |
| [taxmap-project-setup](skills/taxmap-project-setup/SKILL.md) | Optional step-by-step setup or existing-project configuration |
| [ingest-resources](skills/ingest-resources/SKILL.md) | Add documents, links, chats, images |
| [screen-sources](skills/screen-sources/SKILL.md) | Find and verify authoritative documents |
| [extract-assessment-roll](skills/extract-assessment-roll/SKILL.md) | Transcribe and prove per-parcel rolls |
| [transcribe-assessment-formula](skills/transcribe-assessment-formula/SKILL.md) | Turn methods into executable formulas |
| [acquire-parcel-gis](skills/acquire-parcel-gis/SKILL.md) | Download parcels from ArcGIS |
| [build-districts](skills/build-districts/SKILL.md) | Membership rules, notes, outlines |
| [validate-formulas](skills/validate-formulas/SKILL.md) | Check formulas against rolls |
| [build-map-site](skills/build-map-site/SKILL.md) | Build, preview, publish the map |
| [write-transparency-brief](skills/write-transparency-brief/SKILL.md) | Plain-language, cited explanations |
| [taxmap-uninstall](skills/taxmap-uninstall/SKILL.md) | Disable, re-enable, remove tools, and review legacy installations safely |

### Guided app lifecycle

The assistant handles research and judgment; the MCP server never calls an
LLM. These deterministic helpers compose existing operations:

| Tool | Purpose |
|---|---|
| `app_start` | Safe creation/resume, standard-folder defaults, preserved explicit year |
| `app_status` | Durable scope, adoption evidence, caveats and artifact references |
| `app_check` | Integrity plus every roll total/count, parcel join and formula/roll comparison |
| `app_review` | Private exact-export manifest; explicit user approval recorded by digest |
| `app_preview` | Approved build, owned localhost server, available port and HTTP health check |

Guided projects keep intent, progress, approval and last readiness/preview
handoff in reviewable `app.yaml`. Detailed reports in `derived` and preview
outputs under `.taxmap/app-previews` are rebuildable. A saved URL is not proof
that a server is running. Resume with the same identity/path, check current
files, then preview; unchanged content needs no repeated content approval.
The MCP process owns its preview. Stop with `app_preview(stop=true)` or use
the returned standalone restart command and Ctrl+C. Content changes need
review again; public deployment always requires separate permission.

`project_validate` still means file/reference integrity, not financial
reconciliation. `ready_with_caveats` can include printed-total differences,
unmatched parcels or missing optional calculators; the generated coverage
page discloses them and never changes the adopted amounts. Structural errors,
unparsed amounts, ambiguous joins and an empty unsupported model block the
guided preview. Existing low-level CLI/MCP behavior remains available.

## Publication controls

Before a viewable local preview or public deployment, review these settings in
`taxmap.yaml`. Guided `app_review` may prepare an unserved private export for
this review; that is not permission to serve, upload or deploy it.

- `site.publish_fields`: a strict parcel-attribute allow-list. Low-level builds
  retain the legacy `null` = all fields behavior; guided apps require an explicit
  list. Required calculator, search and label fields are **not** added
  back. `site.hide_fields` takes precedence. The parcel APN must be approved:
  builds fail if it is excluded because public search and assessment joins
  require APNs. Excluded addresses are omitted from the search index too.
- `site.publish_resources`: approved resource IDs, defaulting to `[]`.
  Only approved resources' metadata and citation text, and research inventory
  rows linked to them, are published. Ingesting or citing a resource does not
  approve publication. Link URL-only inventory rows to an approved resource.
- `site.copy_documents`: default `false`; when enabled, copies only approved
  resources. Review privacy and redistribution rights before approval.
  Rebuilds remove previous generated copies when approval is revoked or copying
  is disabled, preserving unrelated files.
- `site.publish_roll_fields`: extra approved roll columns, keyed by roll ID,
  defaulting to `{}`. Assessment amounts and APN joins are always public;
  other columns need explicit approval, including calculator inputs.

Model descriptions, roll notes and `model/notes/*.md` are public prose, not
private resources; review them separately. Build warnings report excluded
calculator inputs, genuinely absent approved/required fields and withheld
citations/inventory rows. Approved lookup-table citations appear in calculators.

**Existing projects:** without `publish_resources`, no resource metadata,
citations or documents are exported. Review and add approved IDs rather than
automatically approving the registry. Approve necessary extra roll columns
explicitly. Existing generated project requests are not rewritten: replace
old independent-MCP instructions with the plugin-managed `project_open` flow.
Previously deployed content is not removed until you rebuild and redeploy.

## CLI

These low-level commands remain available for manual work. They do not run
AI research or enforce the guided `app.yaml` approval lifecycle. Review content
before building/serving, check roll reconciliation separately from integrity,
and preserve coverage disclosures and source amounts.

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
Every new project initialized by CLI or MCP receives `PROJECT-PROMPT.md`.
Optional `init --focus TEXT` and repeated `--starting-resource PATH-OR-URL`
arguments personalize it; MCP `project_init` accepts the matching `focus` and
`starting_resources` parameters. See the
[template](src/taxmap/project/templates/project-prompt.md).
Existing projects are not migrated, and edited requests are never silently
regenerated when settings change.

## Example: Concord, CA

[examples/concord](examples/concord) ports
[concord-lnl](https://github.com/Spinnernicholas/concord-lnl) to the
platform. It has four FY 2026-27 districts, three verified rolls (1,671
parcels), formulas with full EDU schedules, and outlines that reproduce the
original project exactly. See its [README](examples/concord/README.md).

## Public website and GitHub Pages

- [Landing page](https://cocoa-county.github.io/tax-map/)
- [Concord example map](https://cocoa-county.github.io/tax-map/concord/)
- [Documentation](https://cocoa-county.github.io/tax-map/docs/): two distinct
  paths: [Use a map](website/docs/use.html) for readers, and
  [Create a map](website/docs/create.html) for project creators. The latter
  contains agent-assisted, developer, and AI-agent reference guides. Existing
  guide URLs remain unchanged. Agents can read the
  [plain-text playbook](https://cocoa-county.github.io/tax-map/docs/agents.txt)
  and [documentation index](https://cocoa-county.github.io/tax-map/llms.txt).

The landing page source is in [website/](website/). The
[Pages workflow](.github/workflows/pages.yml) builds Concord from this checkout,
downloads its configured public parcel layer (cached between builds), and
publishes both sites in one artifact. Pushes to `main` deploy automatically;
`workflow_dispatch` also allows a manual rebuild. Parcel geometry and generated
map files are not committed.

taxmap is the application; Cocoa County is the organization behind it. The
website leads with the app name and attributes it as "by Cocoa County."
The landing page and documentation use Cocoa County's community/open-source
identity from the [organization profile](https://github.com/Cocoa-County/.github)
with an original cozy cocoa-and-cream theme and gently floating marshmallows.
The theme selector supports system, light, and dark modes, with a remembered
choice across documentation pages. Cozy red wordmark mugs have gently rising
steam. Both steam and marshmallow motion can be paused and are disabled for
reduced-motion preferences. No external fonts or animation libraries are needed.
This is a project-local design, not a shared official design-system package.
The generic map generator and Concord district colors remain unchanged.

In GitHub **Settings > Pages**, select **GitHub Actions** as the source.
The repository site serves the landing page at `/tax-map/` and the example at
`/tax-map/concord/`. Relative links keep both working under the repository prefix.
If the parcel download or model validation fails, the deployment fails rather
than publishing an incomplete map; the previous deployment remains available.

For a separate city's project repository, use the standalone
[Pages workflow template](templates/github-pages.yml).
That template uses the low-level build, not guided app approval. For a guided
app, publish the exact approved export returned by `app_preview` only after
separate deployment permission, preserving its generated coverage page and
disclaimer. Do not publish the whole `.taxmap` cache. A CI rebuild needs equivalent
approval, reconciliation and coverage safeguards; a green integrity check alone
is not enough.

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
