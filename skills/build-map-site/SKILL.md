---
name: build-map-site
description: Generate, preview and publish the static parcel map (search, district outlines, per-parcel assessments, calculator modals, sources). Use when the user wants to see or share the map, or after model changes.
---

# Build the map site

## Build

For a guided app with app.yaml, use `app_check`, `app_review` and `app_preview`
from `build-taxmap-app` instead of separate approval/build/serve calls. Review
the exact exported model prose as well as fields/resources, approve the digest
only after user approval, and use the returned verified URL and restart command.
Guided outputs are under `.taxmap/app-previews`; content approval is not deployment
permission. The low-level steps below remain available for expert projects.

1. `project_validate`: fix errors first.
   Before building even a local preview, review the proposed site content
   with the user: parcel fields, extra roll columns, resource metadata,
   citations, document copies and public model prose. Obtain approval for
   specific resource IDs in `site.publish_resources`; defaults withhold all
   resources. The preview uses the same approval-filtered export as deployment.
   Content approval is not permission to commit, upload or deploy. If resources
   remain unapproved, keep them withheld and explain missing citations in
   the preview handoff. Check redistribution rights before copying documents.
2. `site_build` writes `site/`:
   - parcel tiles and published attributes
   - search index
   - districts, with outlines and the APN → membership/amount index
   - compiled formulas
   - sources
   - pages from `model/notes/*.md`
3. Read the `warnings`. Excluded parcel/roll fields are not added back for
   calculators; those inputs cannot prefill. Unapproved citations and research
   inventory rows are withheld. Resolve or explain these gaps before publishing.
   Field discovery covers the whole layer; warnings identify approved or
   required fields that are genuinely absent.
4. `site_serve` (default port 8000) and open `http://localhost:8000/`. The
   site needs HTTP; opening the file directly will not work.
   Leave the preview available for the user's review; completing the build
   is not a reason to stop it. Call `site_serve` with `stop: true` when the
   user finishes reviewing or requests shutdown. Explain that ending the
   MCP process also stops its preview, and give instructions to reopen it.

## What to check in the preview

- Search an APN from each roll; the panel lists the right districts, zones
  and roll amount.
- Open each calculator: inputs are prefilled with their provenance, the
  result is compared with the roll, and every constant shows a citation.
  Open Lookup tables and verify approved table citations and page locators.
- The Districts modal has outlines labelled approximate and notes that
  render.
- The Sources modal lists the approved resources and their linked inventory
  rows; review missing public citations without exposing private resources.
- The disclaimer is visible.
- Deep links work: `#apn=...`, `#calc=<formula>`, `#district=<id>`,
  `#page=<note>`.

## Publish

The site is static. For manual builds, commit `site/` or build it in CI and
deploy to GitHub Pages, Netlify or any web server, only after separate approval.
For guided apps, use the exact checked export directory returned by
`app_preview`, including its generated coverage page and disclaimer. Copy
only that approved export into the deployment artifact, never the entire
`.taxmap` cache. A low-level rebuild does not enforce app.yaml approval or add
the guided coverage page; do not substitute one without equivalent safeguards.
The Pages workflow template is
`templates/github-pages.yml` in the tools checkout, not the city project or
plugin cache. For an installed skill, read `../../taxmap-plugin.json` relative
to this SKILL.md and resolve `tools_root`, then read the template there.
For a source-checkout skill, use the repository root two levels above this
skill. Verify the file exists before using it. If the checkout is unavailable,
report the blocker and request approval before obtaining matching source;
do not invent a workflow or silently fetch the latest revision.

Before publishing, confirm with the user:

- `site.publish_fields` contains no personal data they do not want public.
  Include the APN field; builds fail if it is excluded because search and
  assessment joins require public APNs. `hide_fields` takes precedence.
- `site.publish_resources` lists only explicitly approved resource IDs.
  It defaults to empty, including for existing projects without this setting.
  Citations do not implicitly approve resources. URL-only inventory rows
  must first be linked to an approved resource.
- `site.publish_roll_fields` lists approved extra columns by roll ID;
  assessment amounts and APN joins are always public. Review all model prose
  and `model/notes` pages, which are public independently of resource approval.
- `site.disclaimer` says the map is unofficial and that the tax bill or roll
  is authoritative.
- `site.copy_documents: true` only if redistribution of approved documents is
  allowed. Rebuilding removes previous generated document copies when approval
  is revoked or copying is disabled; existing deployments need rebuilding and
  redeployment to remove old published content.
