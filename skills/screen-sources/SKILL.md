---
name: screen-sources
description: Find, triage and verify the documents that define special assessments or taxes (engineer's reports, resolutions, rate schedules, rolls, boundary maps). Use when building the research inventory, deciding which document is authoritative, or checking that a source is final and current.
---

# Screen sources

`model/sources.csv` is the research inventory. Each row moves through these
statuses:

`lead` → `screened` → `verified` (or `rejected` / `unreadable`)

| Status | Meaning |
|---|---|
| lead | Possibly relevant; not opened yet |
| screened | Opened; relevant; key facts noted |
| verified | Final/adopted version confirmed; numbers in the model come from it with page evidence |
| rejected | Not relevant, superseded, or draft |
| unreadable | Could not be read; say why in `notes` |

## Procedure

1. **Search widely for leads.** Look for council or board agendas, minutes and
   attachments, Engineer's Reports, levy resolutions, Prop 218 notices, tax
   roll exports, CFD special tax reports, boundary diagrams and GIS services.
   Record each as `source_upsert` with `status: lead`.
2. **Screen.** Ingest the document (`ingest-resources`), skim it
   (`resource_read`, `resource_search`), and record:
   - `finding`: one or two sentences on what it establishes (method, rates,
     totals, zones).
   - `evidence_location`: where, e.g. `"Part D Table 2 PDF p. 14; roll PDF pp. 19-23"`.
   - `district`: the district id it supports.
3. **Verify** only when all of these hold:
   - It is the **final** report for the fiscal year (not preliminary). Check
     the adoption resolution or minutes.
   - The totals in the report agree with each other (rate table × units ≈
     zone totals; roll sum = stated total).
   - It is linked to a stored resource or stable URL.
4. **Reject** preliminary versions once the final exists, and note
   `superseded by <id>`.

## Authority order (most to least)

1. Adopted assessment roll or County tax roll export
2. Final Engineer's Report / special tax report adopted by resolution
3. Resolution text and adopted rate schedules
4. Preliminary reports, staff reports, presentations
5. News, websites, conversations: context only, never the source of a number

When sources conflict, record both, prefer the higher authority, and explain
the difference in a district note.
