# Changelog

All notable changes to Snap Judgment are recorded here. Versions are the
tool's own; the record is the record. Every version below shipped on
November 19, 2026 - it was that kind of day in court.

## 0.14.0 - 2026-11-22

- The appeals court: `--appeal` re-hears the verdict on its
  factors. Every statistic the trial court already computed is
  entered as a mitigating factor (a daylight record, no duplicate
  waste, a long silence in the record, a measured pace, little
  ancient evidence) or an aggravating factor (a night habit,
  3 AM activity, duplicate waste, burst behavior, a relentless
  streak, a single-day haul), each with its evidence. The panel
  rules by a stated rule, never by vibes: mitigating factors
  outnumbering aggravating factors by three or more grants the
  appeal and reduces the verdict by exactly one rung on the
  ladder (never below Innocent Bystander, where the verdict
  stands and the sentence is commuted instead); outnumbering
  them at all commutes the sentence; otherwise the appeal is
  denied and both stand. The original verdict and sentence are
  always reported beside the appealed ones, in the terminal
  report, as an `appeal` object in `--json`, and as an
  `## Appeals Court` section in the Markdown report, and the
  closing states the factor counts the ruling rests on. The
  appeal composes with `--judge` and `--intensity` as usual.
  It refuses `--vs`, `--vs-year`, `--rematch`, `--plea-bargain`,
  `--cleanup-list`, `--wrapped-kit`, `--trend`, `--rehab`, and
  `--grand-jury` with a plain error: those are different
  proceedings, and a verdict is re-heard on its own record, not
  on a comparison or a projection. The downloads docket refuses
  `--appeal` with the same plain error; an appeal is a
  screenshots-court proceeding. An appeal re-hears the case; it
  does not rewrite the record, and the verdict in the court
  record and the report header remains the original.

## 0.13.0 - 2026-11-22

- The rehabilitation plan: `--rehab` turns the sentence into a
  staged cleanup plan, served in order. The screenshots are
  partitioned into three steps and every screenshot is claimed by
  at most one step: duplicate extras first (every suspect beyond
  one keeper per exact size-and-dimensions group), then ancient
  evidence (screenshots older than two years, measured from the
  newest screenshot, that step 1 did not already claim - the plea
  bargain counts an old duplicate as old; the plan never assigns
  the same file twice), then the heavy evidence (the ten largest
  screenshots still standing, assigned only when more than ten
  would remain). Each step reports its files and bytes and the
  cumulative position after serving it: the remaining count and
  the projected verdict at that point, so the plan shows the
  verdict improving stage by stage, not just at the end. The plan
  closes with the totals: files assigned, space reclaimed and its
  share of the evidence, the projected verdict, an estimated
  review time (five seconds per file, rounded up to minutes), a
  session schedule (at most fifty files per session), and the
  court's outlook: no rehabilitation required when nothing is
  assigned, rehabilitable when finishing the plan improves the
  verdict, and space-but-not-standing when it would not. A plan
  with nothing to clear assigns no steps. `--json` adds a
  `rehabilitation` object with the steps, totals, and outlook,
  the Markdown report gains a `## Rehabilitation Plan` section,
  and the plan composes with `--judge` and `--intensity` as
  usual. It refuses `--vs`, `--vs-year`, `--rematch`,
  `--plea-bargain`, `--cleanup-list`, `--wrapped-kit`, `--trend`,
  and `--grand-jury` with a plain error: those are different
  proceedings, and the plea bargain in particular is the same
  evidence tried as a single hypothetical rather than a served
  plan. The downloads docket refuses `--rehab` with the same
  plain error; the plan is a screenshots-court sentence. Nothing
  is deleted, moved, or modified: the court assigns the plan,
  the defendant serves it.

## 0.12.0 - 2026-11-20

- The trend: `--trend` records the current hearing first, then
  reports the court record for the same folders and court instead
  of the full report: the hearing count, the first and latest
  counts with the signed delta and percent, the first and latest
  verdicts, and a sparkline of the counts across every hearing,
  scaled from the smallest hearing to the largest. A first hearing
  says so, gives today's numbers, and draws its single bar at full
  height. `--json` adds a `trend` object beside the usual report,
  the Markdown report gains a `## Trend` section with the sparkline
  in a code block, and the section composes with `--judge` and
  `--intensity` as usual. The trend refuses `--vs`, `--vs-year`,
  `--rematch`, `--grand-jury`, `--plea-bargain`, and
  `--wrapped-kit` with a plain error: a trend is one folder set's
  own history in one court, and those are different proceedings.
