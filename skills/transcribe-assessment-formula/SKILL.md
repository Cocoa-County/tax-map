---
name: transcribe-assessment-formula
description: Turn an Engineer's Report or rate schedule's apportionment method into an executable taxmap formula with constants, lookup tables, inputs and page-level evidence. Use when modelling how a parcel's assessment or special tax is calculated.
---

# Transcribe an assessment formula

The formula explains the levy and powers the map calculator. It is **not**
the source of truth for amounts: the roll is. Model what the document says,
and record what the data cannot capture as caveats.

## 1. Read the method

Find the apportionment or method section, the rate tables, and any appendix
schedules (EDU or land-use tables). Write down:

- Each benefit **component** or zone, and how it is measured: per parcel,
  per EDU or unit, per sq ft of lot or building, per front foot, or as a
  share of total.
- Each **rate or constant** with its PDF page.
- Lookup schedules, such as land-use code → EDU.
- Escalators and caps (CPI, maximum rate). These are context, not inputs,
  unless the user is exploring future years.

## 2. Map inputs to data

For each variable, choose an input `source`:

| Value | Source |
|---|---|
| Parcel attribute (use code, units, area) | `{parcel: FIELD}` or `{parcel: [FIELD1, FIELD2]}` (first non-empty) |
| Roll column (class, units, zone flag) | `{roll: roll_id, column: name}` |
| Zone membership indicator | `{member: zone_id}` (gives 1 or 0) |
| Not in any data (frontage, private street) | no source, with `default` and a `caveat` |

Use `layer_info` on the parcel layer to find fields and fill rates. When a
field is only an approximation (for example current living area versus
original floor area), say so in the input `caveat`.

## 3. Write it

Call `formula_upsert` with:

- `constants`: every rate, each with `evidence`.
- `lookups`: entries use `keys` such as `"10, 15, 62-69"` and `key_type: int`.
  Put sub-factors in `details`.
- `terms`: named sub-results. Use one term per zone or component so the
  calculator shows the breakdown.
- `expression`: usually `round(sum of terms, 2)`.
- `applies_when` (optional) and `caveats` (plain-language limits).

Expression language: `+ - * / % ^`, comparisons, `and or not`, `if(c,a,b)`,
`min`, `max`, `round(x,n)`, `floor`, `ceil`, `abs`, `coalesce(a,b,...)`,
`present(x)`, `lookup(table, key)`. A missing value is an error unless it is
guarded with `coalesce`, `present` or `if`. Note that `coalesce` skips
missing values but not zeros; use `if(coalesce(x,0) > 0, x, fallback)` to
skip zeros.

## 4. Check it

- `formula_evaluate` on a few known parcels; read every term.
- Then run the `validate-formulas` skill.
- Link the formula from the district (`formulas: [id]`) and attach
  `evidence_add` for anything you missed.
