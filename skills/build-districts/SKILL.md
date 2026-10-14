---
name: build-districts
description: Define assessment districts and benefit zones with membership rules, colors, totals, evidence and plain-language notes, then build approximate district outlines from member parcels. Use after rolls or parcel attributes identify which parcels are in each district.
---

# Build districts and zones

## Membership rules (choose exactly one per district or zone)

| Situation | Rule |
|---|---|
| Roll lists the district's parcels | `{roll: roll_id}` |
| Roll has a zone column or Y flags | zone: `{roll: roll_id, where: {zone: "Kirkwood"}}` or `{roll: roll_id, where: {streetscape: "Y"}}` |
| Every parcel in the district is in the zone | zone: `{parent: true}` |
| Defined by a parcel attribute (e.g. city boundary, tax-rate area) | `{attribute: FIELD, values: [..]}` |
| Defined only by a map | Digitize or import a boundary layer and record it as a lead. Don't guess membership. |

A district with no membership of its own is the union of its zones. Zone ids
must be unique across the whole project.
Roll filters require an explicit roll ID; they do not inherit the district's
roll. An explicit membership object must choose exactly one approach.
Attribute rules require non-empty `values`; `parent` is valid only for zones.
Omit membership for a district defined by the union of its zones.

## Procedure

1. Call `district_upsert` with `id`, `name`, `short_name`, `fiscal_year`,
   `agency`, `color` (optional), `membership`, `zones`, `formulas`, `total`,
   `parcel_count`, `notes` (a file name in `model/notes/`), and `evidence`.
2. Write `model/notes/<id>.md`. This is a plain-language explainer for
   residents: what the district pays for, how charges are calculated, the
   totals, and what is uncertain. Keep it short and use tables for rates.
   (Write it with your file tools, or use `resource_add_note` for research
   notes.)
3. `district_summary` shows member counts per district and zone; compare them
   with the report.
4. Outlines: call `district_outline_build`.
   - Each result has `member_parcels`, `parts`, `area_acres` and
     `non_member_parcels_inside`.
   - Parcels are merged by closing gaps of `gap_m` (default 30 m), so streets
     inside the district are filled in. Use a smaller gap (e.g. 15 m) for
     narrow corridor zones that merge across streets.
   - Many `parts` usually means scattered parcels; a high
     `non_member_parcels_inside` count means the gap is too large. Tune with
     `outline: {gap_m, fill_holes_m2}` on the district or zone. Set
     `outline: {enabled: false}` for citywide districts.
   - Outlines are **approximate** and labelled so on the map. Parcel
     membership is always authoritative.
