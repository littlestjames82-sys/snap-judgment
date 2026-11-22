# Snap Judgment

[![PyPI](https://img.shields.io/pypi/v/snap-judgment)](https://pypi.org/project/snap-judgment/)
[![CI](https://github.com/littlestjames82-sys/snap-judgment/actions/workflows/ci.yml/badge.svg)](https://github.com/littlestjames82-sys/snap-judgment/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/github/license/littlestjames82-sys/snap-judgment)](LICENSE)

**Your screenshots, reviewed.** Snap Judgment reads a folder of screenshots and issues a courtroom-style verdict on your screenshot habits: a tier, the evidence, awards, and a sentence.

![Snap Judgment in action: a folder is judged, verdict delivered](examples/demo.gif)

Meet [the defendants](gallery/README.md): five synthetic screenshot collections, from The Minimalist (35 screenshots, acquitted) to The Hoarder (2,800, Unrepentant Screenshot Felon), judged and entered into the record. New presiding voices live in [judges/](judges/README.md), and the version record is kept in the [changelog](CHANGELOG.md).

> "Clever little bastard. Guilty of being fun."
>
> ChatGPT, reviewing the tool in November 2026, put Snap Judgment itself on trial in its own Court of Screenshot Affairs and delivered that verdict. The full review is entered into the record in the site's [Reviewed by a higher court](https://littlestjames82-sys.github.io/snap-judgment/) section.

## Two courts and a grand jury

The screenshots court is not the only chamber. `--court downloads` convenes
the **Downloads Docket**, a second court that judges your Downloads folder
from names, sizes, and dates only: installers and archives, duplicate
download chains like `setup (2).exe`, the oldest file in the pile, and a
verdict ladder of its own, from Tidy Desk to Superfund Site. A sample
hearing is at [`examples/downloads-demo-output.txt`](examples/downloads-demo-output.txt),
and the full docket is documented under [The downloads docket](#the-downloads-docket)
below. `--grand-jury` then convenes both courts in one hearing and closes
with a combined FULL DOCKET ruling: both verdicts, the combined evidence,
and a standing line keyed to the pair. See [The grand jury](#the-grand-jury)
below.

New in v0.16.0:

- Downloads parity for `--plea-bargain`: the Downloads Docket now hears the last proceeding it used to refuse. Its plea projects the verdict if you deleted the duplicate-download extra copies and every file older than `--plea-years` years (default 2), measured from the newest file, on the docket's own ladder. With this, the docket hears every proceeding the screenshots court hears except the co-defendant comparison by year and judge packs. See [The downloads docket](#the-downloads-docket) below.

New in v0.15.0:

- Downloads parity for `--rehab` and `--appeal`: the Downloads Docket now hears the two newest proceedings on its own evidence. Its rehabilitation plan clears duplicate extras, then ancient installers, then the heavy evidence, with projected verdicts on the docket's own ladder; its appeals court re-hears the verdict on docket factors (installer restraint and a light load against duplicate waste, installer pileups, ancient relics, archive hoards, gigabyte landfills, and time capsules) under the same three-factor rule. See [The downloads docket](#the-downloads-docket) below.

New in v0.14.0:

- `--appeal`: the appeals court. The verdict is re-heard on its mitigating and aggravating factors, each with its evidence, and ruled on by a stated rule: mitigating factors outnumbering aggravating factors by three or more reduces the verdict by one rung, outnumbering them at all commutes the sentence, and anything else is denied. The original verdict always stays in the record beside the appealed one. See [The appeals court](#the-appeals-court) below.

New in v0.13.0:

- `--rehab`: the rehabilitation plan. A staged cleanup plan (duplicate extras, then ancient evidence, then the largest remaining screenshots) with the space each step reclaims, the projected verdict after each step, a review-time estimate, and a session schedule. Nothing is deleted; the court assigns the plan, you serve it. See [The rehabilitation plan](#the-rehabilitation-plan) below.

## Install

```
pip install snap-judgment
```

Or keep the tool in its own environment with [pipx](https://pipx.pypa.io/):

```
pipx install snap-judgment
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
snapjudgment ~/Pictures --since 2024-01-01  # only screenshots on or after a date (a bare year works too)
snapjudgment ~/Pictures --until 2026-06-30  # only screenshots on or before a date
snapjudgment ~/Pictures --year 2026      # judge one single year only
snapjudgment ~/Pictures --vs ~/Phone     # co-defendant mode: compare two folders
snapjudgment ~/Pictures --vs-year 2026   # compare one year against another year in the data
snapjudgment ~/Pictures --judge strict   # strict | fair | lenient presiding judge
snapjudgment ~/Pictures --judge-file poet.json  # a custom judge voice from a JSON pack
snapjudgment ~/Pictures --intensity roast  # court commentary at full roast (0-100 or gentle|kind|honest|spicy|roast)
snapjudgment ~/Pictures --wrapped-kit ./kit     # year-scoped SVG card + ready-to-post drafts
snapjudgment ~/Downloads --court downloads  # the downloads docket: a second court, for the downloads folder
snapjudgment --grand-jury ~/Pictures/Screenshots ~/Downloads  # both courts, one hearing, a combined FULL DOCKET verdict
snapjudgment ~/Downloads --court downloads --recursive  # the docket judges subfolders too
snapjudgment ~/Pictures --cleanup-list cleanup.txt  # the cleanup order: the exact files a plea bargain would remove
snapjudgment ~/Pictures --plea-bargain   # the hypothetical verdict if you deleted the old and the duplicated
snapjudgment ~/Pictures --rematch        # compare this hearing with the last one on record
snapjudgment ~/Pictures --rehab          # the rehabilitation plan: a staged cleanup plan with a projected verdict per step
snapjudgment ~/Pictures --appeal         # the appeals court: the verdict re-heard on its mitigating and aggravating factors
snapjudgment ~/Downloads --court downloads --rehab   # the docket's rehabilitation plan: duplicate extras, ancient installers, heavy evidence
snapjudgment ~/Downloads --court downloads --appeal  # the docket's appeals court: the verdict re-heard on the docket's own factors
snapjudgment ~/Pictures --badge badge.svg  # a shields-style verdict badge for your README
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
biggest 60-second burst, the busiest hour window (the fullest rolling 60
minutes anywhere in the record), duplicate suspects and the storage they
waste, the per-format share (PNG, JPG, HEIC...), your busiest screenshot
season, your screenshot anniversary (the month-day you shoot most, summed
across years), and the device eras told by your screen resolutions. The
Markdown and HTML reports also draw a screenshot heatmap: the whole
collection as an hour-by-weekday grid. `--since` and `--until` narrow the
record to a date range (inclusive, and they compose with `--year`); the
active filter is entered into the evidence. Files the court cannot
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

Add `--intensity` and the court appends a line of commentary after the
sentence, aimed at your most damning stat: night share first, then sheer
count, duplicate extras, your longest streak, the oldest evidence. The
intensity runs 0-100 or by name: gentle, kind, honest, spicy, roast. At
gentle the court simply notes that the defendant keeps late hours; at
full roast it finds the sleep schedule guilty too. Commentary is opt-in,
appears in the terminal report, JSON, and the Markdown and HTML reports,
and composes with any judge. Like the judge's voice, it changes the
wording only, never the math. It can also live in the config file as
`intensity`.

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

A pack may also carry an `awards` object that renames awards by their
stable keys (`night_owl`, `burst_mode`, `screenshot_streak`, and the
rest; every award in the JSON report now carries its `key`). Each entry
may override the display `name`, the `evidence` line, or both; the
evidence is a template filled with the award's own stat values, so
`"{night_count} hoots at {night_share:.1f}%."` renders with the real
numbers. Entries keyed by an unknown award are warned about and
ignored. **The Critic** (`judges/judge-critic.json`) shows the feature
off: a dry film-critic judge whose Night Owl is entered into the record
as *Nocturne in a Minor Key*.

### Configuration file

Options you reach for every time can live in a `.snapjudgment.toml`.
Snap Judgment looks for one in the current directory first; when exactly
one folder is given and the current directory has none, a config file
inside that folder applies. Command-line flags always override the file.

```toml
# .snapjudgment.toml
folders = ["~/Pictures/Screenshots"]
judge = "strict"
plea_years = 3
fail_above = "Repeat Offender"
all = false
```

The supported keys are `folders` (a list of paths, used when no folder
argument is given), `judge` (`strict` | `fair` | `lenient`),
`judge_file` (a custom judge pack, as with `--judge-file`),
`plea_years` (an integer), `fail_above` (a verdict tier name),
`all` (a boolean), `intensity` (an integer 0-100 or a band name,
as with `--intensity`), and `court` (`screenshots` | `downloads`). Python 3.11+ parses the file with `tomllib`; on
3.9/3.10 a small built-in parser reads the same flat subset of TOML
(top-level `key = value` lines). A config file that cannot be parsed,
names an unknown judge or tier, or points at a judge pack that does not
load is warned about on stderr and ignored as a whole: a config file
must never break a run.

### Shell completions

Completion scripts for bash and zsh live in
[`completions/`](completions/): they complete every flag, with judge
voices for `--judge` and verdict tiers for `--fail-above`. For bash,
copy `completions/snapjudgment.bash` into your bash-completion
directory (e.g. `~/.local/share/bash-completion/completions/snapjudgment`)
or source it from `~/.bashrc`. For zsh, copy
`completions/snapjudgment.zsh` as `_snapjudgment` into a directory on
your `$fpath` and run `compinit`.

### Year over year

`--vs-year 2026` compares one year against the most recent year present in
the data (or, when the target is the most recent year, against the previous
year present): counts, verdict tiers, night shares, and sizes per year,
plus a closing line noting improvement, relapse, or no change in behavior.

### The plea bargain

`--plea-bargain` asks what a cleanup would buy you, without touching a
single file. The court computes the hypothetical outcome if you deleted
(a) every duplicate suspect except one keeper per exact size-and-dimension
group, and (b) every screenshot older than `--plea-years N` years
(default 2), measured from the newest screenshot in the folder: how many
files would go, how much space would come back, and what verdict the
remainder would earn. If the projected tier beats the real one, the court
would accept the plea; if not, it notes for the record that the plea
changes nothing. It is a projection, labeled as such - Snap Judgment
never deletes, moves, or modifies anything it judges. The downloads
docket hears its own plea bargain on the same terms: the duplicate
extras the docket itself computes, plus every file older than
`--plea-years` years measured from the newest file, projected on
the docket's ladder.

`--cleanup-list FILE` turns the plea into paperwork: it writes the
exact files the plea bargain would remove (duplicate extras plus
screenshots older than `--plea-years`), one sorted path per line under
a `#` header giving the count and bytes, and enters a `cleanup_order`
object into the JSON report. It needs no `--plea-bargain` flag, and
when both are given they always agree, because they are the same
computation. The court issues the order; it does not swing the axe
itself. What you do with the list is your business.

### The rehabilitation plan

`--rehab` turns the sentence into homework: a staged cleanup plan,
served in order, that shows the verdict improving step by step
instead of only at the end. The screenshots are partitioned into
three steps, and every screenshot is claimed by at most one step:

1. **Clear the duplicate extras** - every duplicate suspect beyond
   one keeper per exact size-and-dimension group.
2. **Retire the ancient evidence** - screenshots older than two
   years, measured from the newest screenshot, that step 1 did not
   already claim. (The plea bargain counts an old duplicate as old;
   the plan never assigns the same file twice.)
3. **Move out the heavy evidence** - the ten largest screenshots
   still standing after the first two steps, assigned only when
   more than ten would remain.

Each step reports its files and bytes and the cumulative position
after serving it: how much evidence remains and what verdict the
remainder would earn. The plan closes with the totals (files
assigned, space reclaimed and its share of the evidence, the
projected verdict), an estimated review time at five seconds per
file, a session schedule of at most fifty files per session, and
the court's outlook: no rehabilitation required when nothing is
assigned, rehabilitable when finishing the plan improves the
verdict, and space-but-not-standing when it would not. The plan
appears in the terminal report, as a `rehabilitation` object in
`--json`, and as a section of the Markdown report, and it composes
with `--judge` and `--intensity`. It does not combine with the
comparison, plea, cleanup-order, wrapped-kit, trend, rematch, or
grand-jury proceedings; the refusals are plain errors. The
downloads docket hears its own rehabilitation plan, on its own
steps: duplicate extras, then ancient installers, then the heavy
evidence, projected on the docket's ladder. Like everything else
in this court, the plan is read-only: Snap Judgment assigns it
and deletes nothing.

### The appeals court

`--appeal` takes the verdict upstairs: the same evidence, re-heard
on its factors. Every statistic the trial court already computed
is entered as a mitigating factor or an aggravating factor, each
with its evidence:

- **Mitigating** - a daylight record (under 20% taken at night),
  no duplicate waste, shows restraint (a silence of 60 days or
  more in the record), a measured pace (a median of at most 2
  screenshots per active day), little ancient evidence (under
  10% over a year old).
- **Aggravating** - a night habit (40% or more taken at night),
  3 AM activity (5 or more screenshots between 2 and 4 AM),
  duplicate waste (10 or more duplicate suspects), burst
  behavior (8 or more screenshots inside a single minute), a
  relentless streak (14 or more consecutive days), a single-day
  haul (25 or more screenshots on the busiest day).

The panel rules by a stated rule, never by vibes. Mitigating
factors outnumbering aggravating factors by three or more grants
the appeal: the verdict is reduced by exactly one rung on the
ladder, and the sentence is commuted. (At Innocent Bystander
there is no lower rung, so the verdict stands and the sentence
is commuted instead.) Outnumbering them at all commutes the
sentence and leaves the verdict standing. Anything else is
denied, and both stand. The original verdict and sentence are
always reported beside the appealed ones, in the terminal
report, as an `appeal` object in `--json`, and as a section of
the Markdown report, and the proceeding composes with `--judge`
and `--intensity`. It does not combine with the comparison,
plea, cleanup-order, wrapped-kit, trend, rehabilitation,
rematch, or grand-jury proceedings; the refusals are plain
errors. The downloads docket hears its own appeals court, on
its own factors: installer restraint, few unopened archives, no
duplicate waste, little ancient evidence, and a light load
against duplicate waste, an installer pileup, an ancient relic,
an archive hoard, a gigabyte landfill, and a time capsule,
ruled by the same three-factor rule on the docket's ladder. An
appeal re-hears the case; it does not rewrite the record, so
the verdict in the report header and the court record remains
the original.

### The downloads docket

`--court downloads` convenes a second court for the folder where good
intentions go to be forgotten. It judges the folder given on the
command line, or `~/Downloads` when none is given, and it judges
*every* file on the surface, any type, non-recursively, from names,
sizes, and dates only. No file is ever opened.

The evidence is the downloads folder's own: total files and size,
installers (`.exe`, `.msi`, `.dmg`, `.pkg`, `.deb`, `.rpm`,
`.appimage`) and archives, duplicate downloads (the `setup.exe`,
`setup (1).exe`, `setup (2).exe` chains, counted by extra copies), the
oldest and largest files, an extension census, and the share of files
over a year old. The verdict tiers are the docket's own:

| Files | Verdict |
|---|---|
| 0 | No Evidence |
| 1-24 | Tidy Desk |
| 25-99 | Mild Clutter |
| 100-249 | Digital Junk Drawer |
| 250-499 | Landfill Adjacent |
| 500-999 | Hoarding Situation |
| 1,000+ | Superfund Site |

Its awards are its own too: **Installer Archaeologist** (10 or more
installers), **Zip Hoarder** (15 or more archives), **Double
Downloader** (5 or more duplicate extra copies), **Ancient Relic**
(the oldest file is at least 3 years old), **Gigabyte Landfill** (5 GB
or more in total), **Still Seeding** (a `.torrent` file), **Receipt
Keeper DX** (an invoice or receipt in a filename), and **Time
Capsule** (60% or more of the files are over a year old). The
terminal report, `--json`, `--out`, `--oneline`, `--fail-above`
(against the docket's tiers), `--judge`, `--intensity` (with
commentary written for downloads sins), the share cards (`--card`,
`--svg`, `--png`, headed THE DOWNLOADS DOCKET), `--badge` (colored by
the docket's own ladder), the full HTML report (`--html`),
`--rematch` against the last downloads hearing, `--year`,
`--wrapped-kit`, `--csv` (one row per file, categorized as installer,
archive, or other), the `--since` / `--until` date filters, and the
court record all work in this court. The cleanup order works here
too, listing only the provable waste: the duplicate-download extra
copies, every group member except the keeper. The rehabilitation
plan (`--rehab`) works here too, on the docket's own steps:
duplicate extras, then ancient installers (installers older than
two years, measured from the newest file), then the ten largest
files still standing, with projected verdicts on the docket's
ladder, a review-time estimate, and a session schedule. The
appeals court (`--appeal`) sits here too, re-hearing the verdict
on the docket's own factors: mitigating installer restraint (two
or fewer installers), few unopened archives (two or fewer), no
duplicate waste, little ancient evidence (under 10% over a year
old), and a light load (under 100 MB), against aggravating
duplicate waste (five or more extra copies), an installer pileup
(ten or more), an ancient relic (the oldest file at least three
years old), an archive hoard (fifteen or more), a gigabyte
landfill (5 GB or more), and a time capsule (60% or more over a
year old), ruled by the same three-factor rule as the screenshots
court, on the docket's ladder. The plea bargain (`--plea-bargain`)
is heard here too: the hypothetical verdict if the defendant
deleted the duplicate-download extra copies and every file older
than `--plea-years` years (default 2), measured from the newest
file, with a file that is both old and a duplicate extra counted
once, as old - projected on the docket's ladder, in the terminal
report, as a `plea_bargain` object in `--json`, and as a section
of the Markdown report. (The docket's cleanup order stays the
narrower instrument it has always been: it lists only the
duplicate extras, the provable waste, and is not the plea.) Only
the co-defendant comparison by year (`--vs-year`) and judge packs
(`--judge-file`) are refused with a plain error, because their
evidence is not the docket's. A sample hearing is at
`examples/downloads-demo-output.txt`. `--court` can also live in the
config file.

`--recursive` lets the docket judge subfolders too, not just the
surface. The evidence gains a per-folder breakdown (also the
`folders` list in JSON), and duplicate-download grouping stays
within each containing folder: a `setup.exe` in two different
folders is not a duplicate chain.

### The grand jury

`--grand-jury` convenes both courts in one hearing: the screenshots
court and the downloads docket, tried together, with a combined
verdict. Pass both folders (screenshots first, downloads second) or
pass neither, in which case the screenshots court looks in the usual
places and the docket sits on `~/Downloads`. One folder alone is a
plain error; the grand jury needs both defendants or none.

Each court issues its compact verdict block, and the hearing closes
with THE FULL DOCKET: the combined file count and size, both
verdicts side by side, and a standing line in the presiding judge's
voice, keyed to the pair of severities. Two tidy folders earn an
acquittal. Two crime scenes earn the harshest line the judge has.
Anything in between is a split verdict, and it names the worse
folder as the problem. A court whose folder is missing or empty
reports No Evidence, and the combined report still issues for the
other; the exit code is 2 only when neither court has evidence.

`--judge` and `--intensity` apply to both courts at once, and
`--fail-above` is resolved per court against that court's own
ladder, tripping if either verdict ranks above the tier. `--json`
nests each court's usual object under `screenshots` and `downloads`
keys, with a `combined` summary of files, bytes, and both tiers.
`--png`, `--svg`, and `--card` render a FULL DOCKET share card (both
verdicts, the combined size, and the first award from each court),
and `--out` mirrors the whole hearing in Markdown. Both hearings
enter the court record as normal entries, so each court's
`--rematch` keeps working afterward. Single-court procedure (the
comparisons, the plea bargain, the cleanup order, the wrapped kit,
date scopes, `--history`, judge packs, `--csv`, the badge, and the
HTML report) does not combine with a grand jury and is refused with
a plain error.

### Rematch

`--rematch` tries this hearing against the last one on record for the
same folders: the count delta since that hearing and whether the verdict
has improved, worsened, or held since. With no prior hearing for the
folders, the court says so. In JSON the comparison rides along as a
`rematch` object (or `null` when there is nothing to rematch against).

### A badge for your README

`--badge badge.svg` writes a shields-style verdict badge: a dark
"snap judgment" segment and the tier on a segment colored by severity,
green for Innocent Bystander through to dark red for Unrepentant
Screenshot Felon. Post your card, wear your verdict.

The share cards carry the word too: the HTML, SVG, and PNG cards print a
small `pip install snap-judgment` line under the footer, so anyone who
sees your verdict knows how to get one of their own.

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
or down since, and `--rematch` puts the two hearings side by side.
`snapjudgment --history` prints the full record.

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
- **Time Traveler** - at least one screenshot is dated in the future; the court admits it anyway
- **The Long Haul** - the collection spans 4 or more years
- **Lifer** - the collection spans 8 or more years
- **Ancient Relic** - the oldest screenshot sits 10 or more years before the newest
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
  Busiest hour window            12 screenshots in the hour from Aug 14, 2026 21:14
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

## FAQ

**Does it read my pictures?**
No. The screenshots court reads file names, sizes, modification times, and
the header bytes of each image, solely to learn its width and height. It
never decodes a pixel and never runs OCR. The Downloads Docket does not
open files at all: names, sizes, and dates only.

**Does it delete anything?**
Never. Snap Judgment only reports. The plea bargain computes what a
cleanup would do, and the cleanup order writes the list of files that
cleanup would remove, but the tool itself deletes, moves, and modifies
nothing it judges. The court does not swing the axe itself.

**Does anything leave my machine?**
No. There are no uploads, no accounts, and no telemetry. Beyond the
outputs you ask for, the only file it writes is the court record, a local
history at `~/.snap-judgment/history.jsonl`.

**Which platforms does it run on?**
Windows, macOS, and Linux, on Python 3.9 or newer, with zero
dependencies. The browser demo runs the same courts in your browser,
on your machine only.

**Can I use my own judge?**
Yes. Three judges ship built in (strict, fair, lenient), and
`--judge-file` seats a judge pack of your own: a small JSON file that
can rename the court's awards and rewrite their evidence. The Poet,
The Drill Sergeant, The Grandmother, and The Critic ship as examples
in `judges/`.

## License

MIT. Copyright Ryan Cotten / Ghost Developer Studio.
