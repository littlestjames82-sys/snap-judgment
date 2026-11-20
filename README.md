# Snap Judgment

**Your screenshots, reviewed.** Snap Judgment reads a folder of screenshots and issues a courtroom-style verdict on your screenshot habits: a tier, the evidence, awards, and a sentence.

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
snapjudgment ~/Downloads --all           # judge every image in the folder
snapjudgment ~/Pictures --since 2024     # only screenshots from 2024 onward
snapjudgment ~/Pictures --year 2026      # judge one single year only
snapjudgment ~/Pictures --vs ~/Phone     # co-defendant mode: compare two folders
snapjudgment ~/Pictures --judge strict   # strict | fair | lenient presiding judge
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
day, longest daily streak, longest clean stretch, biggest 60-second burst,
duplicate suspects and the storage they waste, and the device eras told by
your screen resolutions. Files the court cannot read (broken symlinks,
locked files) are counted and reported, never fatal.

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
- **Burst Mode** - 8 or more screenshots inside a single 60-second window
- **The Archivist** - your oldest screenshot is at least 3 years old
- **Receipt Keeper** - half or more of your shots are weekday 9-to-5 evidence
- **Repeat Customer** - 10 or more files are exact size-and-dimension duplicates of a sibling
- **Gigabyte Club** - your screenshots total a gigabyte or more of evidence
- **Time Traveler** - the collection spans 4 or more years
- **Screenshot Streak** - 7 or more consecutive days with at least one screenshot
- **Clean Stretch** - 30 or more consecutive days without a single screenshot; a recorded moment of restraint
- **Minimalist** - under 50 screenshots total; the court's rare acquittal

## Sample output

From a synthetic demo folder of 310 screenshots (see `examples/` for the
full report and a sample share card):

```
THE VERDICT
  Repeat Offender
  310 screenshots, 15.1 MB of evidence,
  spanning Jan 5, 2024 to Nov 10, 2026.

THE CHARGES
  Desktop / landscape            157 (50.6%)
  Phone (tall)                   153 (49.4%)

AWARDS
  * Night Owl - 91 screenshots between 10 PM and 5 AM (29.4% of the total).
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
