#!/usr/bin/env python3
"""Generate the gallery: five synthetic defendants, formally judged.

Usage: python3 tools/make_gallery.py

Each defendant is a synthetic screenshot collection with a distinct
personality, generated from a fixed seed. The tool judges it in-process
and writes gallery/<slug>.md (the Markdown report) and gallery/<slug>.png
(the PNG share card), plus the gallery/README.md index.

Everything is deterministic: fixed seeds, timestamps derived from the
seeds (never the wall clock), UTC throughout (TZ is set before the tool
is imported), a fixed "now" for age-based findings, and a scratch HOME
so no court record is written or read. Re-running this script on a clean
checkout reproduces the committed gallery files byte-for-byte.

Standard library only, like everything else in this repo.
"""
import os
import time

os.environ["TZ"] = "UTC"
time.tzset()

import calendar
import random
import shutil
import struct
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_SCRATCH_HOME = tempfile.mkdtemp(prefix="snap-gallery-home-")
os.environ["HOME"] = _SCRATCH_HOME

import snap_judgment as sj  # noqa: E402

GALLERY = os.path.join(ROOT, "gallery")

# The court's clock for age-based findings (evidence retention, The
# Archivist). Fixed so the gallery never drifts with the calendar.
FIXED_NOW = datetime(2026, 11, 19, 12, 0, 0)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 19, 12, 0, 0)


sj.datetime = _FixedDateTime


def _chunk(tag, data):
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", __import__("zlib").crc32(tag + data) & 0xFFFFFFFF)
    )


def _png_header(width, height):
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")


def _epoch(dt):
    return calendar.timegm(dt.timetuple())


def write_collection(folder, records):
    """records: list of (datetime, (w, h), size). Writes sparse PNG files
    whose headers carry the dimensions and whose stat size is `size`."""
    os.makedirs(folder, exist_ok=True)
    used = set()
    for dt, dims, size in records:
        epoch = _epoch(dt)
        while True:
            name = "Screenshot {:%Y-%m-%d} at {:%H.%M.%S}.png".format(dt, dt)
            if name not in used:
                break
            epoch += 1
            dt = dt + timedelta(seconds=1)
        used.add(name)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(_png_header(*dims))
            fh.truncate(size)  # sparse: logical size without the disk cost
        os.utime(path, (epoch, epoch))


def _rand_dt(rng, start, end, hour_lo, hour_hi):
    days = (end - start).days
    day = start + timedelta(days=rng.randrange(days + 1))
    return day.replace(
        hour=rng.randrange(hour_lo, hour_hi),
        minute=rng.randrange(60),
        second=rng.randrange(60),
    )


# ---------------------------------------------------------------------------
# the defendants: each returns a list of (datetime, dims, size) records
# ---------------------------------------------------------------------------

def the_minimalist(rng):
    records = []
    start, end = datetime(2025, 9, 1), datetime(2026, 11, 1)
    for _ in range(35):
        dt = _rand_dt(rng, start, end, 9, 18)
        dims = (1920, 1080) if rng.random() < 0.7 else (1512, 982)
        records.append((dt, dims, rng.randrange(180_000, 650_000)))
    return records


def the_night_archivist(rng):
    records = []
    start, end = datetime(2023, 6, 1), datetime(2026, 11, 10)
    night_hours = [22, 23, 0, 1, 2, 3, 4]
    for _ in range(900):
        if rng.random() < 0.60:
            dt = _rand_dt(rng, start, end, 0, 1).replace(
                hour=rng.choice(night_hours))
        else:
            dt = _rand_dt(rng, start, end, 6, 22)
        dims = (1170, 2532) if rng.random() < 0.8 else (1290, 2796)
        records.append((dt, dims, rng.randrange(900_000, 3_200_000)))
    return records


def the_burst_photographer(rng):
    records = []
    start, end = datetime(2025, 1, 5), datetime(2026, 11, 10)
    for _ in range(150):
        dt = _rand_dt(rng, start, end, 0, 24)
        records.append((dt, (1080, 2400), rng.randrange(700_000, 2_400_000)))
    for _ in range(25):  # 25 bursts of 12, each inside one minute
        base = _rand_dt(rng, start, end, 0, 24).replace(second=0)
        for i in range(12):
            dt = base + timedelta(seconds=4 * i + rng.randrange(3))
            records.append((dt, (1080, 2400),
                            rng.randrange(700_000, 2_400_000)))
    return records


