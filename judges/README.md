# Judge packs

A judge pack teaches Snap Judgment a new presiding voice. The evidence in
a hearing never changes; the judge owns the intro line, the sentences,
and the framing of the awards. Load one with:

```
snapjudgment ~/Pictures/Screenshots --judge-file judges/judge-poet.json
```

## Format (v1, frozen)

A pack is a single JSON object. Only `name` is required; every other
field falls back to the fair judge.

| Field | Type | Meaning |
|---|---|---|
| `name` | string, required | The judge's name, shown in the report and carried in the `--json` report's `judge` field. |
| `intro` | string | One line opening the hearing. May use the placeholders `{tier}` and `{count}`. |
| `sentences` | object | One sentence per verdict tier, keyed by the exact tier names below. Missing tiers fall back to the fair judge's sentence for that tier. |
| `award_lead` | string | The line introducing the awards list. |

The six verdict tiers, in ascending order of concern:

- `Innocent Bystander`
- `Petty Offender`
- `Repeat Offender`
- `Habitual Hoarder`
- `Felony-Level Archivist`
- `Unrepentant Screenshot Felon`

Rules of the format:

- Plain JSON, UTF-8, no comments, no trailing commas.
- Sentences begin with `Sentence:` (or `Encouragement:`, if your judge
  is the lenient sort) and stay one or two lines. The report is a formal
  court document that happens to be funny; the judge is seasoning, not
  the meal.
- A pack changes tone only. It must never change evidence, counts, or
  awards; the loader ignores any other keys.
- A malformed pack (bad JSON, missing `name`, wrong types) exits with
  code 2 and a plain message. The court does not seat a judge it cannot
  read.

## Packs in this folder

- `judge-poet.json`: The Poet (also at `examples/judge-poet.json`).
  Reads the folder like a diary.
- `judge-drill-sergeant.json`: The Drill Sergeant. Inspections at dawn;
  mercy is not on the docket.
- `judge-grandmother.json`: The Grandmother. Every screenshot is
  wonderful, dear, and she has seen the receipts to prove it.

The three built-in judges (`--judge strict`, `fair`, `lenient`) live in
the tool itself and need no pack.

## Submitting a pack

Open a pull request adding one JSON file per judge to this folder, and
add yourself to the list above in the same PR. Packs are reviewed for
the same standard as the built-ins: funny because the evidence is real,
never mean about a real person, and readable as a formal document.
