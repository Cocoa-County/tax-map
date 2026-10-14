---
name: ingest-resources
description: Add documents, web pages, chats, pictures, notes and data files to a taxmap project and make them searchable. Use when the user provides files, links, meeting records, emails, photos or conversation transcripts to research.
---

# Ingest resources

Everything the project learns from is a **resource** with a stable id. Model
values cite resources by id and page, so ingest first and cite later.

## Choose the tool

| Input | Tool | Notes |
|---|---|---|
| Local file (PDF, image, CSV, GeoJSON, text) | `resource_add_file` | Copied into `resources/<kind>/`, sha256 recorded |
| URL to snapshot | `resource_add_url` | Downloads and stores a copy; records `retrieved_at` |
| URL that is too large or not allowed to copy | `resource_add_url` with `link_only: true` | Citation only; no text search |
| A conversation (user interview, email thread, chat) | `resource_add_chat` | `messages`: list of `{role, content, author, time}` or plain text |
| Your own research findings | `resource_add_note` | Markdown; cite it like any document |

Give each resource a clear `title`. Add `tags` such as `engineers-report`,
`fy2026-27`, `roll`, `council`, `photo` or `boundary-map`. Prefer a meaningful
`resource_id` such as `downtown-report-2026-27`.

## After adding

1. `extract_text` for PDFs and HTML. Its result lists pages with no text
   layer. For those, and for maps, diagrams and photos, use
   `render_page_image` and read the image yourself.
2. `index_rebuild` (it also runs automatically on search) and then
   `resource_search` to check the content is findable.
3. Add a `source_upsert` row so the research inventory tracks the document
   (see `screen-sources`).

## Rules

- PDF page numbers in evidence are **1-based PDF pages**, not printed page
  numbers. Put the printed page in `locator`, e.g.
  `"Part D, Table 2, printed p. 10"`.
- Never edit files under `resources/`. If a document changes, add the new
  version as a new resource.
- Chats and notes can hold personal information. Ask before ingesting
  private correspondence. Ingestion and citation do not authorize publication.
  Resources remain private unless their IDs are explicitly approved in
  `site.publish_resources`. Approval exposes metadata and citation text;
  `site.copy_documents: true` also copies approved local files. Review linked
  research inventory rows before approving a resource. Keep private prose
  out of model descriptions and `model/notes`, which are public site content.
