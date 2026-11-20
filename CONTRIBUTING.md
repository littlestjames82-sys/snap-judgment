# Contributing to Snap Judgment

Snap Judgment is one Python file of plain arithmetic over file metadata,
plus tests. Contributions that keep it that way are welcome.

## Ground rules

- **Zero dependencies.** The standard library only, for the tool and for
  the build tooling in `tools/`. If a feature needs a package, it does
  not ship. The PNG share card is drawn by a hand-rolled bitmap font for
  exactly this reason.
- **Metadata only.** The tool reads file names, sizes, modification
  times, and image header bytes (for width and height). It never decodes
  pixels, never runs OCR, and never uploads anything. New features must
  be computable from metadata alone.
- **Evidence never changes.** Judges, packs, and jokes may change tone;
  counts, awards, and findings come from the record and nothing else.
- **Deterministic artifacts.** Cards, reports, and gallery files are
  generated from the record and must reproduce byte-for-byte on a
  re-run. Anything time-sensitive takes an explicit `now`, never a
  hidden wall clock.

## Dev setup

```
git clone https://github.com/littlestjames82-sys/snap-judgment
cd snap-judgment
python -m unittest discover -s tests
```

Python 3.9+ is the whole toolchain. Tests build synthetic screenshot
folders with known-answer metadata: minimal PNG/JPEG/GIF/WebP headers
written byte by byte, and modification times set with `os.utime`.
Weekday- and hour-sensitive fixtures must pin their timestamps (a fixed
offset lands on different weekdays depending on the run date).

## Good first contributions

- **Judge packs.** One JSON file in `judges/`, format documented and
  frozen as v1 in [judges/README.md](judges/README.md). Funny because
  the evidence is real, never mean about a real person.
- **Gallery defendants.** `tools/make_gallery.py` generates the
  synthetic defendants in `gallery/` from fixed seeds; propose new
  personalities in an issue first so the dock stays characterful.
- **Detection fixes.** Found a screenshot naming scheme the court misses
  (a new phone, a new OS, a new capture tool)? Those are the
  highest-value bug reports this project gets. Include the exact
  filename pattern.

## Regenerating the gallery and demo

```
python tools/make_gallery.py    # gallery/<slug>.md + <slug>.png + README
python tools/make_demo_gif.py   # examples/demo.gif
```

Both are deterministic: fixed seeds, fixed timestamps, UTC throughout.
Re-running them on a clean checkout reproduces the committed files.

## Pull requests

Run the full suite on the oldest Python you have access to as well as
your own; CI covers 3.9, 3.10, and 3.12 on Linux, Windows, and macOS.
Keep the diff to one idea. The changelog gets an entry under the newest
version heading.
