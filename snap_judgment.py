#!/usr/bin/env python3
"""Snap Judgment - the courtroom verdict on your screenshot habits.

Every phone and laptop quietly accumulates screenshots: receipts, memes,
chats nobody reopened, error dialogs, boarding passes from 2023. Snap
Judgment reads one or more folders of screenshots and issues a formal
courtroom-style verdict about the habit: a tier, the evidence, awards,
and a sentence.

PRIVACY: Snap Judgment works from file METADATA ONLY. It reads file
names, sizes, modification times, and - solely to learn an image's
width and height - the header bytes of the image file. It never decodes
image pixels, never runs OCR, never looks at what a screenshot shows,
and never uploads anything anywhere. What your screenshots depict is
your business; how many of them there are is the court's.

Pure Python standard library. No dependencies, no accounts, no network.

    pip install snap-judgment
    snapjudgment ~/Pictures/Screenshots
    snapjudgment ~/Desktop ~/Pictures --card card.html --out report.md
    snapjudgment ~/Pictures --svg card.svg --year 2026
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

__version__ = "0.3.0"

# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}
RECORDING_EXTS = {".mp4", ".mov"}

_NAME_HINTS = (
    "screenshot",
    "screen shot",
    "screen_shot",
    "screen-shot",
    "cleanshot",
    "bildschirmfoto",
)


class SnapError(Exception):
    """A plain, user-facing error. Printed as one line, exit code 2."""


@dataclass
class Shot:
    path: str
    name: str
    size: int
    mtime: float
    dt: datetime
    dims: tuple | None  # (width, height) or None when unknown
    is_recording: bool = False


def name_looks_like_screenshot(filename: str) -> bool:
    low = filename.lower()
    return any(hint in low for hint in _NAME_HINTS)


def path_in_screenshot_folder(path: Path) -> bool:
    """True when any parent folder of the file is named like *screenshot*."""
    return any("screenshot" in part.lower() for part in path.parent.parts)


# ---------------------------------------------------------------------------
# dimensions from header bytes only (pixels are never decoded)
# ---------------------------------------------------------------------------

def _png_dimensions(data: bytes):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return (width, height)


_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


def _jpeg_dimensions(data: bytes):
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    i = 2
    n = len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        while marker == 0xFF and i + 2 < n:  # padding bytes before a marker
            i += 1
            marker = data[i + 1]
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # standalone, no length
            i += 2
            continue
        if marker == 0xD9:  # end of image
            return None
        if i + 4 > n:
            return None
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2:
            return None
        if marker in _SOF_MARKERS:
            if i + 9 > n:
                return None
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return (width, height)
        i += 2 + length
    return None


def _gif_dimensions(data: bytes):
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    return (width, height)


def _webp_dimensions(data: bytes):
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    fourcc = data[12:16]
    if fourcc == b"VP8 ":  # lossy
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return (width, height)
    if fourcc == b"VP8L":  # lossless
        if data[20] != 0x2F:
            return None
        b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
        width = (b0 | ((b1 & 0x3F) << 8)) + 1
        height = ((b1 >> 6) | (b2 << 2) | ((b3 & 0x0F) << 10)) + 1
        return (width, height)
    if fourcc == b"VP8X":  # extended
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return (width, height)
    return None


def _bmp_dimensions(data: bytes):
    if len(data) < 26 or data[:2] != b"BM":
        return None
    dib_size = int.from_bytes(data[14:18], "little")
    if dib_size >= 40:  # BITMAPINFOHEADER and later
        width = int.from_bytes(data[18:22], "little", signed=True)
        height = int.from_bytes(data[22:26], "little", signed=True)
        return (abs(width), abs(height))
    if dib_size == 12:  # BITMAPCOREHEADER
        width = int.from_bytes(data[18:20], "little")
        height = int.from_bytes(data[20:22], "little")
        return (width, height)
    return None


_PARSERS = {
    ".png": _png_dimensions,
    ".jpg": _jpeg_dimensions,
    ".jpeg": _jpeg_dimensions,
    ".gif": _gif_dimensions,
    ".webp": _webp_dimensions,
    ".bmp": _bmp_dimensions,
}


def read_dimensions(path: Path, ext: str):
    """(width, height) from header bytes, or None. Never decodes pixels."""
    parser = _PARSERS.get(ext)
    if parser is None:  # heic/heif and anything unknown: do not attempt
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(1 << 20)
        return parser(data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------

def _walk_onerror(_error):
    """os.walk error handler: unreadable folders are ignored, never fatal."""
    return None


def collect(folders, all_images: bool = False, skipped=None):
    """Walk folders recursively. Returns (screenshots, recordings).

    A candidate file that cannot be read (broken symlink, locked file,
    deleted mid-scan) is skipped without crashing; when `skipped` is a
    one-element list, the number of such files is added to skipped[0].
    """
    shots: list[Shot] = []
    recordings: list[Shot] = []
    skipped_count = 0
    for folder in folders:
        for root, _dirs, files in os.walk(folder, onerror=_walk_onerror):
            for filename in files:
                path = Path(root) / filename
                ext = path.suffix.lower()
                if ext in IMAGE_EXTS:
                    is_shot = (
                        all_images
                        or path_in_screenshot_folder(path)
                        or name_looks_like_screenshot(filename)
                    )
                    if not is_shot:
                        continue
                    target, is_rec = shots, False
                elif ext in RECORDING_EXTS:
                    is_recording = (
                        all_images
                        or path_in_screenshot_folder(path)
                        or name_looks_like_screenshot(filename)
                        or "screen recording" in filename.lower()
                    )
                    if not is_recording:
                        continue
                    target, is_rec = recordings, True
                else:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    skipped_count += 1
                    continue
                dims = None if is_rec else read_dimensions(path, ext)
                target.append(
                    Shot(
                        path=str(path),
                        name=filename,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        dt=datetime.fromtimestamp(st.st_mtime),
                        dims=dims,
                        is_recording=is_rec,
                    )
                )
    shots.sort(key=lambda s: s.mtime)
    recordings.sort(key=lambda s: s.mtime)
    if skipped is not None:
        skipped[0] += skipped_count
    return shots, recordings


def default_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / "Pictures" / "Screenshots",
        home / "OneDrive" / "Pictures" / "Screenshots",
        home / "Desktop",
        home / "Pictures",
        home / "Downloads",
    ]


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _fmt_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _fmt_month(d: date) -> str:
    return f"{d:%b} {d.year}"


def _fmt_hour(hour: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {suffix}"


# ---------------------------------------------------------------------------
# source census: where a screenshot came from, from metadata hints only
# ---------------------------------------------------------------------------

PACKAGE_NAMES = {
    "com.whatsapp": "WhatsApp",
    "org.telegram.messenger": "Telegram",
    "com.instagram.android": "Instagram",
    "com.android.chrome": "Chrome",
    "com.twitter.android": "Twitter/X",
    "com.reddit.frontpage": "Reddit",
    "com.zhiliaoapp.musically": "TikTok",
    "com.ss.android.ugc.trill": "TikTok",
    "com.google.android.apps.messaging": "Messages",
    "com.google.android.gm": "Gmail",
    "com.google.android.youtube": "YouTube",
    "com.discord": "Discord",
    "com.snapchat.android": "Snapchat",
    "com.facebook.katana": "Facebook",
    "com.sec.android.gallery3d": "Gallery",
    "com.google.android.apps.photos": "Gallery",
    "com.android.camera": "Camera",
    "com.android.camera2": "Camera",
    "com.google.android.GoogleCamera": "Camera",
    "com.sec.android.app.camera": "Camera",
}

_PACKAGE_TOKEN_RE = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z0-9]+)+")


def extract_package(filename: str):
    """The app package in a filename, or None.

    Android screenshot names often end in the app package, delimited by
    underscores, e.g. Screenshot_20260101-120000_com.whatsapp.png. Each
    underscore/space-separated segment is tested as a whole, and every
    dotted label must contain a letter, so version strings like
    10.00.00 do not qualify.
    """
    stem = Path(filename).stem.lower()
    for segment in re.split(r"[\s_]+", stem):
        if _PACKAGE_TOKEN_RE.fullmatch(segment) and all(
            any(ch.isalpha() for ch in label) for label in segment.split(".")
        ):
            return segment
    return None


def classify_source(shot: Shot) -> str:
    """Origin of one screenshot: app package if the name carries one,
    otherwise the form factor its dimensions suggest."""
    package = extract_package(shot.name)
    if package:
        return PACKAGE_NAMES.get(package, package)
    if shot.dims is None:
        return "Unknown origin"
    width, height = shot.dims
    if width <= 0:
        return "Unknown origin"
    aspect = height / width
    if aspect >= 1.9:
        return "Phone (tall)"
    if aspect >= 1.3:
        return "Phone"
    if aspect >= 0.75:
        return "Tablet / square"
    return "Desktop / landscape"


def analyze(shots: list[Shot], recordings: list[Shot]) -> dict:
    """Compute every statistic from the screenshot records (metadata only)."""
    count = len(shots)
    total_bytes = sum(s.size for s in shots)
    first_dt = shots[0].dt
    last_dt = shots[-1].dt
    first_date, last_date = first_dt.date(), last_dt.date()

    per_year = dict(sorted(Counter(s.dt.year for s in shots).items()))

    day_counts = Counter(s.dt.date() for s in shots)
    busiest_day, busiest_day_count = sorted(
        day_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[0]

    hour_counts = Counter(s.dt.hour for s in shots)
    busiest_hour, busiest_hour_count = sorted(
        hour_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[0]

    month_counts = Counter((s.dt.year, s.dt.month) for s in shots)
    (busiest_year, busiest_month_num), busiest_month_count = sorted(
        month_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[0]
    busiest_month = date(busiest_year, busiest_month_num, 1)

    night_count = sum(1 for s in shots if s.dt.hour >= 22 or s.dt.hour < 5)
    night_share = round(100.0 * night_count / count, 1) if count else 0.0

    weekend_count = sum(1 for s in shots if s.dt.weekday() >= 5)
    weekend_share = round(100.0 * weekend_count / count, 1) if count else 0.0

    now = datetime.now()
    retention_old_count = sum(1 for s in shots if (now - s.dt).days > 365)
    retention_old_share = (
        round(100.0 * retention_old_count / count, 1) if count else 0.0
    )

    source_counts = Counter(classify_source(s) for s in shots)
    sources = [
        {
            "name": name,
            "count": n,
            "share": round(100.0 * n / count, 1) if count else 0.0,
        }
        for name, n in sorted(
            source_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )[:5]
    ]

    work_count = sum(
        1 for s in shots if s.dt.weekday() < 5 and 9 <= s.dt.hour < 17
    )
    work_share = round(100.0 * work_count / count, 1) if count else 0.0

    # longest streak of consecutive days with at least one screenshot
    longest_streak = 0
    run = 0
    prev = None
    for day in sorted(day_counts):
        run = run + 1 if prev is not None and (day - prev).days == 1 else 1
        longest_streak = max(longest_streak, run)
        prev = day

    # longest clean stretch: consecutive days with zero screenshots,
    # measured between active days inside the span
    longest_gap_days = 0
    longest_gap_start = None
    longest_gap_end = None
    active_days = sorted(day_counts)
    for prev_day, next_day in zip(active_days, active_days[1:]):
        gap = (next_day - prev_day).days - 1
        if gap > longest_gap_days:
            longest_gap_days = gap
            longest_gap_start = prev_day + timedelta(days=1)
            longest_gap_end = next_day - timedelta(days=1)

    # burst record: most screenshots inside any rolling 60-second window
    times = sorted(s.mtime for s in shots)
    burst = 0
    j = 0
    for i, t in enumerate(times):
        j = max(j, i)
        while j + 1 < len(times) and times[j + 1] - t <= 60:
            j += 1
        burst = max(burst, j - i + 1)

    # duplicate suspects: groups sharing exact (size, dimensions)
    groups: dict = defaultdict(int)
    for s in shots:
        if s.dims is not None:
            groups[(s.size, s.dims)] += 1
    dup_groups = sum(1 for v in groups.values() if v >= 2)
    dup_files = sum(v for v in groups.values() if v >= 2)
    duplicate_bytes_wasted = sum(
        size * (n - 1) for (size, _dims), n in groups.items() if n >= 2
    )

    # device eras: each distinct resolution, first and last seen
    era_map: dict = {}
    for s in shots:
        if s.dims is None:
            continue
        era = era_map.setdefault(
            s.dims, {"dims": s.dims, "count": 0, "first": s.dt.date(), "last": s.dt.date()}
        )
        era["count"] += 1
        era["first"] = min(era["first"], s.dt.date())
        era["last"] = max(era["last"], s.dt.date())
    device_eras = sorted(era_map.values(), key=lambda e: e["first"])

    per_day = round(count / ((last_date - first_date).days + 1), 1)
    largest = max(shots, key=lambda s: s.size)

    oldest = shots[0]
    return {
        "count": count,
        "recordings": len(recordings),
        "total_bytes": total_bytes,
        "total_human": human_bytes(total_bytes),
        "first_date": first_date,
        "last_date": last_date,
        "span_days": (last_date - first_date).days,
        "oldest_name": oldest.name,
        "oldest_date": oldest.dt.date(),
        "oldest_dt": oldest.dt,
        "per_year": per_year,
        "busiest_day": busiest_day,
        "busiest_day_count": busiest_day_count,
        "busiest_month": busiest_month,
        "busiest_month_count": busiest_month_count,
        "busiest_hour": busiest_hour,
        "busiest_hour_count": busiest_hour_count,
        "night_count": night_count,
        "night_share": night_share,
        "weekend_count": weekend_count,
        "weekend_share": weekend_share,
        "retention_old_count": retention_old_count,
        "retention_old_share": retention_old_share,
        "sources": sources,
        "work_count": work_count,
        "work_share": work_share,
        "longest_streak": longest_streak,
        "longest_gap_days": longest_gap_days,
        "longest_gap_start": longest_gap_start,
        "longest_gap_end": longest_gap_end,
        "burst_record": burst,
        "duplicate_groups": dup_groups,
        "duplicate_files": dup_files,
        "duplicate_bytes_wasted": duplicate_bytes_wasted,
        "per_day": per_day,
        "largest_name": largest.name,
        "largest_size": largest.size,
        "largest_human": human_bytes(largest.size),
        "device_eras": device_eras,
        "skipped": 0,
    }


# ---------------------------------------------------------------------------
# verdict and awards
# ---------------------------------------------------------------------------

TIERS = [
    (2500, "Unrepentant Screenshot Felon",
     "Sentence: delete nothing. We both know you won't. The court has stopped pretending otherwise."),
    (1000, "Felony-Level Archivist",
     "Sentence: delete nothing. We both know you won't."),
    (500, "Habitual Hoarder",
     "Sentence: mandatory folder organization, suspended, because we both know it won't happen."),
    (200, "Repeat Offender",
     "Sentence: probation. The screenshots folder gets a check-in once a month."),
    (50, "Petty Offender",
     "Sentence: community service. Delete one duplicate. Just one. Baby steps."),
    (1, "Innocent Bystander",
     "Sentence: case dismissed. Go take a screenshot of this verdict."),
]


def verdict_for(count: int, voice: str = "fair"):
    """(tier name, sentence) for a screenshot count, in the judge's voice."""
    if count <= 0:
        return ("No Evidence", "")
    for minimum, name, sentence in TIERS:
        if count >= minimum:
            if voice in VOICE_SENTENCES:
                sentence = VOICE_SENTENCES[voice].get(name, sentence)
            return (name, sentence)
    return ("Innocent Bystander", TIERS[-1][2])