- Co-defendants in the docket: `--vs FOLDER` now works in the
  downloads court. Both folders are judged by the docket and
  compared in one block: both verdicts, the file counts and total
  sizes with the signed delta between them, the per-folder award
  counts, and a closing line that names the bigger offender and
  states the ground it rests on: tier severity on the docket's
  ladder first, then count, then total size, with a dead heat
  declared when all three are level. `--json` nests both downloads
  objects under `versus` with `worse_offender`, the same shape the
  screenshots court uses, and the Markdown report gains the
  comparison table. `--vs-year` stays a screenshots-court
  proceeding and is still refused in the docket.

## 0.11.0 - 2026-11-20

- The Grand Jury: `--grand-jury` convenes both courts in one hearing.
  Pass both folders (screenshots first, downloads second) or neither
  (the screenshots court looks in the usual places, the docket sits
  on `~/Downloads`); one folder alone is a plain error. Each court
  issues its compact verdict block, and a FULL DOCKET section closes
  the hearing with the combined file count and size, both verdicts,
  and a standing line in the presiding judge's voice keyed to the
  pair of severities: an acquittal for two tidy folders, the harshest
  line for two crime scenes, and a split verdict naming the worse
  folder in between. A court with a missing or empty folder reports
  No Evidence and the combined report still issues (exit 2 only when
  neither court has evidence). `--judge` and `--intensity` apply to
  both courts, `--fail-above` is resolved per court against that
  court's own ladder, `--json` nests both courts' usual objects
  under a `combined` summary, `--png` / `--svg` / `--card` render a
  FULL DOCKET share card, and `--out` mirrors the hearing in
  Markdown. Both hearings enter the court record as normal entries,
  so each court's rematch keeps working. Single-court procedure
  (`--vs`, `--rematch`, the plea bargain, the cleanup order, the
  wrapped kit, date scopes, `--history`, judge packs, `--csv`, the
  badge, and the HTML report) is refused with a plain error.
- The downloads cleanup order: `--cleanup-list` now works in the
  downloads court too, listing only the provable waste, the
  duplicate-download extra copies (every group member except the
  keeper), in the same format as the screenshots court's order.
  Nothing is ever deleted.
- The recursive docket: `--recursive` lets the downloads court judge
  subfolders too. The evidence gains a per-folder breakdown (also
  the `folders` list in JSON), and duplicate-download grouping stays
  within each containing folder, so a setup.exe in two different
  folders is not a duplicate chain. The default stays on the surface.

## 0.10.0 - 2026-11-20

