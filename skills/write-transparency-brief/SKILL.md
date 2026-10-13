---
name: write-transparency-brief
description: Write plain-language, cited explanations of assessments for residents, officials and journalists, such as district explainers, what-if comparisons, equity observations, and answers to "why do I pay this?". Use when the user asks for communication, decision-support, or transparency material from a taxmap project.
---

# Write a transparency brief

The audience is a resident or decision-maker, not an engineer. Every number
comes from the model and links to its source.

## Gather facts with tools, not memory

- `district_summary`, `model_get`, `roll_summary`: totals, counts, rates.
- `formula_evaluate` for example parcels (typical home, condo, business) and
  for what-if inputs such as a new rate or more units.
- `evidence_for` and `resource_read`: quote the exact page.
- `resource_search` over chats and notes for the questions people asked.

## Structure

1. **The answer first.** One or two sentences, e.g. "A typical single-family
   home in Kirkwood pays $40.64 a year for landscaping and lighting."
2. **What it pays for:** services, from the report.
3. **How it is calculated:** the formula in words, with a small table.
4. **Who pays and how much:** distribution by class or zone, and total.
5. **What changed:** rate history, CPI escalator, caps.
6. **Limits:** caveats, approximations, and what the map cannot know.
7. **Sources:** links with page numbers.

## Decision support

For proposals (rate change, new zone, method change), compute with
`formula_evaluate` using `inputs` overrides, or a draft formula id such as
`<district>-proposal`. Present:

- the current versus proposed amount per parcel class
- the total revenue
- the number of parcels affected

Label proposals clearly; never publish a draft formula as the adopted one.

## Style

- Short sentences, everyday words, dollars rounded to cents.
- Neutral tone; separate facts from interpretation.
- Save the brief as `model/notes/<topic>.md` so it becomes a site page, or
  with `resource_add_note` if it is internal.