def the_hoarder(rng):
    records = []
    start, end = datetime(2020, 3, 1), datetime(2026, 11, 15)
    unique = []
    for _ in range(2380):
        dt = _rand_dt(rng, start, end, 0, 24)
        dims = (1080, 1920) if dt.year <= 2022 else (1170, 2532)
        size = rng.randrange(300_000, 900_000)
        unique.append((dt, dims, size))
        records.append((dt, dims, size))
    for dt, dims, size in rng.sample(unique, 420):  # exact duplicate copies
        copy_dt = _rand_dt(rng, start, end, 0, 24)
        records.append((copy_dt, dims, size))
    return records


def the_receipt_keeper(rng):
    records = []
    start, end = datetime(2025, 2, 3), datetime(2026, 11, 13)
    made = 0
    while made < 220:
        dt = _rand_dt(rng, start, end, 9, 17)
        if dt.weekday() >= 5:
            continue
        dims = (1920, 1080) if rng.random() < 0.2 else (1179, 2556)
        records.append((dt, dims, rng.randrange(150_000, 800_000)))
        made += 1
    return records


DEFENDANTS = [
    ("the-minimalist", "The Minimalist", 101, the_minimalist, "Minimalist",
     "Thirty-five screenshots in fourteen months, all in daylight. The "
     "court had to check the folder was the right one."),
    ("the-night-archivist", "The Night Archivist", 202, the_night_archivist,
     "Night Owl",
     "Nine hundred screenshots over three years, sixty percent of them "
     "between 10 PM and 5 AM. The archive keeps its own hours."),
    ("the-burst-photographer", "The Burst Photographer", 303,
     the_burst_photographer, "Burst Mode",
     "A moderate folder, except for the twenty-five occasions on which "
     "twelve screenshots happened inside a single minute."),
    ("the-hoarder", "The Hoarder", 404, the_hoarder, "Repeat Customer",
     "Six years, twenty-eight hundred screenshots, and hundreds of them "
     "taken twice. Deleting is not on the docket."),
    ("the-receipt-keeper", "The Receipt Keeper", 505, the_receipt_keeper,
     "Receipt Keeper",
     "Every screenshot a weekday, every one between nine and five. This "
     "folder is not a habit; it is a filing system."),
]


def judge(folder):
    skipped = [0]
    shots, recordings = sj.collect([folder], skipped=skipped)
    stats = sj.analyze(shots, recordings)
    stats["skipped"] = skipped[0]
    tier, sentence = sj.verdict_for(stats["count"])
    awards = sj.compute_awards(stats, now=FIXED_NOW)
    return stats, tier, sentence, awards


def main():
    os.makedirs(GALLERY, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="snap-gallery-")
    rows = []
    sections = []
    try:
        for slug, name, seed, builder, signature, blurb in DEFENDANTS:
            print(f"== {name}")
            folder = os.path.join(tmp_root, slug)
            write_collection(folder, builder(random.Random(seed)))
            stats, tier, sentence, awards = judge(folder)
            with open(os.path.join(GALLERY, slug + ".md"), "w",
                      encoding="utf-8") as fh:
                fh.write(sj.render_markdown(stats, tier, sentence, awards))
            with open(os.path.join(GALLERY, slug + ".png"), "wb") as fh:
                fh.write(sj.render_png(stats, tier, sentence, awards))
            award_names = [a["name"] for a in awards]
            if signature not in award_names:
                raise SystemExit(
                    f"{name}: signature award {signature!r} not earned "
                    f"(awards: {award_names})"
                )
            rows.append(
                f"| [{name}]({slug}.md) | {stats['count']:,} | "
                f"{stats['total_human']} | {tier} | {signature} |"
            )
            sections.append(
                f"## {name}\n\n{blurb}\n\n"
                f"![{name} share card]({slug}.png)\n\n"
                f"[Full report]({slug}.md) - {stats['count']:,} screenshots, "
                f"{stats['total_human']}, verdict **{tier}**.\n"
            )
            print(f"   {stats['count']} screenshots, {tier}, "
                  f"awards: {', '.join(award_names)}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        shutil.rmtree(_SCRATCH_HOME, ignore_errors=True)

    index = [
        "# The Defendants: five screenshot collections, formally judged",
        "",
        "Every defendant below is a synthetic screenshot collection with a",
        "fixed personality, generated by `tools/make_gallery.py` from fixed",
        "seeds and judged by Snap Judgment itself, in UTC, with the court's",
        "clock stopped at November 19, 2026. No real person's screenshots",
        "were involved. Several defendants would like to appeal.",
        "",
        "| Defendant | Screenshots | Size | Verdict | Signature award |",
        "| --- | --- | --- | --- | --- |",
        *rows,
        "",
        *sections,
        "*Generated by Snap Judgment. `pip install snap-judgment` and judge your own folder.*",
        "",
    ]
    with open(os.path.join(GALLERY, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(index))
    print("gallery written to", GALLERY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