- Full citizenship for the Downloads Docket. The docket now takes the
  share cards (`--card`, `--svg`, `--png`, all headed THE DOWNLOADS
  DOCKET, with duplicate waste as the headline stat when there is any
  and the installer count otherwise), the verdict badge (`--badge`,
  colored by the docket's own severity ladder), the full HTML report
  (`--html`), a rematch against the most recent downloads hearing in
  the court record (`--rematch`, reporting count and size deltas and
  tier movement), `--year` scoping, a Wrapped kit in docket wording
  (`--wrapped-kit`), per-file records (`--csv`: name, size_bytes,
  modified, extension, and a category of installer, archive, or
  other), and the `--since` / `--until` date filters. Only `--vs`,
  `--vs-year`, `--plea-bargain`, `--cleanup-list`, and `--judge-file`
  are still refused in this court, with the same plain error.

## 0.9.0 - 2026-11-19

- The Downloads Docket: `--court downloads` convenes a second court
  for the downloads folder (the positional folder, or `~/Downloads`
  when none is given). It judges every file on the surface, any type,
  non-recursively, from names, sizes, and dates only: total files and
  size, installers and archives, duplicate downloads (the `setup.exe`
  / `setup (1).exe` chains), the oldest and largest files, an
  extension census, and the share over a year old. Its verdict tiers
  run from Tidy Desk to Superfund Site, and it keeps its own awards:
  Installer Archaeologist, Zip Hoarder, Double Downloader, Ancient
  Relic, Gigabyte Landfill, Still Seeding, Receipt Keeper DX, and
  Time Capsule. Terminal, `--json` (now carrying a `court` field in
  both courts), `--out`, `--oneline`, `--fail-above`, `--judge`,
  `--intensity` (with downloads-specific commentary), and the court
  record all work; the screenshots court's card, comparison, plea,
  and date-filter options are refused with a plain error. `--court`
  can also live in the config file.
- Award packs: a judge pack may now carry an `awards` object that
  renames awards by their stable keys, overriding the display name,
  the evidence line (a template filled with the award's own stat
  values), or both. Unknown keys are warned about and ignored. Every
  award in JSON output now carries its stable `key`. A new pack,
  `judges/judge-critic.json` (The Critic), demonstrates the feature.
- The cleanup order: `--cleanup-list FILE` writes the exact files a
  plea bargain would remove (duplicate extras plus screenshots older
  than `--plea-years`), one path per line under a `#` header, and
  reports the order in the terminal report and as a `cleanup_order`
  object in JSON. The tool still deletes nothing; the court does not
  swing the axe itself.

## 0.8.0 - 2026-11-19

- `--intensity LEVEL`: the court adds a line of commentary after the
  sentence, flavored by a roast intensity given as 0-100 or by band
  name (`gentle`, `kind`, `honest`, `spicy`, `roast`; `full roast`
  works too). The commentary aims at the defendant's most damning
  stat: night share of 40% or more first, then a count of 1,000 or
  more, then 10 or more duplicate extras, then a streak of 14 days or
  more, then an oldest screenshot 5 years old or more. It appears in
  the terminal report (a `COMMENTARY:` line), in JSON (a `commentary`
  key), and in the Markdown and HTML reports; the share cards, badge,
  CSV, and one-line summary are unchanged. Commentary is opt-in:
  without the flag every output is exactly as it was in 0.7.0, and the
  intensity never changes the verdict, the counts, the awards, or the
  plea math - wording only. It composes with every judge, built-in or
  loaded from a pack, and can also be set as `intensity` in
  `.snapjudgment.toml`. Ported from the browser demo's roast slider.

## 0.7.0 - 2026-11-19

- Configuration file: a `.snapjudgment.toml` in the current directory
  (or, when exactly one folder is given, inside that folder; the
  current directory wins) can supply `folders`, `judge`, `judge_file`,
  `plea_years`, `fail_above`, and `all`. Command-line flags always
  override the file. Parsing uses `tomllib` where it exists
  (Python 3.11+) and a small built-in parser for the flat subset of
  TOML on 3.9/3.10. A config file that cannot be parsed, names an
  unknown judge or tier, or points at a judge pack that does not load
  is warned about on stderr and ignored as a whole - a config file
  must never break a run.
- Shell completions: `completions/snapjudgment.bash` and
  `completions/snapjudgment.zsh` complete every flag (with judge
  voices and verdict tiers as values where they belong). A test keeps
  the bash file honest against `--help`, so the two cannot drift.
- The gallery is regenerated with the current report format: heatmaps
  and the newer evidence lines are in, and The Hoarder's span award
  now reads The Long Haul, as the 0.6.0 rename intended. No verdict
  changed; the defendants' records stand.

## 0.6.0 - 2026-11-19

- `--plea-bargain`: the court hears a plea. It computes the hypothetical
  outcome if the defendant deleted every duplicate suspect beyond one
  keeper per group and every screenshot older than `--plea-years N`
  (default 2, measured from the newest screenshot): files removed, space
  reclaimed, and the projected verdict, with the court's answer -
  accepted, or noted as changing nothing. Purely hypothetical; the tool
  remains read-only and deletes nothing, ever.
- `--rematch`: this hearing is compared against the most recent court
  record for the same folders - the count delta since the last hearing
  and whether the verdict has improved, worsened, or held.
- Screenshot heatmap: an hour-by-weekday grid of the whole collection,
  rendered in the Markdown report (shade blocks) and the HTML report
  (a shaded table), and exposed in JSON as a 7x24 grid.
- `--since` and `--until` now take full dates (YYYY-MM-DD, inclusive on
  both ends) and compose with `--year`; a bare year still works, as it
  did before. The active filter is entered into the evidence.
- `--badge FILE.svg`: a shields-style verdict badge for READMEs - dark
  "snap judgment" segment, tier segment colored by severity.
- New evidence: the busiest hour window, the fullest rolling 60 minutes
  anywhere in the record (distinct from both the calendar busiest hour
  and the 60-second burst).
- Two new awards: Time Traveler (a screenshot dated in the future; the
  court admits it anyway) and Ancient Relic (the oldest evidence sits
  ten or more years before the newest). The span award formerly called
  Time Traveler is renamed The Long Haul - sixteen awards in total.
- More filename locales judged on sight: French ("Capture d'écran"),
  Spanish ("Captura de pantalla"), Japanese, and Polish ("Zrzut ekranu").
- The share cards put the word out: the HTML, SVG, and PNG cards now
  carry a small "pip install snap-judgment" line under the footer, so a
  posted verdict tells its viewers how to get judged themselves.

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
