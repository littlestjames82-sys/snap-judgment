# Snap Judgment

[![CI](https://github.com/littlestjames82-sys/snap-judgment/actions/workflows/ci.yml/badge.svg)](https://github.com/littlestjames82-sys/snap-judgment/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/github/license/littlestjames82-sys/snap-judgment)](LICENSE)

**Your screenshots, reviewed.** Snap Judgment reads a folder of screenshots and issues a courtroom-style verdict on your screenshot habits: a tier, the evidence, awards, and a sentence.

![Snap Judgment in action: a folder is judged, verdict delivered](examples/demo.gif)

Meet [the defendants](gallery/README.md): five synthetic screenshot collections, from The Minimalist (35 screenshots, acquitted) to The Hoarder (2,800, Unrepentant Screenshot Felon), judged and entered into the record. New presiding voices live in [judges/](judges/README.md), and the version record is kept in the [changelog](CHANGELOG.md).

## Install

```
pip install snap-judgment
```

Pure Python standard library. No dependencies, Python 3.9+.

## Usage

```
snapjudgment ~/Pictures/Screenshots
snapjudgment ~/Desktop ~/Pictures --card card.html --out report.md
snapjudgment ~/Pictures --svg card.svg   # SVG share card (1200x630)
snapjudgment ~/Pictures --png card.png   # PNG share card (1200x630), rendered with the stdlib alone
snapjudgment ~/Pictures --html page.html # full self-contained HTML report
snapjudgment ~/Downloads --all           # judge every image in the folder
snapjudgment ~/Pictures --since 2024     # only screenshots from 2024 onward
snapjudgment ~/Pictures --year 2026      # judge one single year only
snapjudgment ~/Pictures --vs ~/Phone     # co-defendant mode: compare two folders
snapjudgment ~/Pictures --vs-year 2026   # compare one year against another year in the data
snapjudgment ~/Pictures --judge strict   # strict | fair | lenient presiding judge
snapjudgment ~/Pictures --judge-file poet.json  # a custom judge voice from a JSON pack
snapjudgment ~/Pictures --wrapped-kit ./kit     # year-scoped SVG card + ready-to-post drafts
snapjudgment ~/Pictures --fail-above "Repeat Offender"  # exit 1 if the verdict ranks above this tier
snapjudgment ~/Pictures --oneline        # print exactly one summary line
snapjudgment ~/Pictures --csv files.csv  # per-file records as CSV
snapjudgment --history                   # print the court record of prior hearings
snapjudgment ~/Pictures --json           # machine-readable verdict
```

With no folder argument, Snap Judgment checks the usual places
(`~/Pictures/Screenshots`, `~/OneDrive/Pictures/Screenshots`, `~/Desktop`,
`~/Pictures`, `~/Downloads`) and judges the first one that actually
contains screenshots.

The verdict tiers run from **Innocent Bystander** (under 50 screenshots) up
to **Unrepentant Screenshot Felon** (2,500+). Along the way the court weighs
the evidence: total count and size, the largest single screenshot, date
span, busiest day, month and hour, night share, weekend share, evidence
retention (how much of the collection is over a year old), screenshots per
day, median per active day, silent months (whole calendar months inside the
span with zero screenshots), longest daily streak, longest clean stretch,
biggest 60-second burst, duplicate suspects and the storage they waste, the
per-format share (PNG, JPG, HEIC...), your busiest screenshot season, your
screenshot anniversary (the month-day you shoot most, summed across years),
and the device eras told by your screen resolutions. Files the court cannot
read (broken symlinks, locked files) are counted and reported, never fatal.

With 5 or more screenshots the report also presents **exhibits**: the five
oldest screenshots, entered into evidence as Exhibit A through Exhibit E.
When several folders are judged in one run, an evidence block lists each
folder with its screenshot count.

With 10 or more screenshots the court also reads **the charges**: a census
of where the screenshots came from, from metadata hints only. Android
filenames that carry an app package are credited to that app (WhatsApp,
Telegram, Instagram, and friends); everything else is classified by the
form factor its dimensions suggest, from Phone (tall) to Desktop /
landscape.

Three judges can preside. The **fair** judge is the default. The **strict**
judge (The Hanging Judge) words the same verdict severely, and the
**lenient** judge (The Sympathetic Judge) frames everything kindly, with
encouragement instead of a sentence. The voice changes the wording only,
never the math.

### Custom judge packs

`--judge-file FILE.json` seats a judge of your own. A pack is a JSON object:

```json
{
  "name": "The Poet",
  "intro": "The Poet presides over {count} screenshots.",
  "sentences": {
    "Innocent Bystander": "A short poem of a folder."
  },
  "award_lead": "The court pins these medals:"
}
```

`name` is required; `intro` (which may use `{tier}` and `{count}`),
`sentences`, and `award_lead` are optional. Any verdict tier missing from
`sentences` falls back to the fair judge's sentence for that tier. A
complete example, **The Poet**, ships at `examples/judge-poet.json`. With
`--json`, the report's `judge` field carries the pack's name. A malformed
pack exits with code 2 and a plain message.

### Year over year

`--vs-year 2026` compares one year against the most recent year present in
the data (or, when the target is the most recent year, against the previous
year present): counts, verdict tiers, night shares, and sizes per year,
plus a closing line noting improvement, relapse, or no change in behavior.

### The wrapped kit

`--wrapped-kit DIR` scopes the hearing to the current year (or to `--year`
when given) and writes two files into DIR: `wrapped-YYYY.svg`, the year's
share card, and `post.txt` with two ready-to-post drafts built only from
the review's own numbers: a short post of at most 280 characters and a
standard post with the verdict, count, size, and top awards. If the year
has no screenshots, nothing is written and the exit code is 2.

### Failing a build on a verdict

`--fail-above TIER` exits 1 when the actual verdict ranks above TIER in the
tier order, and 0 otherwise. Tier names are case-insensitive and accept
spaces or hyphens (`"Repeat Offender"`, `repeat-offender`). An unknown tier
exits 2 and prints the valid tier list. The report (or JSON) is still
printed first, so the flag drops straight into CI.

### One line

`--oneline` prints exactly one line and nothing else:

```
Repeat Offender · 310 screenshots · 15.1 MB · Jan 2024 - Nov 2026
```

Card, CSV, and report files are still written when their flags are given.
With `--json`, JSON wins and `--oneline` is ignored. An empty folder prints
`No Evidence · 0 screenshots` and exits 2.

## GitHub Action

Snap Judgment ships as a composite GitHub Action. It installs the package
from this repo, judges a folder, and appends the Markdown verdict to the
workflow step summary:

```yaml
- uses: littlestjames82-sys/snap-judgment@v0.5.0
  with:
    path: ./screenshots
    judge: fair          # optional: strict | fair | lenient
    fail-above: "Repeat Offender"  # optional: fail the step above this tier
```

Inputs: `path` (required), `judge` (default `fair`), `fail-above`
(optional). The step's exit code is Snap Judgment's own, so `fail-above`
can fail the step.

Every hearing is entered into the **court record** at
`~/.snap-judgment/history.jsonl`. The next time a folder is judged, the
verdict reports the last hearing's count and whether the collection is up
or down since. `snapjudgment --history` prints the full record.

## The verdicts

| Screenshots | Verdict |
|---|---|
| 0 | No Evidence |
| 1-49 | Innocent Bystander |
| 50-199 | Petty Offender |
| 200-499 | Repeat Offender |
| 500-999 | Habitual Hoarder |
| 1,000-2,499 | Felony-Level Archivist |
| 2,500+ | Unrepentant Screenshot Felon |

## The awards

- **Night Owl** - a quarter or more of your screenshots happen between 10 PM and 5 AM
- **The 3 AM Club** - 10 or more screenshots taken between 2 AM and 4 AM
- **Burst Mode** - 8 or more screenshots inside a single 60-second window
- **The Archivist** - your oldest screenshot is at least 3 years old
- **Receipt Keeper** - half or more of your shots are weekday 9-to-5 evidence
- **Repeat Customer** - 10 or more files are exact size-and-dimension duplicates of a sibling
- **Creature of Habit** - a quarter or more of your screenshots fall in a single hour of the day
- **Gigabyte Club** - your screenshots total a gigabyte or more of evidence
- **Time Traveler** - the collection spans 4 or more years
- **Lifer** - the collection spans 8 or more years
- **Anniversary** - one month-day accounts for 15 or more screenshots across the years
- **Screenshot Streak** - 7 or more consecutive days with at least one screenshot
- **Clean Stretch** - 30 or more consecutive days without a single screenshot; a recorded moment of restraint
- **Minimalist** - under 50 screenshots total; the court's rare acquittal

## Sample output

From a synthetic demo folder of 310 screenshots (see `examples/` for the
full report, the HTML, SVG, and PNG share cards, a wrapped-kit example, and
the example judge pack):

```
THE VERDICT
  Repeat Offender
  310 screenshots, 15.1 MB of evidence,
  spanning Jan 5, 2024 to Nov 10, 2026.

THE EVIDENCE
  Total screenshots              310
  Total size                     15.1 MB (15,799,537 bytes)
  Taken at night (10 PM-5 AM)    91 (29.4%)
  Formats                        PNG 100%
  Screenshot season              Summer (Jun-Aug) - 31.0%
  Screenshot anniversary         Aug 14 - 14 screenshots across 2 years

THE CHARGES
  Desktop / landscape            157 (50.6%)
  Phone (tall)                   153 (49.4%)

EXHIBITS
  Exhibit A - Screenshot 2024-01-05 at 17.59.50.png (Jan 5, 2024)
  Exhibit B - Screenshot 2024-01-12 at 11.46.18.png (Jan 12, 2024)
  Exhibit C - Screenshot 2024-01-17 at 07.08.34.png (Jan 17, 2024)
  Exhibit D - Screenshot 2024-01-21 at 22.29.11.png (Jan 21, 2024)
  Exhibit E - Screenshot 2024-01-22 at 14.04.37.png (Jan 22, 2024)

AWARDS
  * Night Owl - 91 screenshots between 10 PM and 5 AM (29.4% of the total).
  * The 3 AM Club - 32 screenshots taken between 2 AM and 4 AM, when nothing on screen can be urgent.
  * Burst Mode - 12 screenshots inside a single 60-second window.
  * Repeat Customer - 12 files share an exact size and dimensions with a sibling. The same screenshot, taken again.
  * Screenshot Streak - 10 consecutive days with at least one screenshot.

THE SENTENCE
  Sentence: probation. The screenshots folder gets a check-in once a month.
```

## Privacy

Snap Judgment works from **file metadata only**. It reads file names, sizes,
modification times, and - solely to learn an image's width and height - the
header bytes of the image file. It never decodes image pixels, never runs
OCR, and never uploads anything anywhere. What your screenshots show is your
business; how many there are is the court's.

## License

MIT. Copyright Ryan Cotten / Ghost Developer Studio.
