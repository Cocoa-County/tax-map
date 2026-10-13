---
name: extract-assessment-roll
description: Transcribe a parcel assessment roll (APN, zone or class, amount) from a PDF or spreadsheet into model/rolls and prove it is complete. Use when a report or tax-roll export lists per-parcel charges.
---

# Extract an assessment roll

A roll is the most reliable data in a project. It gives exact membership and
the billed amount per parcel. Formulas are only checked against it.

## Procedure

1. **Locate the roll pages.** `resource_search` for "Assessment Roll",
   "Assessor's Parcel", "APN" or "Levy". Note the first and last PDF page.
   Also find the report's **stated total and parcel count**.
2. **Extract the rows**, trying these methods in order:
   - `extract_tables` per page (`strategy: lines`, then `text` for
     borderless tables).
   - `extract_words` per page and group words into rows by `top` and columns
     by `x0`. This works for most fixed-layout rolls.
   - Scanned pages: `render_page_image` and transcribe visually, then
     double-check every row total.
   - A spreadsheet or CSV export: `resource_add_file` it and pass the path
     to `roll_import`.
3. **Normalize columns.**
   - `apn`: keep the document's formatting; normalization happens on join.
   - `assessment`: number with no `$` or `,` (`roll_import` tolerates both,
     but keep the CSV clean).
   - Keep useful extra columns such as `zone`, `property_class`, `units`,
     `frontage`, and Y/blank flags for each zone. They drive memberships and
     formula inputs.
4. **Import:** call `roll_import` with `roll_id` (e.g. `downtown-2026-27`),
   `district`, `rows` or `csv_path`, `fiscal_year`, `stated_total`,
   `stated_count`, `columns` (a description of each column) and `evidence`
   (page range).
5. **Prove completeness.**
   - `roll_summary` must show `checks` all `ok`, with no duplicates and no
     unparsed amounts. If the total is off, find the missing or misread rows
     page by page (compare page subtotals when the report prints them).
   - `roll_join_parcels` should match about 100%. Unmatched APNs usually
     mean a typo, a retired or split parcel, or a different APN format.
     Explain any that remain in the roll `notes`.

## Rules

- Never fabricate or interpolate rows. If a page is unreadable, stop and
  report it.
- Parcels levied $0 that are not printed in the roll are not members. Say
  so in the district note.
- Re-run the checks after every correction.
