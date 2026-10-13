---
name: build-map-site
description: Generate, preview and publish the static parcel map (search, district outlines, per-parcel assessments, calculator modals, sources). Use when the user wants to see or share the map, or after model changes.
---

# Build the map site

## Build

1. `project_validate`: fix errors first.
2. `site_build` writes `site/`:
   - parcel tiles and published attributes
   - search index
   - districts, with outlines and the APN → membership/amount index
   - compiled formulas
   - sources
   - pages from `model/notes/*.md`
3. Read the `warnings`. A warning that a field required by a formula is
   hidden means the calculator cannot prefill that input.
4. `site_serve` (default port 8000) and open `http://localhost:8000/`. The
   site needs HTTP; opening the file directly will not work. Call
   `site_serve` with `stop: true` when done.

## What to check in the preview

- Search an APN from each roll; the panel lists the right districts, zones
  and roll amount.
- Open each calculator: inputs are prefilled with their provenance, the
  result is compared with the roll, and every constant shows a citation.
- The Districts modal has outlines labelled approximate and notes that
  render.
- The Sources modal lists every verified source.
- The disclaimer is visible.
- Deep links work: `#apn=...`, `#calc=<formula>`, `#district=<id>`,
  `#page=<note>`.

## Publish

The site is static. Commit `site/` or build it in CI and deploy to GitHub
Pages, Netlify or any web server. A Pages workflow template is in this repo
at `templates/github-pages.yml`.

Before publishing, confirm with the user:

- `site.publish_fields` contains no personal data they do not want public.
- `site.disclaimer` says the map is unofficial and that the tax bill or roll
  is authoritative.
- `site.copy_documents: true` only if redistribution of the documents is
  allowed.
