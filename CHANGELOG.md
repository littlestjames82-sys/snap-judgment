# Changelog

All notable changes to Snap Judgment are recorded here. Versions are the
tool's own; the record is the record. Every version below shipped on
November 19, 2026 - it was that kind of day in court.

## 0.5.0 - 2026-11-19

- `--png FILE`: the share card as a real 1200x630 PNG, drawn by a tiny
  5x7 bitmap-font renderer in the standard library alone. Same input,
  same bytes, every run.
- A composite GitHub Action (`action.yml`): install from the repo, judge
  a folder, append the Markdown verdict to the step summary, and let
  `fail-above` fail the step.
- `--judge-file FILE.json`: custom judge packs. A pack carries a name, an
  intro (with `{tier}` and `{count}` placeholders), per-tier sentences,
  and an award lead; tiers a pack omits fall back to the fair judge.
  The Poet ships as the example at `examples/judge-poet.json`, and the
  format is documented and frozen as v1 in `judges/README.md`.
- `--html FILE`: a full self-contained HTML report (evidence table,
  per-year bars, exhibits, charges, awards, sentence), inline CSS only.
- New evidence: silent months (whole calendar months inside the span
  with zero screenshots), median screenshots per active day, and the
  screenshot anniversary (the month-day shot most, summed across years).
- Two new awards: Anniversary and Creature of Habit. Fourteen total.
- `--oneline`: exactly one summary line - verdict, count, size, span.
  Card, CSV, and report files are still written when their flags are
  given; `--json` wins when both are passed.

## 0.4.0 - 2026-11-19

- EXHIBITS: with 5 or more screenshots, the five oldest are entered into
  evidence as Exhibit A through Exhibit E, in the terminal report, the
  Markdown report, and JSON.
- `--wrapped-kit DIR`: a share kit for the year. Writes the year's SVG
  card and a `post.txt` with two ready-to-post drafts (a short one of at
  most 280 characters, a standard one), built from the review's own
  numbers only. Implies `--year` for the current year when none is given.
- `--vs-year YYYY`: one year tried against the most recent year in the
  data (or the previous year, when the target is the most recent), with
  a closing line noting improvement, relapse, or no change.
- New evidence: a Formats line (per-extension share) and your screenshot
  season (the meteorological quarter you shoot most).
- Per-folder breakdown: when several folders are judged in one run, each
  is listed with its screenshot count.
- Two new awards: The 3 AM Club and Lifer.
- `--fail-above TIER`: exit 1 when the verdict ranks above the given
  tier, so a hearing can fail a CI build. Tier names are
  case-insensitive and accept spaces or hyphens.
- Markdown reports draw the per-year counts as ASCII bars.

## 0.3.0 - 2026-11-19

- `--vs FOLDER`: co-defendant mode. Two folders are judged separately
  and compared - verdicts, counts, sizes, night shares, oldest evidence -
  with a closing line naming the bigger offender.
- Three presiding judges: `--judge strict` (The Hanging Judge),
  `fair` (the default), and `lenient` (The Sympathetic Judge). The voice
  changes the wording of intros, sentences, and award framing only;
  the math never changes.
- THE CHARGES: with 10 or more screenshots, a census of where they came
  from, from metadata hints only. Android filenames carrying an app
  package are credited to the app (WhatsApp, Telegram, Instagram, and
  friends); everything else is classified by the form factor its
  dimensions suggest.
- New evidence: evidence retention (how much of the collection is over
  a year old) and weekend share.
- `--csv FILE`: per-file records for every judged screenshot.
- The court record: every hearing is appended to
  `~/.snap-judgment/history.jsonl`, repeat hearings report the change
  since the last one, and `--history` prints the full record.

## 0.2.0 - 2026-11-19

- `--svg FILE`: the share card as a self-contained 1200x630 SVG.
- Richer evidence: duplicate waste (the bytes held by duplicate
  suspects beyond one copy), longest clean stretch (consecutive days
  with no screenshots, with its dates), busiest month, screenshots per
  day, and the largest single screenshot.
- Two new awards: Clean Stretch and Gigabyte Club.
- `--year YYYY`: judge one single year only.
- Unreadable files (broken symlinks, locked files, files deleted
  mid-scan) are counted and reported as skipped, never fatal.
- `~/OneDrive/Pictures/Screenshots` joins the usual places checked when
  no folder is passed.

## 0.1.0 - 2026-11-19

- The first hearing. Snap Judgment reads one or more folders of
  screenshots and issues a courtroom-style verdict computed from file
  metadata only: names, sizes, modification times, and - solely for
  width and height - image header bytes. Pixels are never decoded.
- Seven verdict tiers, from Innocent Bystander (under 50 screenshots)
  to Unrepentant Screenshot Felon (2,500+), plus No Evidence for an
  empty folder.
- The evidence: total count and size, date span, oldest screenshot,
  busiest day and hour, night share (10 PM-5 AM), longest daily streak,
  biggest 60-second burst, duplicate suspects, screen recordings, and
  device eras told by screen resolutions.
- Eight awards: Night Owl, Burst Mode, The Archivist, Receipt Keeper,
  Repeat Customer, Time Traveler, Screenshot Streak, and Minimalist.
- Outputs: the terminal report, `--json`, an HTML share card (`--card`),
  and a Markdown report (`--out`). `--since YYYY` narrows the record,
  `--all` judges every image in a folder, and with no folder argument
  the usual places are checked in order.
- Exit codes: 0 for a hearing held, 2 for plain-language errors and
  empty folders.