# ---------------------------------------------------------------------------
# the presiding judges: voices change the wording, never the math
# ---------------------------------------------------------------------------

VOICES = ("strict", "fair", "lenient")

JUDGE_NAMES = {
    "strict": "The Hanging Judge",
    "fair": "The Fair Judge",
    "lenient": "The Sympathetic Judge",
}

JUDGE_INTROS = {
    "strict": "The Hanging Judge presiding. Mercy is not on today's docket.",
    "lenient": "The Sympathetic Judge presiding. This court has seen the folder, "
               "and it understands.",
}

VOICE_SENTENCES = {
    "strict": {
        "Unrepentant Screenshot Felon":
            "Sentence: maximum. Every screenshot will be reviewed, named, and "
            "justified in writing. All of them.",
        "Felony-Level Archivist":
            "Sentence: a full audit of the evidence locker, this weekend. "
            "No extensions. No excuses.",
        "Habitual Hoarder":
            "Sentence: the folder is seized. Nothing new enters it until "
            "the backlog is halved.",
        "Repeat Offender":
            "Sentence: weekly inspections of the screenshots folder until "
            "this court says otherwise.",
        "Petty Offender":
            "Sentence: the duplicates are deleted today. Not tomorrow. Today.",
        "Innocent Bystander":
            "Sentence: none. This time. The court will be watching the folder.",
    },
    "lenient": {
        "Unrepentant Screenshot Felon":
            "Encouragement: every archive tells a story, and yours is a long "
            "one. There is no rush to change.",
        "Felony-Level Archivist":
            "Encouragement: you kept it all because it all mattered at the "
            "time. That is not a crime in this court.",
        "Habitual Hoarder":
            "Encouragement: a healthy collection. Sort one folder someday, "
            "if you ever feel like it.",
        "Repeat Offender":
            "Encouragement: honestly, this is most people. A little tidy now "
            "and then, if the mood strikes.",
        "Petty Offender":
            "Encouragement: barely a habit at all. Keep doing what you are doing.",
        "Innocent Bystander":
            "Encouragement: case dismissed, with applause. Screenshot this "
            "verdict; you have earned one.",
    },
}

