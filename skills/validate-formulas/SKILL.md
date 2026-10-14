---
name: validate-formulas
description: Check every formula against its assessment roll, diagnose mismatches, and run the project integrity check before publishing. Use after transcribing a formula, changing data, or before building the site.
---

# Validate formulas and the project

For guided projects, `app_check` composes integrity, every roll's totals and
counts, joins, and every formula/roll pair in one report. Inspect the nested
results: green integrity alone does not mean totals or formulas agree.
Use the individual tools below to diagnose specific failures. Keep supported
partial previews labeled with caveats; never change source amounts to get green.

## Formula versus roll

Call `formula_validate` with `formula_id`. The result has `match_rate`,
`computed_total` versus `official_total_of_computable`, `missing_reasons`,
and `mismatch_examples` (with the resolved inputs).

| Result | Meaning | Action |
|---|---|---|
| `status: pass` (all match within `tolerance`) | Formula reproduces the roll | Done |
| Mismatches are one cent | Rounding differs | Check whether rounding is per component or on the total; adjust `round()` placement |
| A few large mismatches | Data limits (e.g. additions change floor area) or special cases | Add a `caveat`; don't overfit |
| Systematic ratio | Wrong constant or unit (acres vs sq ft) | Re-read the rate table |
| Many `missing_reasons` | Input field empty | Use a documented fallback or roll column; otherwise mark the input unavailable |
| Unavoidable gap (input not in the data, e.g. frontage) | Calculator is a lower bound or estimate | Say so in `caveats` and in the district note |

For each mismatch class, evaluate one parcel with `formula_evaluate` and read
the terms. Never change constants to force a match unless the document
supports the new value. Report the match rate honestly in the notes.

## Project integrity

`project_validate` must return `ok: true` before publishing. It checks:

- resource files and hashes
- evidence pointing at real resources
- zone id uniqueness
- roll, formula and district cross-references
- notes files
- that formulas compile

Fix every error. Treat warnings (missing evidence, missing layer data) as a
to-do list.

## Report

Summarize for the user, per district:

- the roll total versus the stated total
- the join rate
- the formula match rate and its known limits
- open questions