# (framing line before the award list, line when there are no awards);
# the fair judge keeps the long-standing wording, so it has no entry.
AWARD_FRAMING = {
    "strict": (
        "The court enters the following into the record, without pleasure:",
        "None. A small mercy, and the only one this court offers today.",
    ),
    "lenient": (
        "The court would also like to celebrate the following:",
        "None needed. The court is proud of you.",
    ),
}


def compute_awards(stats: dict, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    awards = []
    count = stats["count"]
    if stats["night_count"] >= 20 and stats["night_share"] >= 25:
        awards.append({
            "name": "Night Owl",
            "evidence": f"{stats['night_count']} screenshots between 10 PM and 5 AM "
                        f"({stats['night_share']:.1f}% of the total).",
        })
    if stats["burst_record"] >= 8:
        awards.append({
            "name": "Burst Mode",
            "evidence": f"{stats['burst_record']} screenshots inside a single 60-second window.",
        })
    if (now - stats["oldest_dt"]).days >= 3 * 365:
        awards.append({
            "name": "The Archivist",
            "evidence": f"The oldest screenshot dates to {_fmt_date(stats['oldest_date'])}, "
                        f"over three years ago, and it is still here.",
        })
    if count >= 50 and stats["work_share"] >= 50:
        awards.append({
            "name": "Receipt Keeper",
            "evidence": f"{stats['work_share']:.1f}% of shots were taken on weekdays between "
                        f"9 AM and 5 PM. Very professional.",
        })
    if stats["duplicate_files"] >= 10:
        awards.append({
            "name": "Repeat Customer",
            "evidence": f"{stats['duplicate_files']} files share an exact size and dimensions "
                        f"with a sibling. The same screenshot, taken again.",
        })
    if stats["total_bytes"] >= 1024 ** 3:
        awards.append({
            "name": "Gigabyte Club",
            "evidence": f"The screenshots alone total {stats['total_human']}. "
                        f"The evidence locker needs a bigger room.",
        })
    if stats["span_days"] >= 4 * 365:
        awards.append({
            "name": "Time Traveler",
            "evidence": f"The collection spans {stats['span_days'] / 365.25:.1f} years of screenshots.",
        })
    if stats["longest_streak"] >= 7:
        awards.append({
            "name": "Screenshot Streak",
            "evidence": f"{stats['longest_streak']} consecutive days with at least one screenshot.",
        })
    if stats["longest_gap_days"] >= 30:
        awards.append({
            "name": "Clean Stretch",
            "evidence": f"{stats['longest_gap_days']} days without a single screenshot. "
                        f"The court's longest recorded moment of restraint.",
        })
    if 0 < count < 50:
        awards.append({
            "name": "Minimalist",
            "evidence": f"Only {count} screenshots. The court rarely sees such restraint.",
        })
    return awards


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_no_evidence() -> str:
    return "\n".join([
        "=" * 62,
        "SNAP JUDGMENT - The Court of Screenshot Affairs",
        "=" * 62,
        "",
        "THE VERDICT",
        "  No Evidence",
        "",
        "  The court finds nothing. No screenshots were produced as",
        "  evidence, which means this folder is innocent, empty, or",
        "  not where your screenshots actually live.",
        "",
        "  Point Snap Judgment at a real screenshots folder:",
        "      snapjudgment ~/Pictures/Screenshots",
        "  or pass --all to judge every image in a folder.",
        "",
    ])


def _evidence_lines(stats: dict) -> list[tuple[str, str]]:
    lines = [
        ("Total screenshots", f"{stats['count']:,}"),
        ("Total size", f"{stats['total_human']} ({stats['total_bytes']:,} bytes)"),
        ("Largest screenshot", f"{stats['largest_name']} ({stats['largest_human']})"),
        ("Date span", f"{_fmt_date(stats['first_date'])} - {_fmt_date(stats['last_date'])} "
                      f"({stats['span_days'] + 1:,} day{'s' if stats['span_days'] else ''})"),
        ("Oldest screenshot", f"{stats['oldest_name']} ({_fmt_date(stats['oldest_date'])})"),
        ("Busiest day", f"{_fmt_date(stats['busiest_day'])} ({stats['busiest_day_count']} screenshots)"),
        ("Busiest month", f"{_fmt_month(stats['busiest_month'])} ({stats['busiest_month_count']} screenshots)"),
        ("Busiest hour", f"{_fmt_hour(stats['busiest_hour'])} ({stats['busiest_hour_count']} screenshots)"),
        ("Taken at night (10 PM-5 AM)", f"{stats['night_count']} ({stats['night_share']:.1f}%)"),
        ("Weekend share", f"{stats['weekend_count']} ({stats['weekend_share']:.1f}%)"),
        ("Evidence retention", f"{stats['retention_old_count']} over a year old "
                               f"({stats['retention_old_share']:.1f}%)"),
        ("Screenshots per day", f"{stats['per_day']:.1f}"),
        ("Longest daily streak", f"{stats['longest_streak']} days"),
    ]
    if stats["longest_gap_days"] >= 2:
        lines.append(
            ("Longest clean stretch",
             f"{stats['longest_gap_days']} days "
             f"({_fmt_date(stats['longest_gap_start'])} - {_fmt_date(stats['longest_gap_end'])})")
        )
    lines += [
        ("Biggest burst", f"{stats['burst_record']} screenshots in 60 seconds"),
        ("Duplicate suspects", f"{stats['duplicate_groups']} "
                               f"group{'s' if stats['duplicate_groups'] != 1 else ''}, "
                               f"{stats['duplicate_files']} files"),
    ]
    if stats["duplicate_bytes_wasted"] > 0:
        lines.append(
            ("Duplicate waste", human_bytes(stats["duplicate_bytes_wasted"]))
        )
    lines += [
        ("Screen recordings", f"{stats['recordings']}"),
        ("Device eras", f"{len(stats['device_eras'])}"),
    ]
    if stats.get("skipped"):
        lines.append(("Files the court could not read", f"{stats['skipped']}"))
    return lines


def render_report(stats: dict, tier: str, sentence: str, awards: list[dict],
                  year: int | None = None, voice: str = "fair",
                  court_record: str | None = None) -> str:
    out = [
        "=" * 62,
        "SNAP JUDGMENT - The Court of Screenshot Affairs",
    ]
    if voice != "fair":
        out.append(f"Presiding: {JUDGE_NAMES[voice]}")
    out += [
        "=" * 62,
        "",
        "THE VERDICT",
    ]
    intro = JUDGE_INTROS.get(voice)
    if intro:
        out.append(f"  {intro}")
    out += [
        f"  {tier}",
        f"  {stats['count']:,} screenshots, {stats['total_human']} of evidence,",
        f"  spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.",
    ]
    if year is not None:
        out.append(f"  Scope: year {year}")
    if court_record:
        out.append(f"  {court_record}")
    out += [
        "",
        "THE EVIDENCE",
    ]
    for label, value in _evidence_lines(stats):
        out.append(f"  {label:<30} {value}")
    for era in stats["device_eras"]:
        w, h = era["dims"]
        out.append(
            f"  {'':<30} {w}x{h} - {era['count']} shots, "
            f"{_fmt_date(era['first'])} to {_fmt_date(era['last'])}"
        )
    per_year = ", ".join(f"{y}: {n}" for y, n in stats["per_year"].items())
    out.append(f"  {'Screenshots per year':<30} {per_year}")
    if stats["count"] >= 10 and stats["sources"]:
        out += ["", "THE CHARGES"]
        for source in stats["sources"]:
            out.append(
                f"  {source['name']:<30} {source['count']} ({source['share']:.1f}%)"
            )
    out += ["", "AWARDS"]
    framing = AWARD_FRAMING.get(voice)
    if awards:
        if framing:
            out.append(f"  {framing[0]}")
        for award in awards:
            out.append(f"  * {award['name']} - {award['evidence']}")
    else:
        out.append(
            f"  {framing[1] if framing else 'None. The court is as surprised as you are.'}"
        )
    out += ["", "THE SENTENCE", f"  {sentence}", ""]
    return "\n".join(out)


def worse_offender(main_stats: dict, vs_stats: dict) -> str:
    """Who is guiltier: 'main', 'vs', or 'tie'. Count first, then size."""
    if main_stats["count"] != vs_stats["count"]:
        return "main" if main_stats["count"] > vs_stats["count"] else "vs"
    if main_stats["total_bytes"] != vs_stats["total_bytes"]:
        return "main" if main_stats["total_bytes"] > vs_stats["total_bytes"] else "vs"
    return "tie"


def _comparison_closing(main_stats: dict, vs_stats: dict, worse: str) -> str:
    if worse == "tie":
        return "Equally guilty: the court cannot separate them."
    if worse == "main":
        who, winner, loser = "the defendant", main_stats, vs_stats
    else:
        who, winner, loser = "the co-defendant", vs_stats, main_stats
    if winner["count"] != loser["count"]:
        return (f"Bigger offender: {who}, {winner['count']:,} screenshots "
                f"to {loser['count']:,}.")
    return (f"Bigger offender: {who}, on total size ({winner['total_human']} "
            f"to {loser['total_human']}) with the counts level.")


def _comparison_rows(main_stats: dict, main_tier: str,
                     vs_stats: dict, vs_tier: str) -> list[tuple[str, str, str]]:
    return [
        ("Verdict", main_tier, vs_tier),
        ("Screenshots", f"{main_stats['count']:,}", f"{vs_stats['count']:,}"),
        ("Total size", main_stats["total_human"], vs_stats["total_human"]),
        ("Night share", f"{main_stats['night_share']:.1f}%",
         f"{vs_stats['night_share']:.1f}%"),
        ("Oldest", _fmt_date(main_stats["oldest_date"]),
         _fmt_date(vs_stats["oldest_date"])),
    ]


def render_comparison(main_stats: dict, main_tier: str, versus: dict) -> str:
    vs_stats, vs_tier = versus["stats"], versus["tier"]
    out = [
        "CO-DEFENDANT COMPARISON",
        f"  {'':<22} {'Defendant':<30} Co-defendant",
    ]
    for label, main_value, vs_value in _comparison_rows(
        main_stats, main_tier, vs_stats, vs_tier
    ):
        out.append(f"  {label:<22} {main_value:<30} {vs_value}")
    out += [
        "",
        f"  {_comparison_closing(main_stats, vs_stats, versus['worse'])}",
        "",
    ]
    return "\n".join(out)


def render_markdown(stats: dict, tier: str, sentence: str, awards: list[dict],
                    year: int | None = None, voice: str = "fair",
                    versus: dict | None = None) -> str:
    out = [
        "# Snap Judgment",
        "",
        "## The Verdict",
        "",
        f"**{tier}** - {stats['count']:,} screenshots, {stats['total_human']} of evidence, "
        f"spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.",
    ]
    intro = JUDGE_INTROS.get(voice)
    if intro:
        out.append(intro)
    if year is not None:
        out.append(f"Scope: year {year}")
    out += [
        "",
        "## The Evidence",
        "",
    ]
    for label, value in _evidence_lines(stats):
        out.append(f"- **{label}:** {value}")
    for era in stats["device_eras"]:
        w, h = era["dims"]
        out.append(
            f"- **Device era:** {w}x{h} - {era['count']} shots, "
            f"{_fmt_date(era['first'])} to {_fmt_date(era['last'])}"
        )
    per_year = ", ".join(f"{y}: {n}" for y, n in stats["per_year"].items())
    out.append(f"- **Screenshots per year:** {per_year}")
    if stats["count"] >= 10 and stats["sources"]:
        out += ["", "## The Charges", ""]
        for source in stats["sources"]:
            out.append(
                f"- **{source['name']}:** {source['count']} ({source['share']:.1f}%)"
            )
    out += ["", "## Awards", ""]
    framing = AWARD_FRAMING.get(voice)
    if awards:
        if framing:
            out.append(framing[0])
        for award in awards:
            out.append(f"- **{award['name']}** - {award['evidence']}")
    else:
        out.append(
            framing[1] if framing else "None. The court is as surprised as you are."
        )
    out += ["", "## The Sentence", "", sentence, ""]
    if versus is not None:
        vs_stats, vs_tier = versus["stats"], versus["tier"]
        out += [
            "## Co-Defendant Comparison",
            "",
            "| | Defendant | Co-defendant |",
            "| --- | --- | --- |",
        ]
        for label, main_value, vs_value in _comparison_rows(
            stats, tier, vs_stats, vs_tier
        ):
            out.append(f"| {label} | {main_value} | {vs_value} |")
        out += [
            "",
            _comparison_closing(stats, vs_stats, versus["worse"]),
            "",
        ]
    return "\n".join(out)


def build_json(stats: dict, tier: str, sentence: str, awards: list[dict],
               folders: list[str], year: int | None = None,
               judge: str = "fair") -> dict:
    return {
        "version": __version__,
        "folders": folders,
        "verdict": tier,
        "sentence": sentence,
        "judge": judge,
        "year": year,
        "count": stats["count"],
        "recordings": stats["recordings"],
        "total_bytes": stats["total_bytes"],
        "total_human": stats["total_human"],
        "first_date": stats["first_date"].isoformat(),
        "last_date": stats["last_date"].isoformat(),
        "span_days": stats["span_days"],
        "oldest": {
            "name": stats["oldest_name"],
            "date": stats["oldest_date"].isoformat(),
        },
        "largest": {
            "name": stats["largest_name"],
            "bytes": stats["largest_size"],
            "human": stats["largest_human"],
        },
        "per_year": {str(y): n for y, n in stats["per_year"].items()},
        "per_day": stats["per_day"],
        "busiest_day": {
            "date": stats["busiest_day"].isoformat(),
            "count": stats["busiest_day_count"],
        },
        "busiest_month": {
            "month": _fmt_month(stats["busiest_month"]),
            "count": stats["busiest_month_count"],
        },
        "busiest_hour": {
            "hour": stats["busiest_hour"],
            "count": stats["busiest_hour_count"],
        },
        "night_count": stats["night_count"],
        "night_share_pct": stats["night_share"],
        "weekend_count": stats["weekend_count"],
        "weekend_share": stats["weekend_share"],
        "retention_old_count": stats["retention_old_count"],
        "retention_old_share": stats["retention_old_share"],
        "sources": stats["sources"],
        "work_share_pct": stats["work_share"],
        "longest_streak_days": stats["longest_streak"],
        "longest_gap_days": stats["longest_gap_days"],
        "longest_gap": (
            {
                "start": stats["longest_gap_start"].isoformat(),
                "end": stats["longest_gap_end"].isoformat(),
            }
            if stats["longest_gap_start"] is not None else None
        ),
        "burst_record": stats["burst_record"],
        "duplicate_groups": stats["duplicate_groups"],
        "duplicate_files": stats["duplicate_files"],
        "duplicate_bytes_wasted": stats["duplicate_bytes_wasted"],
        "skipped": stats.get("skipped", 0),
        "device_eras": [
            {
                "width": era["dims"][0],
                "height": era["dims"][1],
                "count": era["count"],
                "first_seen": era["first"].isoformat(),
                "last_seen": era["last"].isoformat(),
            }
            for era in stats["device_eras"]
        ],
        "awards": awards,
    }


def render_card(stats: dict, tier: str, sentence: str, awards: list[dict]) -> str:
    gb = stats["total_bytes"] / (1024 ** 3)
    top_stats = [
        ("Busiest day", f"{_fmt_date(stats['busiest_day'])} ({stats['busiest_day_count']} shots)"),
        ("Night share", f"{stats['night_share']:.1f}% after 10 PM"),
        ("Biggest burst", f"{stats['burst_record']} in 60 seconds"),
    ]
    stats_html = "\n".join(
        f'      <div class="stat"><div class="stat-value">{html.escape(v)}</div>'
        f'<div class="stat-label">{html.escape(k)}</div></div>'
        for k, v in top_stats
    )
    if awards:
        awards_html = "\n".join(
            f"        <li><strong>{html.escape(a['name'])}</strong> - {html.escape(a['evidence'])}</li>"
            for a in awards
        )
    else:
        awards_html = "        <li>None. The court is as surprised as you are.</li>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Snap Judgment - {html.escape(tier)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0d0d12; color: #e8e3d8; font-family: Georgia, 'Times New Roman', serif;
         display: flex; justify-content: center; padding: 40px 16px; }}
  .card {{ max-width: 640px; width: 100%; background: #16161f; border: 2px solid #b08d3c;
          border-radius: 10px; padding: 36px 40px; box-shadow: 0 12px 40px rgba(0,0,0,.6); }}
  .court {{ text-align: center; letter-spacing: 3px; font-size: 12px; color: #b08d3c;
           text-transform: uppercase; }}
  h1 {{ text-align: center; font-size: 30px; margin: 10px 0 4px; }}
  .tier {{ text-align: center; font-size: 22px; color: #d4af5a; margin-bottom: 18px; }}
  .headline {{ display: flex; justify-content: space-around; text-align: center;
              border-top: 1px solid #33333f; border-bottom: 1px solid #33333f;
              padding: 16px 0; margin-bottom: 18px; }}
  .headline .num {{ font-size: 26px; color: #fff; }}
  .headline .lbl {{ font-size: 11px; letter-spacing: 2px; color: #9a94a6; text-transform: uppercase; }}
  .stats {{ display: flex; justify-content: space-between; gap: 12px; margin-bottom: 18px; }}
  .stat {{ flex: 1; background: #1e1e2a; border-radius: 8px; padding: 12px; text-align: center; }}
  .stat-value {{ font-size: 15px; color: #fff; }}
  .stat-label {{ font-size: 11px; letter-spacing: 1px; color: #9a94a6; text-transform: uppercase; margin-top: 4px; }}
  h2 {{ font-size: 13px; letter-spacing: 2px; color: #b08d3c; text-transform: uppercase; margin-bottom: 8px; }}
  ul {{ list-style: none; margin-bottom: 18px; }}
  li {{ padding: 4px 0; font-size: 14px; }}
  .sentence {{ font-style: italic; color: #cfc9ba; border-top: 1px solid #33333f; padding-top: 14px; }}
  .foot {{ text-align: center; font-size: 11px; color: #6d6878; margin-top: 16px; }}
</style>
</head>
<body>
  <div class="card">
    <div class="court">The Court of Screenshot Affairs</div>
    <h1>Snap Judgment</h1>
    <div class="tier">{html.escape(tier)}</div>
    <div class="headline">
      <div><div class="num">{stats['count']:,}</div><div class="lbl">Screenshots</div></div>
      <div><div class="num">{gb:.2f} GB</div><div class="lbl">Of evidence</div></div>
      <div><div class="num">{_fmt_date(stats['first_date'])} - {_fmt_date(stats['last_date'])}</div><div class="lbl">Span</div></div>
    </div>
    <div class="stats">
{stats_html}
    </div>
    <h2>Awards</h2>
    <ul>
{awards_html}
    </ul>
    <div class="sentence">{html.escape(sentence)}</div>
    <div class="foot">Judged from file metadata only. Pixels were never read. Snap Judgment, Ghost Developer Studio.</div>
  </div>
</body>
</html>
"""


def render_svg(stats: dict, tier: str, sentence: str, awards: list[dict]) -> str:
    """Self-contained 1200x630 SVG share card. Inline styles only."""
    esc = _xml_escape
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}"
    stat_cells = [
        (f"{stats['count']:,}", "SCREENSHOTS", 42),
        (stats["total_human"], "OF EVIDENCE", 42),
        (span, "SPAN", 27),
        (f"{stats['night_share']:.1f}%", "NIGHT SHARE", 42),
    ]
    cells = []
    for (value, label, size), x in zip(stat_cells, (210, 490, 770, 1050)):
        cells.append(
            f'  <text x="{x}" y="348" text-anchor="middle" font-size="{size}" '
            f'fill="#ffffff">{esc(value)}</text>\n'
            f'  <text x="{x}" y="384" text-anchor="middle" font-size="16" '
            f'letter-spacing="3" fill="#9a94a6">{esc(label)}</text>'
        )
    stats_svg = "\n".join(cells)
    if awards:
        award_names = "  ·  ".join(a["name"] for a in awards[:3])
    else:
        award_names = "None. The court is as surprised as you are."
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630" role="img" aria-label="Snap Judgment verdict card">
  <rect width="1200" height="630" fill="#0d0d12"/>
  <rect x="24" y="24" width="1152" height="582" rx="14" fill="#16161f" stroke="#b08d3c" stroke-width="3"/>
  <text x="600" y="92" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="19" letter-spacing="6" fill="#b08d3c">THE COURT OF SCREENSHOT AFFAIRS</text>
  <text x="600" y="158" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="58" font-weight="bold" fill="#e8e3d8">SNAP JUDGMENT</text>
  <text x="600" y="228" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="46" fill="#d4af5a">{esc(tier)}</text>
  <line x1="200" y1="262" x2="1000" y2="262" stroke="#33333f" stroke-width="1"/>
{stats_svg}
  <text x="600" y="448" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="14" letter-spacing="4" fill="#b08d3c">AWARDS</text>
  <text x="600" y="486" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="25" fill="#e8e3d8">{esc(award_names)}</text>
  <text x="600" y="540" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" font-style="italic" fill="#cfc9ba">{esc(sentence)}</text>
  <text x="600" y="580" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="16" fill="#6d6878">Judged from file metadata only. No pixels were read.</text>
</svg>
"""


def write_csv(path, shots: list[Shot], recordings: list[Shot]) -> None:
    """Per-file records for every judged screenshot (and recording)."""
    rows = sorted([*shots, *recordings], key=lambda s: s.mtime)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["path", "size_bytes", "modified", "width", "height", "is_recording"]
        )
        for s in rows:
            writer.writerow([
                s.path,
                s.size,
                s.dt.isoformat(),
                s.dims[0] if s.dims else "",
                s.dims[1] if s.dims else "",
                "true" if s.is_recording else "false",
            ])


# ---------------------------------------------------------------------------
# court record: a JSONL history of prior hearings, kept in the home folder
# ---------------------------------------------------------------------------

def history_path() -> Path:
    return Path.home() / ".snap-judgment" / "history.jsonl"


def read_history() -> list[dict]:
    """All recorded hearings, oldest first. Never raises."""
    try:
        path = history_path()
        if not path.is_file():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records
    except Exception:
        return []


def append_history(record: dict) -> None:
    """Append one hearing to the court record. Never raises."""
    try:
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def court_record_line(records: list[dict], primary_folder: str,
                      current_count: int) -> str | None:
    """The 'last hearing' line for the verdict, or None when there is no
    previous hearing for this folder."""
    previous = None
    for record in records:
        folders = record.get("folders") or []
        if folders and folders[0] == primary_folder:
            previous = record
    if previous is None:
        return None
    try:
        prev_count = int(previous["count"])
        prev_date = datetime.fromisoformat(previous["timestamp"]).date()
    except Exception:
        return None
    diff = current_count - prev_count
    if diff > 0:
        change = f"UP by {diff} since"
    elif diff < 0:
        change = f"DOWN by {-diff} since"
    else:
        change = "NO CHANGE since"
    return (
        f"Court record: {prev_count} screenshots at your last hearing on "
        f"{_fmt_date(prev_date)} ({change})."
    )


def render_history(records: list[dict]) -> str:
    out = ["COURT RECORD - prior hearings", ""]
    for record in records:
        try:
            when = datetime.fromisoformat(record["timestamp"])
            folder = (record.get("folders") or ["?"])[0]
            out.append(
                f"  {_fmt_date(when.date())}  {folder}  "
                f"{record['count']} screenshots  {record['verdict']}"
            )
        except Exception:
            continue
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapjudgment",
        description=(
            "Snap Judgment issues a courtroom-style verdict on your screenshot "
            "habits, computed from file metadata only. It reads names, sizes, "
            "dates, and image header bytes (for dimensions); it never decodes "
            "image pixels and never uploads anything."
        ),
    )
    parser.add_argument("folders", nargs="*", metavar="FOLDER",
                        help="folder(s) of screenshots (default: auto-detect)")
    parser.add_argument("--all", action="store_true",
                        help="treat every image in the folders as a screenshot")
    parser.add_argument("--json", action="store_true",
                        help="print the verdict as JSON instead of the report")
    parser.add_argument("--card", metavar="FILE",
                        help="write a self-contained HTML share card to FILE")
    parser.add_argument("--svg", metavar="FILE",
                        help="write an SVG share card (1200x630) to FILE")
    parser.add_argument("--out", metavar="FILE",
                        help="write a Markdown report to FILE")
    parser.add_argument("--since", metavar="YYYY",
                        help="only count screenshots from this year onward")
    parser.add_argument("--year", metavar="YYYY",
                        help="only count screenshots from this single year")
    parser.add_argument("--vs", metavar="FOLDER", dest="vs",
                        help="also judge FOLDER as a co-defendant and compare")
    parser.add_argument("--judge", choices=list(VOICES), default="fair",
                        help="the presiding judge's voice (default: fair)")
    parser.add_argument("--csv", metavar="FILE",
                        help="write per-file records to FILE as CSV")
    parser.add_argument("--history", action="store_true",
                        help="print the court record (prior hearings) and exit")
    parser.add_argument("--version", action="version",
                        version=f"snapjudgment {__version__}")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.history:
        records = read_history()
        if not records:
            print(
                "snapjudgment: no court record yet; this court has held "
                "no hearings",
                file=sys.stderr,
            )
            return 2
        print(render_history(records))
        return 0

    since_year = None
    if args.since is not None:
        if len(args.since) != 4 or not args.since.isdigit():
            parser.error("--since must be a four-digit year, e.g. --since 2024")
        since_year = int(args.since)

    year = None
    until_year = None
    if args.year is not None:
        if len(args.year) != 4 or not args.year.isdigit():
            parser.error("--year must be a four-digit year, e.g. --year 2026")
        year = int(args.year)
        since_year = year
        until_year = year

    skipped_box = [0]
    try:
        if args.folders:
            folders = []
            for raw in args.folders:
                folder = Path(raw).expanduser()
                if not folder.is_dir():
                    raise SnapError(f"folder not found: {raw}")
                folders.append(folder)
            shots, recordings = collect(
                folders, all_images=args.all, skipped=skipped_box
            )
        else:
            shots, recordings = [], []
            folders = []
            for candidate in default_candidates():
                if candidate.is_dir():
                    box = [0]
                    found_shots, found_recordings = collect(
                        [candidate], all_images=args.all, skipped=box
                    )
                    if found_shots:
                        shots, recordings = found_shots, found_recordings
                        skipped_box = box
                        folders = [candidate]
                        break
            if not folders:
                raise SnapError(
                    "no screenshots found in the usual places "
                    "(~/Pictures/Screenshots, ~/OneDrive/Pictures/Screenshots, "
                    "~/Desktop, ~/Pictures, ~/Downloads); "
                    "pass a folder: snapjudgment /path/to/folder"
                )

        vs_shots: list[Shot] = []
        vs_recordings: list[Shot] = []
        vs_folder = None
        if args.vs is not None:
            vs_folder = Path(args.vs).expanduser()
            if not vs_folder.is_dir():
                raise SnapError(f"folder not found: {args.vs}")
            vs_shots, vs_recordings = collect(
                [vs_folder], all_images=args.all, skipped=skipped_box
            )

        if since_year is not None:
            shots = [s for s in shots if s.dt.year >= since_year]
            recordings = [r for r in recordings if r.dt.year >= since_year]
            vs_shots = [s for s in vs_shots if s.dt.year >= since_year]
            vs_recordings = [r for r in vs_recordings if r.dt.year >= since_year]
        if until_year is not None:
            shots = [s for s in shots if s.dt.year <= until_year]
            recordings = [r for r in recordings if r.dt.year <= until_year]
            vs_shots = [s for s in vs_shots if s.dt.year <= until_year]
            vs_recordings = [r for r in vs_recordings if r.dt.year <= until_year]

        folder_names = [str(f) for f in folders]

        if not shots:
            if args.json:
                print(json.dumps({
                    "version": __version__,
                    "folders": folder_names,
                    "verdict": "No Evidence",
                    "judge": args.judge,
                    "year": year,
                    "count": 0,
                    "recordings": len(recordings),
                    "skipped": skipped_box[0],
                    "awards": [],
                }, indent=2))
            else:
                print(render_no_evidence())
                append_history({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "folders": folder_names,
                    "count": 0,
                    "total_bytes": 0,
                    "verdict": "No Evidence",
                    "judge": args.judge,
                })
            return 2

        if args.vs is not None and not vs_shots:
            raise SnapError(
                f"co-defendant folder produced no screenshots: {args.vs}"
            )

        stats = analyze(shots, recordings)
        stats["skipped"] = skipped_box[0]
        tier, sentence = verdict_for(stats["count"], voice=args.judge)
        awards = compute_awards(stats)

        versus = None
        vs_stats = None
        if args.vs is not None:
            vs_stats = analyze(vs_shots, vs_recordings)
            vs_tier, vs_sentence = verdict_for(
                vs_stats["count"], voice=args.judge
            )
            versus = {
                "folder": str(vs_folder),
                "stats": vs_stats,
                "tier": vs_tier,
                "sentence": vs_sentence,
                "awards": compute_awards(vs_stats),
                "worse": worse_offender(stats, vs_stats),
            }

        court_line = None
        if not args.json and folder_names:
            court_line = court_record_line(
                read_history(), folder_names[0], stats["count"]
            )

        if args.card:
            Path(args.card).write_text(
                render_card(stats, tier, sentence, awards), encoding="utf-8"
            )
        if args.svg:
            Path(args.svg).write_text(
                render_svg(stats, tier, sentence, awards), encoding="utf-8"
            )
        if args.csv:
            write_csv(args.csv, shots, recordings)
        if args.out:
            Path(args.out).write_text(
                render_markdown(stats, tier, sentence, awards, year=year,
                                voice=args.judge, versus=versus),
                encoding="utf-8"
            )

        if args.json:
            data = build_json(stats, tier, sentence, awards, folder_names,
                              year=year, judge=args.judge)
            if versus is not None:
                data["versus"] = {
                    "main": build_json(stats, tier, sentence, awards,
                                       folder_names, year=year,
                                       judge=args.judge),
                    "vs": build_json(vs_stats, versus["tier"],
                                     versus["sentence"], versus["awards"],
                                     [str(vs_folder)], year=year,
                                     judge=args.judge),
                    "worse_offender": versus["worse"],
                }
            print(json.dumps(data, indent=2))
        else:
            print(render_report(stats, tier, sentence, awards, year=year,
                                voice=args.judge, court_record=court_line))
            if versus is not None:
                print(render_comparison(stats, tier, versus))
            append_history({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "folders": folder_names,
                "count": stats["count"],
                "total_bytes": stats["total_bytes"],
                "verdict": tier,
                "judge": args.judge,
            })
        return 0
    except SnapError as exc:
        print(f"snapjudgment: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
