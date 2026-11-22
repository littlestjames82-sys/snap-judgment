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
import statistics
import struct
import sys
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10 have no tomllib
    tomllib = None

__version__ = "0.16.0"

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
    "bildschirmfoto",          # German
    "capture d'écran",         # French (macOS)
    "capture d'ecran",         # French, unaccented
    "capture d’écran",    # French, typographic apostrophe
    "captura de pantalla",     # Spanish
    "スクリーンショット",            # Japanese
    "zrzut ekranu",            # Polish
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

_SEASONS = {
    12: "Winter (Dec-Feb)", 1: "Winter (Dec-Feb)", 2: "Winter (Dec-Feb)",
    3: "Spring (Mar-May)", 4: "Spring (Mar-May)", 5: "Spring (Mar-May)",
    6: "Summer (Jun-Aug)", 7: "Summer (Jun-Aug)", 8: "Summer (Jun-Aug)",
    9: "Autumn (Sep-Nov)", 10: "Autumn (Sep-Nov)", 11: "Autumn (Sep-Nov)",
}


def _season_for_month(month: int) -> str:
    """Meteorological season name for a calendar month."""
    return _SEASONS[month]


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

    # busiest hour window: most screenshots inside any rolling 60-minute
    # window that starts at a screenshot's own timestamp
    ordered = sorted(shots, key=lambda s: s.mtime)
    window_count = 0
    window_start = ordered[0].dt
    j = 0
    for i, shot in enumerate(ordered):
        j = max(j, i)
        while j + 1 < len(ordered) and ordered[j + 1].mtime - shot.mtime <= 3600:
            j += 1
        if j - i + 1 > window_count:
            window_count = j - i + 1
            window_start = shot.dt
    busiest_window = {"count": window_count, "start": window_start}

    # hour x weekday heatmap: 7 rows (Monday first) of 24 hour counts
    heatmap = [[0] * 24 for _ in range(7)]
    for s in shots:
        heatmap[s.dt.weekday()][s.dt.hour] += 1

    # the earliest screenshot dated in the future, if any (time travel)
    future_shot = None
    for s in shots:
        if s.dt > now:
            future_shot = {"name": s.name, "date": s.dt.date()}
            break

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

    # exhibits: the five oldest screenshots; timestamp ties are broken by
    # filename so the order is deterministic
    exhibits = [
        {"name": s.name, "date": s.dt.date()}
        for s in sorted(shots, key=lambda s: (s.mtime, s.name))[:5]
    ]

    # formats: per-extension counts over the screenshots
    formats = dict(sorted(
        Counter(Path(s.path).suffix.lower().lstrip(".") for s in shots).items()
    ))

    # screenshot season: the meteorological season holding the most shots
    season_counts = Counter(_season_for_month(s.dt.month) for s in shots)
    season_name, season_total = sorted(
        season_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[0]
    season = {
        "name": season_name,
        "share": round(100.0 * season_total / count, 1) if count else 0.0,
    }

    three_am_count = sum(1 for s in shots if s.dt.hour in (2, 3))

    # silent months: whole calendar months inside the span with no shots
    months_present = {(s.dt.year, s.dt.month) for s in shots}
    silent_months = 0
    cursor_year, cursor_month = first_date.year, first_date.month
    while (cursor_year, cursor_month) <= (last_date.year, last_date.month):
        if (cursor_year, cursor_month) not in months_present:
            silent_months += 1
        cursor_month += 1
        if cursor_month == 13:
            cursor_year, cursor_month = cursor_year + 1, 1

    median_per_active_day = round(
        float(statistics.median(day_counts.values())), 1
    )

    # screenshot anniversary: the month-day with the most screenshots
    # summed across all years, once it has at least five to its name
    monthday_counts = Counter((s.dt.month, s.dt.day) for s in shots)
    (anniv_month, anniv_day), anniv_count = sorted(
        monthday_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[0]
    anniversary = None
    if anniv_count >= 5:
        anniversary = {
            "month_day": f"{date(2000, anniv_month, anniv_day):%b} {anniv_day}",
            "count": anniv_count,
            "years": len({
                s.dt.year for s in shots
                if (s.dt.month, s.dt.day) == (anniv_month, anniv_day)
            }),
        }

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
        "busiest_window": busiest_window,
        "heatmap": heatmap,
        "future_shot": future_shot,
        "duplicate_groups": dup_groups,
        "duplicate_files": dup_files,
        "duplicate_bytes_wasted": duplicate_bytes_wasted,
        "per_day": per_day,
        "largest_name": largest.name,
        "largest_size": largest.size,
        "largest_human": human_bytes(largest.size),
        "device_eras": device_eras,
        "exhibits": exhibits,
        "formats": formats,
        "season": season,
        "three_am_count": three_am_count,
        "silent_months": silent_months,
        "median_per_active_day": median_per_active_day,
        "anniversary": anniversary,
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


TIER_ORDER = [
    "Innocent Bystander",
    "Petty Offender",
    "Repeat Offender",
    "Habitual Hoarder",
    "Felony-Level Archivist",
    "Unrepentant Screenshot Felon",
]


def _normalize_tier_name(name: str) -> str:
    """Tier names compare loosely: case, spaces and hyphens are flexible."""
    return re.sub(r"[\s\-]+", " ", name.strip().lower())


TIER_RANKS = {
    _normalize_tier_name(tier): rank for rank, tier in enumerate(TIER_ORDER)
}


def tier_rank(tier: str) -> int:
    return TIER_RANKS[_normalize_tier_name(tier)]


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
        # the downloads docket (v0.9.0)
        "Superfund Site":
            "Sentence: maximum. Every file will be reviewed, named, and "
            "justified in writing. All of them.",
        "Hoarding Situation":
            "Sentence: a full audit of the downloads folder, this weekend. "
            "No extensions. No excuses.",
        "Landfill Adjacent":
            "Sentence: the folder is seized. Nothing new enters it until "
            "the backlog is halved.",
        "Digital Junk Drawer":
            "Sentence: weekly inspections of the downloads folder until "
            "this court says otherwise.",
        "Mild Clutter":
            "Sentence: the installers are deleted today. Not tomorrow. Today.",
        "Tidy Desk":
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
        # the downloads docket (v0.9.0)
        "Superfund Site":
            "Encouragement: every archive tells a story, and yours is an "
            "epic. There is no rush to change.",
        "Hoarding Situation":
            "Encouragement: you kept it all because it all mattered at the "
            "time. That is not a crime in this court.",
        "Landfill Adjacent":
            "Encouragement: a healthy collection. Sort one folder someday, "
            "if you ever feel like it.",
        "Digital Junk Drawer":
            "Encouragement: honestly, this is most people. A little tidy "
            "now and then, if the mood strikes.",
        "Mild Clutter":
            "Encouragement: barely a habit at all. Keep doing what you "
            "are doing.",
        "Tidy Desk":
            "Encouragement: case dismissed, with applause. Download "
            "something nice; you have earned the space.",
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

CUSTOM_VOICE_KEY = "custom"

# Award display overrides installed by judge packs, keyed by voice:
# {voice: {award key: {"name": str, "evidence": str}}}. Populated by
# register_judge_pack; consulted by compute_awards for that voice only.
AWARD_OVERRIDES: dict = {}


def load_judge_pack(path) -> dict:
    """Load and validate a custom judge pack (JSON). Raises SnapError
    with a plain message on any problem.

    Format: {"name": str, "intro": str (may use {tier} and {count}),
    "sentences": {tier name: sentence}, "award_lead": str,
    "awards": {award key: {"name": str, "evidence": str}}}. Every field
    except "name" is optional; tiers missing from "sentences" fall back
    to the fair judge's sentence for that tier. The optional "awards"
    object renames awards by their stable keys (see AWARD_KEYS): an
    entry may override the display "name", the "evidence" line (a
    format template filled with the award's own stat values), or both.
    Entries keyed by an unknown award key are warned about on stderr
    and ignored; they never break the run.
    """
    if not path.is_file():
        raise SnapError(f"judge file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SnapError(f"judge file is not valid JSON: {path}")
    if not isinstance(data, dict):
        raise SnapError("judge file must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SnapError('judge file needs a non-empty "name" string')
    intro = data.get("intro", "")
    if not isinstance(intro, str):
        raise SnapError('judge file "intro" must be a string')
    sentences = data.get("sentences", {})
    if not isinstance(sentences, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in sentences.items()
    ):
        raise SnapError(
            'judge file "sentences" must map tier names to sentences'
        )
    award_lead = data.get("award_lead", "")
    if not isinstance(award_lead, str):
        raise SnapError('judge file "award_lead" must be a string')
    awards = data.get("awards", {})
    if not isinstance(awards, dict):
        raise SnapError('judge file "awards" must be an object')
    clean_awards = {}
    for award_key, entry in awards.items():
        if award_key not in AWARD_KEYS:
            print(
                f"snapjudgment: warning: judge pack {path} renames an "
                f"unknown award key: {award_key!r}; entry ignored",
                file=sys.stderr,
            )
            continue
        if not isinstance(entry, dict) or not entry or any(
            field not in ("name", "evidence")
            or not isinstance(value, str)
            for field, value in entry.items()
        ):
            raise SnapError(
                'judge file "awards" entries must be objects holding '
                '"name" and/or "evidence" strings'
            )
        clean_awards[award_key] = dict(entry)
    return {
        "name": name,
        "intro": intro,
        "sentences": sentences,
        "award_lead": award_lead,
        "awards": clean_awards,
    }


def register_judge_pack(pack: dict) -> str:
    """Install a loaded pack as the custom voice. Returns its voice key."""
    key = CUSTOM_VOICE_KEY
    JUDGE_NAMES[key] = pack["name"]
    VOICE_SENTENCES[key] = dict(pack["sentences"])
    AWARD_OVERRIDES[key] = dict(pack.get("awards") or {})
    if pack["intro"]:
        JUDGE_INTROS[key] = pack["intro"]
    else:
        JUDGE_INTROS.pop(key, None)
    if pack["award_lead"]:
        AWARD_FRAMING[key] = (
            pack["award_lead"],
            "None. The court is as surprised as you are.",
        )
    else:
        AWARD_FRAMING.pop(key, None)
    return key


def _format_intro(intro: str, tier: str, count: int) -> str:
    """Fill {tier} and {count} placeholders in a judge's intro line."""
    try:
        return intro.format(tier=tier, count=f"{count:,}")
    except (IndexError, KeyError, ValueError):
        return intro


# ---------------------------------------------------------------------------
# roast intensity and court commentary
# ---------------------------------------------------------------------------

# The roast intensity runs 0-100 in five bands. Intensity is wording
# only: it flavors the court commentary and never touches the verdict,
# the counts, the awards, or the plea math. Commentary is opt-in: it
# appears only when --intensity (or the config key) asks for it.
INTENSITY_BANDS = ["gentle", "kind", "honest", "spicy", "roast"]

_INTENSITY_NAMES = {
    "gentle": 0,
    "kind": 1,
    "honest": 2,
    "spicy": 3,
    "roast": 4,
    "full roast": 4,
}

NO_COMMENTARY = (
    "No evidence, no commentary. The court does not roast an empty folder."
)

# One template per band (gentle, kind, honest, spicy, roast) for each
# case the court can comment on. Tokens: {night} night count, {share}
# night share, {count} total screenshots, {dup} duplicate extras,
# {streak} longest streak, {age} oldest screenshot's age in years.
# The roasts are about the habit, never the person. Ported verbatim
# from the browser demo (docs/demo.js).
COMMENTARY_TEMPLATES = {
    "night": [
        "The court sees {night} screenshots taken at night and simply notes the defendant keeps late hours.",
        "{night} screenshots after dark, {share}% of the whole folder. The court suspects the defendant's best ideas simply arrive after 10 PM.",
        "{night} of these screenshots, {share}%, were taken between 10 PM and 5 AM. That is not a habit. That is a second shift.",
        "{share}% of this folder was captured in the dark, {night} screenshots in total. The court is beginning to think the defendant considers daylight a rumor.",
        "{night} screenshots after 10 PM. The court has reviewed the defendant's sleep schedule and found it guilty too.",
    ],
    "hoard": [
        "The court counts {count} screenshots and sees a thorough archivist at work. Every one of them meant something once.",
        "{count} screenshots is a serious collection. The court admires the commitment, even if the filing system is more of a pile.",
        "{count} screenshots. The court notes the defendant has never once paused to think, perhaps I already have this one.",
        "{count} screenshots. Somewhere around the halfway mark this stopped being a folder and became a storage unit.",
        "{count} screenshots. The court would ask the defendant to produce one specific screenshot, but nobody involved has that kind of time. That is rather the point.",
    ],
    "dup": [
        "The court found {dup} screenshots that duplicate a sibling, and chooses to believe the defendant simply wanted to be sure.",
        "{dup} duplicate extras. The defendant screenshots the way nervous travelers check for their passport, repeatedly and just in case.",
        "{dup} of these screenshots are exact copies of another screenshot. The same moment, filed twice, for safekeeping.",
        "{dup} duplicates. The defendant's working theory appears to be that a screenshot does not count until it exists twice.",
        "{dup} duplicate screenshots. The court has now reviewed the same evidence multiple times, and it was not that compelling the first time.",
    ],
    "streak": [
        "The court notes a {streak}-day streak of screenshots and sees real consistency in it. Some people keep a journal. The defendant keeps screenshots.",
        "{streak} days in a row with at least one screenshot. The streak itself is impressive. The restraint is harder to find.",
        "{streak} consecutive days of screenshots. The court has seen shorter relationships, and less committed ones.",
        "{streak} straight days, not one missed. Whatever appeared on that screen, it never stood a chance of going uncaptured.",
        "A {streak}-day streak, unbroken. The court hereby promotes screenshotting from habit to lifestyle, effective retroactively.",
    ],
    "old": [
        "The oldest screenshot here is {age} years old, and the court finds something sweet in that. Some things are simply worth keeping.",
        "A screenshot from {age} years ago still lives in this folder. The court respects a long memory, even a digital one.",
        "The oldest evidence is {age} years old. The court asks, for the record, if the defendant remembers taking it, or why.",
        "{age} years old, the senior screenshot in this folder has survived new phones, new updates, and several opportunities to delete it.",
        "The court presents a screenshot {age} years old. It has been migrated from device to device like a family heirloom, except an heirloom is occasionally looked at.",
    ],
    "plain": [
        "The court has reviewed {count} screenshots and finds a perfectly ordinary folder. The defendant is free to go.",
        "{count} screenshots, and nothing the court has not seen a hundred times. A respectable, unremarkable collection.",
        "{count} screenshots. Not a crime scene, not a model of restraint. The court records a shrug and moves on.",
        "{count} screenshots, and the most notable thing about them is that there are {count} of them. The court expected either far more drama or far less folder.",
        "{count} screenshots. The court searched this folder for a scandal and found only volume. Even the commentary is disappointed.",
    ],
}


def intensity_level(value: int) -> int:
    """Band level (0-4) for a roast intensity value in 0-100."""
    if value < 20:
        return 0
    if value < 40:
        return 1
    if value < 60:
        return 2
    if value < 80:
        return 3
    return 4


def parse_intensity(raw) -> int:
    """Resolve an intensity setting to a band level (0-4).

    Accepts an integer 0-100 or a band name (gentle, kind, honest,
    spicy, roast; "full roast" also maps to roast), case-insensitively.
    Raises SnapError on anything else.
    """
    complaint = (
        f"invalid intensity: {raw!r}; use 0-100 or one of: "
        "gentle, kind, honest, spicy, roast"
    )
    if isinstance(raw, bool):
        raise SnapError(complaint)
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        name = re.sub(r"[\s\-]+", " ", raw.strip().lower())
        if name in _INTENSITY_NAMES:
            return _INTENSITY_NAMES[name]
        try:
            value = int(raw.strip())
        except ValueError:
            raise SnapError(complaint) from None
    else:
        raise SnapError(complaint)
    if not 0 <= value <= 100:
        raise SnapError(complaint)
    return intensity_level(value)


def _oldest_age_years(stats: dict, now: datetime) -> int:
    """Age of the oldest screenshot in whole years, on the same basis
    as The Archivist award: days since the oldest screenshot, with a
    year counted as 365 days."""
    return max(0, (now - stats["oldest_dt"]).days // 365)


def court_commentary(stats: dict, level: int,
                     now: datetime | None = None) -> str:
    """The court's commentary on a hearing: one line, flavored by the
    roast intensity band (level 0-4), aimed at the defendant's most
    damning stat. Priority: night share of 40% or more, then a count
    of 1,000 or more, then 10 or more duplicate extras, then a streak
    of 14 days or more, then an oldest screenshot 5 years old or
    more, then the plain count. Pure: reads stats, never writes."""
    if not stats or not stats.get("count"):
        return NO_COMMENTARY
    now = now or datetime.now()
    level = max(0, min(4, int(level)))
    dup_extras = stats["duplicate_files"] - stats["duplicate_groups"]
    age = _oldest_age_years(stats, now)
    values = {
        "night": stats["night_count"],
        "share": f"{stats['night_share']:.1f}",
        "count": f"{stats['count']:,}",
        "dup": dup_extras,
        "streak": stats["longest_streak"],
        "age": age,
    }
    if stats["night_share"] >= 40:
        key = "night"
    elif stats["count"] >= 1000:
        key = "hoard"
    elif dup_extras >= 10:
        key = "dup"
    elif stats["longest_streak"] >= 14:
        key = "streak"
    elif age >= 5:
        key = "old"
    else:
        key = "plain"
    return COMMENTARY_TEMPLATES[key][level].format(**values)


# Stable award keys for the screenshots court. Judge packs rename
# awards by these keys (see load_judge_pack); the keys never change,
# even when a display name does.
AWARD_KEYS = {
    "night_owl", "three_am_club", "burst_mode", "archivist",
    "receipt_keeper", "repeat_customer", "creature_of_habit",
    "gigabyte_club", "time_traveler", "long_haul", "lifer",
    "ancient_relic", "anniversary", "screenshot_streak",
    "clean_stretch", "minimalist",
}


def _award_record(key: str, name: str, evidence: str, **context) -> dict:
    """One award under consideration: its stable key, its built-in
    display name and evidence, and the stat values the evidence was
    built from (the context a judge pack's evidence template is
    formatted with)."""
    return {"key": key, "name": name, "evidence": evidence,
            "context": context}


def compute_awards(stats: dict, now: datetime | None = None,
                   voice: str = "fair") -> list[dict]:
    """The awards a hearing earns: [{key, name, evidence}].

    When the presiding voice is a judge pack carrying award overrides
    (AWARD_OVERRIDES), an override may replace the display name, the
    evidence (formatted with the award's own context values), or both.
    An evidence template that does not fit the context falls back to
    the built-in evidence. The math never changes: the same awards are
    earned either way.
    """
    now = now or datetime.now()
    records = []
    count = stats["count"]
    if stats["night_count"] >= 20 and stats["night_share"] >= 25:
        records.append(_award_record(
            "night_owl", "Night Owl",
            f"{stats['night_count']} screenshots between 10 PM and 5 AM "
            f"({stats['night_share']:.1f}% of the total).",
            night_count=stats["night_count"],
            night_share=stats["night_share"],
        ))
    if stats["three_am_count"] >= 10:
        records.append(_award_record(
            "three_am_club", "The 3 AM Club",
            f"{stats['three_am_count']} screenshots taken between "
            f"2 AM and 4 AM, when nothing on screen can be urgent.",
            three_am_count=stats["three_am_count"],
        ))
    if stats["burst_record"] >= 8:
        records.append(_award_record(
            "burst_mode", "Burst Mode",
            f"{stats['burst_record']} screenshots inside a single 60-second window.",
            burst_record=stats["burst_record"],
        ))
    if (now - stats["oldest_dt"]).days >= 3 * 365:
        records.append(_award_record(
            "archivist", "The Archivist",
            f"The oldest screenshot dates to {_fmt_date(stats['oldest_date'])}, "
            f"over three years ago, and it is still here.",
            oldest_name=stats["oldest_name"],
            oldest_date=_fmt_date(stats["oldest_date"]),
        ))
    if count >= 50 and stats["work_share"] >= 50:
        records.append(_award_record(
            "receipt_keeper", "Receipt Keeper",
            f"{stats['work_share']:.1f}% of shots were taken on weekdays between "
            f"9 AM and 5 PM. Very professional.",
            work_share=stats["work_share"],
        ))
    if stats["duplicate_files"] >= 10:
        records.append(_award_record(
            "repeat_customer", "Repeat Customer",
            f"{stats['duplicate_files']} files share an exact size and dimensions "
            f"with a sibling. The same screenshot, taken again.",
            duplicate_files=stats["duplicate_files"],
        ))
    habit_share = (
        round(100.0 * stats["busiest_hour_count"] / count, 1) if count else 0.0
    )
    if habit_share >= 25:
        records.append(_award_record(
            "creature_of_habit", "Creature of Habit",
            f"{habit_share:.1f}% of all screenshots were taken in the "
            f"{_fmt_hour(stats['busiest_hour'])} hour. Same time, same impulse.",
            habit_share=habit_share,
            busiest_hour=_fmt_hour(stats["busiest_hour"]),
        ))
    if stats["total_bytes"] >= 1024 ** 3:
        records.append(_award_record(
            "gigabyte_club", "Gigabyte Club",
            f"The screenshots alone total {stats['total_human']}. "
            f"The evidence locker needs a bigger room.",
            total_human=stats["total_human"],
            total_bytes=stats["total_bytes"],
        ))
    if stats.get("future_shot") is not None:
        future = stats["future_shot"]
        records.append(_award_record(
            "time_traveler", "Time Traveler",
            f"{future['name']} is dated {_fmt_date(future['date'])}, "
            f"which has not happened yet. The court admits it anyway.",
            future_name=future["name"],
            future_date=_fmt_date(future["date"]),
        ))
    if stats["span_days"] >= 4 * 365:
        records.append(_award_record(
            "long_haul", "The Long Haul",
            f"The collection spans {stats['span_days'] / 365.25:.1f} years of screenshots.",
            span_years=round(stats["span_days"] / 365.25, 1),
        ))
    if stats["span_days"] >= 8 * 365:
        records.append(_award_record(
            "lifer", "Lifer",
            f"The collection spans {stats['span_days'] / 365.25:.1f} years. "
            f"The screenshots have seniority now.",
            span_years=round(stats["span_days"] / 365.25, 1),
        ))
    if stats["span_days"] >= 10 * 365:
        records.append(_award_record(
            "ancient_relic", "Ancient Relic",
            f"{stats['oldest_name']} dates to "
            f"{_fmt_date(stats['oldest_date'])}, over ten years "
            f"before the newest screenshot in evidence.",
            oldest_name=stats["oldest_name"],
            oldest_date=_fmt_date(stats["oldest_date"]),
        ))
    if stats["anniversary"] is not None and stats["anniversary"]["count"] >= 15:
        anniv = stats["anniversary"]
        records.append(_award_record(
            "anniversary", "Anniversary",
            f"{anniv['count']} screenshots fall on {anniv['month_day']}, "
            f"gathered across {anniv['years']} different years. "
            f"Some dates just attract evidence.",
            anniversary_count=anniv["count"],
            anniversary_day=anniv["month_day"],
            anniversary_years=anniv["years"],
        ))
    if stats["longest_streak"] >= 7:
        records.append(_award_record(
            "screenshot_streak", "Screenshot Streak",
            f"{stats['longest_streak']} consecutive days with at least one screenshot.",
            longest_streak=stats["longest_streak"],
        ))
    if stats["longest_gap_days"] >= 30:
        records.append(_award_record(
            "clean_stretch", "Clean Stretch",
            f"{stats['longest_gap_days']} days without a single screenshot. "
            f"The court's longest recorded moment of restraint.",
            longest_gap_days=stats["longest_gap_days"],
        ))
    if 0 < count < 50:
        records.append(_award_record(
            "minimalist", "Minimalist",
            f"Only {count} screenshots. The court rarely sees such restraint.",
            count=count,
        ))
    overrides = AWARD_OVERRIDES.get(voice) or {}
    awards = []
    for record in records:
        name, evidence = record["name"], record["evidence"]
        override = overrides.get(record["key"])
        if override:
            if "name" in override:
                name = override["name"]
            if "evidence" in override:
                try:
                    evidence = override["evidence"].format(
                        **record["context"]
                    )
                except (IndexError, KeyError, ValueError):
                    pass  # a template that does not fit: built-in stands
        awards.append({
            "key": record["key"], "name": name, "evidence": evidence,
        })
    return awards


# ---------------------------------------------------------------------------
# the plea bargain: a purely hypothetical cleanup, computed read-only
# ---------------------------------------------------------------------------

def _minus_years(d: date, years: int) -> date:
    """A date `years` before d (Feb 29 lands on Feb 28)."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def compute_plea_bargain(shots: list[Shot], plea_years: int,
                         voice: str = "fair") -> dict:
    """What would happen IF the defendant deleted (a) every duplicate
    suspect except one keeper per exact size+dimensions group, and
    (b) every screenshot older than `plea_years` years, measured from
    the newest screenshot's date. Nothing is deleted; this is math."""
    newest_date = max(s.dt.date() for s in shots)
    cutoff = _minus_years(newest_date, plea_years)

    groups: dict = defaultdict(list)
    for s in shots:
        if s.dims is not None:
            groups[(s.size, s.dims)].append(s)
    duplicate_extra_ids = set()
    for members in groups.values():
        if len(members) >= 2:
            members = sorted(members, key=lambda s: (s.mtime, s.name))
            duplicate_extra_ids.update(id(s) for s in members[1:])

    removed_old = 0
    removed_duplicates = 0
    removed_bytes = 0
    removed_paths = []
    for s in shots:
        is_old = s.dt.date() < cutoff
        is_duplicate = id(s) in duplicate_extra_ids
        if is_old:
            removed_old += 1
            removed_bytes += s.size
            removed_paths.append(s.path)
        elif is_duplicate:
            removed_duplicates += 1
            removed_bytes += s.size
            removed_paths.append(s.path)
    removed_count = removed_old + removed_duplicates
    remaining = len(shots) - removed_count
    projected_tier, _sentence = verdict_for(remaining, voice=voice)
    return {
        "years": plea_years,
        "cutoff": cutoff,
        "old_removed": removed_old,
        "duplicate_removed": removed_duplicates,
        "removed_count": removed_count,
        "removed_bytes": removed_bytes,
        "removed_human": human_bytes(removed_bytes),
        "removed_paths": sorted(removed_paths),
        "remaining_count": remaining,
        "projected_tier": projected_tier,
    }


def plea_closing(plea: dict, current_tier: str) -> str:
    projected_rank = TIER_RANKS.get(
        _normalize_tier_name(plea["projected_tier"]), -1
    )
    if projected_rank < tier_rank(current_tier):
        return "The court would accept this plea."
    return "The court notes this plea changes nothing. The verdict stands."


def render_plea_bargain(plea: dict, current_tier: str) -> str:
    out = [
        "THE PLEA BARGAIN",
        f"  If the defendant deleted {plea['old_removed']:,} screenshots older "
        f"than {plea['years']} year{'s' if plea['years'] != 1 else ''} and "
        f"{plea['duplicate_removed']:,} duplicate extras - a purely "
        f"hypothetical exercise; this court deletes nothing - then:",
        f"  {'Files removed':<30} {plea['removed_count']:,}",
        f"  {'Space reclaimed':<30} {plea['removed_human']} "
        f"({plea['removed_bytes']:,} bytes)",
        f"  {'Remaining evidence':<30} {plea['remaining_count']:,} screenshots",
        f"  {'Projected verdict':<30} {plea['projected_tier']}",
        "",
        f"  {plea_closing(plea, current_tier)}",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# the rehabilitation plan: the sentence, served in stages
# ---------------------------------------------------------------------------

REHAB_YEARS = 2
REHAB_HEAVY_COUNT = 10
REHAB_REVIEW_SECONDS = 5
REHAB_SESSION_SIZE = 50


def compute_rehabilitation(shots: list[Shot],
                           voice: str = "fair") -> dict:
    """A staged cleanup plan, assigned by the court and served by the
    defendant. The screenshots are partitioned into three steps, in
    order, and every screenshot is claimed by at most one step:

    1. duplicate extras: every duplicate suspect beyond one keeper
       per exact size+dimensions group (as in the plea bargain);
    2. ancient evidence: the screenshots older than REHAB_YEARS
       years, measured from the newest screenshot, that step 1 did
       not already claim (the plea bargain counts an old duplicate
       as old; the plan never assigns the same file twice);
    3. heavy evidence: the REHAB_HEAVY_COUNT largest screenshots
       still standing after the first two steps, assigned only when
       more than that many would remain.

    Each step reports its own files and bytes and the cumulative
    position after serving it: how much evidence remains and what
    verdict the remainder would earn. Purely a plan; this court
    deletes, moves, and modifies nothing, ever."""
    total_bytes = sum(s.size for s in shots)
    newest_date = max(s.dt.date() for s in shots)
    cutoff = _minus_years(newest_date, REHAB_YEARS)

    groups: dict = defaultdict(list)
    for s in shots:
        if s.dims is not None:
            groups[(s.size, s.dims)].append(s)
    duplicate_ids = set()
    for members in groups.values():
        if len(members) >= 2:
            members = sorted(members, key=lambda s: (s.mtime, s.name))
            duplicate_ids.update(id(s) for s in members[1:])

    ancient_ids = {
        id(s) for s in shots
        if id(s) not in duplicate_ids and s.dt.date() < cutoff
    }

    claimed = duplicate_ids | ancient_ids
    pool = [s for s in shots if id(s) not in claimed]
    heavy_ids = set()
    if len(pool) > REHAB_HEAVY_COUNT:
        heavy = sorted(
            pool, key=lambda s: (-s.size, s.mtime, s.name)
        )[:REHAB_HEAVY_COUNT]
        heavy_ids = {id(s) for s in heavy}

    step_specs = [
        ("duplicate_extras", "Clear the duplicate extras",
         "Every duplicate suspect beyond one keeper per exact "
         "size-and-dimensions group.",
         duplicate_ids),
        ("ancient_evidence", "Retire the ancient evidence",
         f"Screenshots older than {REHAB_YEARS} years, measured "
         f"from the newest screenshot, that step 1 did not claim.",
         ancient_ids),
        ("heavy_evidence", "Move out the heavy evidence",
         f"The {REHAB_HEAVY_COUNT} largest screenshots still "
         f"standing after the first two steps.",
         heavy_ids),
    ]
    steps = []
    cumulative_count = 0
    cumulative_bytes = 0
    for key, name, detail, ids in step_specs:
        members = [s for s in shots if id(s) in ids]
        step_bytes = sum(s.size for s in members)
        cumulative_count += len(members)
        cumulative_bytes += step_bytes
        remaining = len(shots) - cumulative_count
        projected_tier, _sentence = verdict_for(remaining, voice=voice)
        steps.append({
            "key": key,
            "name": name,
            "detail": detail,
            "count": len(members),
            "bytes": step_bytes,
            "human": human_bytes(step_bytes),
            "cumulative_count": cumulative_count,
            "cumulative_bytes": cumulative_bytes,
            "remaining_count": remaining,
            "projected_tier": projected_tier,
        })
    removed_count = cumulative_count
    removed_bytes = cumulative_bytes
    remaining_count = len(shots) - removed_count
    projected_tier, _sentence = verdict_for(remaining_count, voice=voice)
    review_minutes = (
        -(-removed_count * REHAB_REVIEW_SECONDS // 60) if removed_count
        else 0
    )
    sessions = (
        -(-removed_count // REHAB_SESSION_SIZE) if removed_count else 0
    )
    return {
        "years": REHAB_YEARS,
        "cutoff": cutoff,
        "steps": steps,
        "removed_count": removed_count,
        "removed_bytes": removed_bytes,
        "removed_human": human_bytes(removed_bytes),
        "reclaim_share_pct": (
            round(100.0 * removed_bytes / total_bytes, 1)
            if total_bytes else 0.0
        ),
        "remaining_count": remaining_count,
        "projected_tier": projected_tier,
        "review_seconds_per_file": REHAB_REVIEW_SECONDS,
        "review_minutes": review_minutes,
        "session_size": REHAB_SESSION_SIZE,
        "sessions": sessions,
    }


def rehab_closing(rehab: dict, current_tier: str) -> str:
    """The court's outlook on the plan, in the plea closing's style."""
    if rehab["removed_count"] == 0:
        return (
            "No rehabilitation required. The record is already as "
            "clean as this court can ask."
        )
    projected_rank = TIER_RANKS.get(
        _normalize_tier_name(rehab["projected_tier"]), -1
    )
    if projected_rank < tier_rank(current_tier):
        return (
            "The court finds the defendant rehabilitable: finish "
            "the plan and the verdict improves."
        )
    return (
        "The plan reclaims space but not standing: the verdict "
        "would hold. Rehabilitation is its own reward."
    )


def render_rehabilitation(rehab: dict, current_tier: str) -> str:
    """The terminal REHABILITATION PLAN section printed by --rehab."""
    out = [
        "THE REHABILITATION PLAN",
        "  The court assigns the following plan, in order. Each step "
        "claims only",
        "  files no earlier step claimed, and nothing is deleted, "
        "moved, or",
        "  modified by this court; the plan is yours to serve.",
    ]
    active = [step for step in rehab["steps"] if step["count"]]
    if not active:
        out.append("  No steps assigned: there is nothing to clear.")
    for index, step in enumerate(active, start=1):
        out += [
            f"  Step {index} - {step['name']}: {step['count']:,} "
            f"files, {step['human']} ({step['bytes']:,} bytes)",
            f"    {step['detail']}",
            f"    After this step: {step['remaining_count']:,} "
            f"screenshots remain (projected verdict: "
            f"{step['projected_tier']})",
        ]
    out += [
        f"  {'Files assigned':<30} {rehab['removed_count']:,}",
        f"  {'Space reclaimed':<30} {rehab['removed_human']} "
        f"({rehab['removed_bytes']:,} bytes, "
        f"{rehab['reclaim_share_pct']:.1f}% of the evidence)",
        f"  {'Remaining evidence':<30} "
        f"{rehab['remaining_count']:,} screenshots",
        f"  {'Projected verdict':<30} {rehab['projected_tier']}",
        f"  {'Estimated review time':<30} {rehab['review_minutes']:,} "
        f"minute{'s' if rehab['review_minutes'] != 1 else ''} at "
        f"{rehab['review_seconds_per_file']} seconds per file",
        f"  {'Sessions to serve':<30} {rehab['sessions']:,} "
        f"(at most {rehab['session_size']} files per session)",
        "",
        f"  {rehab_closing(rehab, current_tier)}",
        "",
    ]
    return "\n".join(out)


def rehab_json(rehab: dict, current_tier: str) -> dict:
    """The rehabilitation plan as it appears in --json."""
    return {
        "years": rehab["years"],
        "steps": [
            {
                "key": step["key"],
                "name": step["name"],
                "count": step["count"],
                "bytes": step["bytes"],
                "human": step["human"],
                "cumulative_count": step["cumulative_count"],
                "cumulative_bytes": step["cumulative_bytes"],
                "remaining_count": step["remaining_count"],
                "projected_tier": step["projected_tier"],
            }
            for step in rehab["steps"]
        ],
        "removed_count": rehab["removed_count"],
        "removed_bytes": rehab["removed_bytes"],
        "removed_human": rehab["removed_human"],
        "reclaim_share_pct": rehab["reclaim_share_pct"],
        "remaining_count": rehab["remaining_count"],
        "projected_tier": rehab["projected_tier"],
        "review_minutes": rehab["review_minutes"],
        "sessions": rehab["sessions"],
        "outlook": rehab_closing(rehab, current_tier),
    }


def render_rehabilitation_markdown(rehab: dict,
                                   current_tier: str) -> list[str]:
    """The '## Rehabilitation Plan' section lines for --out."""
    out = ["## Rehabilitation Plan", ""]
    out.append(
        "The court assigns the following plan, in order. Each step "
        "claims only files no earlier step claimed, and nothing is "
        "deleted, moved, or modified by this court; the plan is "
        "yours to serve."
    )
    out.append("")
    active = [step for step in rehab["steps"] if step["count"]]
    if not active:
        out.append("No steps assigned: there is nothing to clear.")
    for index, step in enumerate(active, start=1):
        out.append(
            f"- **Step {index} - {step['name']}:** "
            f"{step['count']:,} files, {step['human']} "
            f"({step['bytes']:,} bytes). {step['detail']} After this "
            f"step: {step['remaining_count']:,} screenshots remain "
            f"(projected verdict: **{step['projected_tier']}**)."
        )
    out += [
        "",
        f"Serving the whole plan would assign "
        f"{rehab['removed_count']:,} files and reclaim "
        f"{rehab['removed_human']} ({rehab['removed_bytes']:,} bytes, "
        f"{rehab['reclaim_share_pct']:.1f}% of the evidence), leaving "
        f"{rehab['remaining_count']:,} screenshots and a projected "
        f"verdict of **{rehab['projected_tier']}**. Estimated review "
        f"time: {rehab['review_minutes']:,} "
        f"minute{'s' if rehab['review_minutes'] != 1 else ''} at "
        f"{rehab['review_seconds_per_file']} seconds per file, in "
        f"{rehab['sessions']:,} "
        f"session{'s' if rehab['sessions'] != 1 else ''} of at most "
        f"{rehab['session_size']} files.",
        "",
        rehab_closing(rehab, current_tier),
        "",
    ]
    return out


# ---------------------------------------------------------------------------
# the appeals court: the verdict, re-heard on its factors
# ---------------------------------------------------------------------------

APPEAL_REDUCTION_MARGIN = 3
APPEAL_COMMUTATION_MARGIN = 1
APPEAL_COMMUTED_SENTENCE = (
    "Sentence on appeal: commuted to time served. The folder is "
    "released on its own recognizance."
)

APPEAL_RULING_LABELS = {
    "granted": "Appeal granted",
    "commuted": "Sentence commuted",
    "denied": "Appeal denied",
}


def compute_appeal(stats: dict, tier: str, sentence: str) -> dict:
    """A re-hearing of the verdict on its factors. Every statistic
    the trial court already computed is entered as a mitigating
    factor (it argues for mercy) or an aggravating factor (it argues
    against), each with its evidence, and the panel rules by a
    stated rule, never by vibes:

    - mitigating factors outnumber aggravating factors by
      APPEAL_REDUCTION_MARGIN or more: the appeal is granted and
      the verdict is reduced by exactly one rung on the ladder
      (never below Innocent Bystander, where it stands and the
      sentence is commuted instead);
    - mitigating factors outnumber aggravating factors by at least
      APPEAL_COMMUTATION_MARGIN: the verdict stands and the
      sentence is commuted;
    - otherwise: the appeal is denied and both stand.

    The original verdict and sentence are always reported beside
    the appealed ones: an appeal re-hears the case, it does not
    rewrite the record."""
    mitigating = []
    aggravating = []

    def factor(key, name, evidence):
        return {"key": key, "name": name, "evidence": evidence}

    if stats["night_share"] < 20:
        mitigating.append(factor(
            "daylight_record", "A daylight record",
            f"Only {stats['night_share']:.1f}% of the screenshots "
            f"were taken at night.",
        ))
    if stats["duplicate_files"] == 0:
        mitigating.append(factor(
            "no_duplicate_waste", "No duplicate waste",
            "Not one duplicate suspect in the evidence.",
        ))
    if stats["longest_gap_days"] >= 60:
        mitigating.append(factor(
            "shows_restraint", "Shows restraint",
            f"A {stats['longest_gap_days']:,}-day silence in the "
            f"record: the defendant can stop.",
        ))
    if stats["median_per_active_day"] <= 2:
        mitigating.append(factor(
            "measured_pace", "A measured pace",
            f"A median of {stats['median_per_active_day']:g} "
            f"screenshots per active day.",
        ))
    if stats["retention_old_share"] < 10:
        mitigating.append(factor(
            "little_ancient_evidence", "Little ancient evidence",
            f"Only {stats['retention_old_share']:.1f}% of the "
            f"evidence is over a year old.",
        ))

    if stats["night_share"] >= 40:
        aggravating.append(factor(
            "night_habit", "A night habit",
            f"{stats['night_share']:.1f}% of the screenshots were "
            f"taken at night.",
        ))
    if stats["three_am_count"] >= 5:
        aggravating.append(factor(
            "three_am_activity", "3 AM activity",
            f"{stats['three_am_count']:,} screenshots between "
            f"2 and 4 AM.",
        ))
    if stats["duplicate_files"] >= 10:
        aggravating.append(factor(
            "duplicate_waste", "Duplicate waste",
            f"{stats['duplicate_files']:,} duplicate suspects, "
            f"{human_bytes(stats['duplicate_bytes_wasted'])} of "
            f"wasted evidence.",
        ))
    if stats["burst_record"] >= 8:
        aggravating.append(factor(
            "burst_behavior", "Burst behavior",
            f"{stats['burst_record']} screenshots inside a single "
            f"60-second window.",
        ))
    if stats["longest_streak"] >= 14:
        aggravating.append(factor(
            "relentless_streak", "A relentless streak",
            f"{stats['longest_streak']:,} consecutive days with "
            f"at least one screenshot.",
        ))
    if stats["busiest_day_count"] >= 25:
        aggravating.append(factor(
            "single_day_haul", "A single-day haul",
            f"{stats['busiest_day_count']:,} screenshots on "
            f"{_fmt_date(stats['busiest_day'])} alone.",
        ))

    net = len(mitigating) - len(aggravating)
    rank = tier_rank(tier)
    if net >= APPEAL_REDUCTION_MARGIN:
        ruling = "granted"
        appealed_tier = TIER_ORDER[max(0, rank - 1)]
        appealed_sentence = APPEAL_COMMUTED_SENTENCE
    elif net >= APPEAL_COMMUTATION_MARGIN:
        ruling = "commuted"
        appealed_tier = tier
        appealed_sentence = APPEAL_COMMUTED_SENTENCE
    else:
        ruling = "denied"
        appealed_tier = tier
        appealed_sentence = sentence
    return {
        "mitigating": mitigating,
        "aggravating": aggravating,
        "net": net,
        "reduction_margin": APPEAL_REDUCTION_MARGIN,
        "commutation_margin": APPEAL_COMMUTATION_MARGIN,
        "ruling": ruling,
        "original_tier": tier,
        "original_sentence": sentence,
        "appealed_tier": appealed_tier,
        "appealed_sentence": appealed_sentence,
    }


def appeal_closing(appeal: dict) -> str:
    """The panel's reasoning, stated with the counts it rests on."""
    mitigating_count = len(appeal["mitigating"])
    aggravating_count = len(appeal["aggravating"])
    if appeal["ruling"] == "granted":
        if appeal["appealed_tier"] != appeal["original_tier"]:
            return (
                f"Appeal granted. The mitigating factors outweighed "
                f"the aggravating {mitigating_count} to "
                f"{aggravating_count}, and under the "
                f"{appeal['reduction_margin']}-factor rule the "
                f"verdict is reduced on appeal from "
                f"{appeal['original_tier']} to "
                f"{appeal['appealed_tier']}. The original verdict "
                f"stays in the court record."
            )
        return (
            f"Appeal granted. The mitigating factors outweighed "
            f"the aggravating {mitigating_count} to "
            f"{aggravating_count}, but the verdict is already the "
            f"lowest on the ladder, so it stands at "
            f"{appeal['original_tier']} and the sentence is "
            f"commuted instead."
        )
    if appeal["ruling"] == "commuted":
        return (
            f"Appeal heard. The mitigating factors outweighed the "
            f"aggravating {mitigating_count} to "
            f"{aggravating_count}: not enough for a reduction "
            f"under the {appeal['reduction_margin']}-factor rule, "
            f"but enough to commute the sentence. The verdict "
            f"stands at {appeal['original_tier']}."
        )
    return (
        f"Appeal denied. The aggravating factors matched or "
        f"outweighed the mitigating, {aggravating_count} to "
        f"{mitigating_count}. The verdict and the sentence stand."
    )


def render_appeal(appeal: dict) -> str:
    """The terminal APPEALS COURT section printed by --appeal."""
    out = [
        "THE APPEALS COURT",
        "  The court re-hears the verdict on its factors. Mitigating",
        "  factors argue for mercy; aggravating factors argue",
        f"  against. Under the {appeal['reduction_margin']}-factor "
        "rule, mitigating",
        "  factors must outnumber aggravating factors by that many",
        "  to reduce the verdict by one rung; outnumbering them at",
        "  all commutes the sentence.",
    ]
    for kind in ("mitigating", "aggravating"):
        factors = appeal[kind]
        out.append(f"  {kind.capitalize()} factors ({len(factors)})")
        if not factors:
            out.append("    - none entered")
        for entry in factors:
            out.append(f"    - {entry['name']}: {entry['evidence']}")
    out += [
        f"  {'Original verdict':<30} {appeal['original_tier']}",
        f"  {'Ruling':<30} "
        f"{APPEAL_RULING_LABELS[appeal['ruling']]}",
        f"  {'Verdict on appeal':<30} {appeal['appealed_tier']}",
        f"  {'Sentence on appeal':<30} "
        f"{appeal['appealed_sentence']}",
        "",
        f"  {appeal_closing(appeal)}",
        "",
    ]
    return "\n".join(out)


def appeal_json(appeal: dict) -> dict:
    """The appeal as it appears in --json."""
    return {
        "mitigating": appeal["mitigating"],
        "aggravating": appeal["aggravating"],
        "mitigating_count": len(appeal["mitigating"]),
        "aggravating_count": len(appeal["aggravating"]),
        "net": appeal["net"],
        "reduction_margin": appeal["reduction_margin"],
        "commutation_margin": appeal["commutation_margin"],
        "ruling": appeal["ruling"],
        "ruling_label": APPEAL_RULING_LABELS[appeal["ruling"]],
        "original_tier": appeal["original_tier"],
        "original_sentence": appeal["original_sentence"],
        "appealed_tier": appeal["appealed_tier"],
        "appealed_sentence": appeal["appealed_sentence"],
        "closing": appeal_closing(appeal),
    }


def render_appeal_markdown(appeal: dict) -> list[str]:
    """The '## Appeals Court' section lines for --out."""
    out = ["## Appeals Court", ""]
    out.append(
        f"The court re-hears the verdict on its factors. Under "
        f"the {appeal['reduction_margin']}-factor rule, "
        f"mitigating factors must outnumber aggravating factors "
        f"by that many to reduce the verdict by one rung; "
        f"outnumbering them at all commutes the sentence."
    )
    for kind in ("mitigating", "aggravating"):
        factors = appeal[kind]
        out += ["", f"**{kind.capitalize()} factors "
                    f"({len(factors)}):**", ""]
        if not factors:
            out.append("- none entered")
        for entry in factors:
            out.append(f"- **{entry['name']}:** {entry['evidence']}")
    out += [
        "",
        f"Original verdict: **{appeal['original_tier']}**. Ruling: "
        f"**{APPEAL_RULING_LABELS[appeal['ruling']]}**. Verdict on "
        f"appeal: **{appeal['appealed_tier']}**. "
        f"{appeal['appealed_sentence']}",
        "",
        appeal_closing(appeal),
        "",
    ]
    return out


# ---------------------------------------------------------------------------
# the downloads docket: a second court, for the downloads folder
# ---------------------------------------------------------------------------

DL_INSTALLER_EXTS = {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm",
                     ".appimage"}
DL_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}

DL_TIERS = [
    (1000, "Superfund Site",
     "Sentence: the court recommends hazmat suits and a weekend. "
     "Bring help."),
    (500, "Hoarding Situation",
     "Sentence: delete nothing. We both know you won't."),
    (250, "Landfill Adjacent",
     "Sentence: mandatory organization, suspended, because we both "
     "know it won't happen."),
    (100, "Digital Junk Drawer",
     "Sentence: probation. The downloads folder gets a check-in "
     "once a month."),
    (25, "Mild Clutter",
     "Sentence: community service. Delete one installer. Just one. "
     "Baby steps."),
    (1, "Tidy Desk",
     "Sentence: case dismissed. Keep the desk exactly as it is."),
]

DL_TIER_ORDER = [
    "Tidy Desk",
    "Mild Clutter",
    "Digital Junk Drawer",
    "Landfill Adjacent",
    "Hoarding Situation",
    "Superfund Site",
]

DL_TIER_RANKS = {
    _normalize_tier_name(tier): rank
    for rank, tier in enumerate(DL_TIER_ORDER)
}

DL_FOOTER = (
    "Judged from file names, sizes, and dates only. "
    "No file was opened."
)


def dl_tier_rank(tier: str) -> int:
    return DL_TIER_RANKS[_normalize_tier_name(tier)]


def downloads_verdict_for(count: int, voice: str = "fair"):
    """(tier name, sentence) for a downloads file count."""
    if count <= 0:
        return ("No Evidence", "")
    for minimum, name, sentence in DL_TIERS:
        if count >= minimum:
            if voice in VOICE_SENTENCES:
                sentence = VOICE_SENTENCES[voice].get(name, sentence)
            return (name, sentence)
    return ("Tidy Desk", DL_TIERS[-1][2])


@dataclass
class DlFile:
    path: str
    name: str
    size: int
    mtime: float
    dt: datetime
    ext: str


def collect_downloads(folders, skipped=None, recursive: bool = False) -> list:
    """Every file directly inside the folders (the whole tree, when
    `recursive` is set).

    The downloads court judges whatever is lying on the surface: any
    type, no name hints, no header reads. A file that cannot be read
    is skipped without crashing; when `skipped` is a one-element
    list, the number of such files is added to skipped[0].
    """
    files: list[DlFile] = []
    skipped_count = 0
    for folder in folders:
        if recursive:
            entries = [
                Path(root) / name
                for root, _dirs, names in os.walk(
                    folder, onerror=_walk_onerror
                )
                for name in names
            ]
        else:
            try:
                entries = list(Path(folder).iterdir())
            except OSError:
                continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                st = entry.stat()
            except OSError:
                skipped_count += 1
                continue
            files.append(DlFile(
                path=str(entry),
                name=entry.name,
                size=st.st_size,
                mtime=st.st_mtime,
                dt=datetime.fromtimestamp(st.st_mtime),
                ext=entry.suffix.lower(),
            ))
    files.sort(key=lambda f: f.mtime)
    if skipped is not None:
        skipped[0] += skipped_count
    return files


_DL_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$|\s+copy(?:\s+\d+)?\s*$",
                           re.IGNORECASE)


def _dl_group_key(name: str):
    """The identity of a download with its copy suffixes stripped:
    'setup (2).exe', 'setup copy.exe' and 'setup.exe' share a key."""
    stem = Path(name).stem
    while True:
        stripped = _DL_SUFFIX_RE.sub("", stem)
        if stripped == stem:
            break
        stem = stripped
    return (stem.strip().lower(), Path(name).suffix.lower())


def analyze_downloads(files: list, now: datetime | None = None,
                      recursive: bool = False) -> dict:
    """Every statistic for a downloads hearing (metadata only).

    Duplicate downloads are grouped by name key; in recursive mode
    the grouping stays within each containing folder, so a setup.exe
    in two different folders is not a duplicate chain.
    """
    now = now or datetime.now()
    count = len(files)
    total_bytes = sum(f.size for f in files)

    installers = [f for f in files if f.ext in DL_INSTALLER_EXTS]
    archives = [f for f in files if f.ext in DL_ARCHIVE_EXTS]

    groups: dict = defaultdict(list)
    group_stems: dict = {}
    for f in files:
        name_key = _dl_group_key(f.name)
        group_key = (
            (str(Path(f.path).parent), name_key) if recursive
            else name_key
        )
        groups[group_key].append(f)
        group_stems[group_key] = name_key[0]
    duplicate_groups = 0
    duplicate_extra = 0
    duplicate_extra_bytes = 0
    duplicate_extra_paths: list = []
    for group_key, members in groups.items():
        if len(members) < 2:
            continue
        duplicate_groups += 1
        stem = group_stems[group_key]
        keeper = min(
            members,
            key=lambda f: (
                0 if Path(f.name).stem.lower() == stem else 1,
                f.name.lower(),
            ),
        )
        for member in members:
            if member is not keeper:
                duplicate_extra += 1
                duplicate_extra_bytes += member.size
                duplicate_extra_paths.append(member.path)

    oldest = min(files, key=lambda f: (f.mtime, f.name))
    largest = max(files, key=lambda f: (f.size, f.name))
    oldest_age_years = max(0, (now - oldest.dt).days // 365)

    ext_counts = Counter(f.ext or "(no extension)" for f in files)
    extensions = [
        {"ext": ext, "count": n}
        for ext, n in sorted(
            ext_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )[:8]
    ]

    old_count = sum(1 for f in files if (now - f.dt).days > 365)
    old_share = round(100.0 * old_count / count, 1) if count else 0.0

    first_date = files[0].dt.date()
    last_date = files[-1].dt.date()
    stats = {
        "count": count,
        "total_bytes": total_bytes,
        "total_human": human_bytes(total_bytes),
        "first_date": first_date,
        "last_date": last_date,
        "span_days": (last_date - first_date).days,
        "installer_count": len(installers),
        "installer_bytes": sum(f.size for f in installers),
        "installer_human": human_bytes(sum(f.size for f in installers)),
        "archive_count": len(archives),
        "archive_bytes": sum(f.size for f in archives),
        "duplicate_groups": duplicate_groups,
        "duplicate_extra": duplicate_extra,
        "duplicate_extra_bytes": duplicate_extra_bytes,
        "duplicate_extra_paths": sorted(duplicate_extra_paths),
        "oldest_name": oldest.name,
        "oldest_date": oldest.dt.date(),
        "oldest_dt": oldest.dt,
        "oldest_age_years": oldest_age_years,
        "largest_name": largest.name,
        "largest_size": largest.size,
        "largest_human": human_bytes(largest.size),
        "extensions": extensions,
        "old_count": old_count,
        "old_share": old_share,
        "torrent_count": sum(1 for f in files if f.ext == ".torrent"),
        "receipt_count": sum(
            1 for f in files
            if "invoice" in f.name.lower() or "receipt" in f.name.lower()
        ),
        "skipped": 0,
    }
    if recursive:
        folder_counts = Counter(
            str(Path(f.path).parent) for f in files
        )
        stats["folders"] = [
            {"path": path, "count": n}
            for path, n in sorted(
                folder_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ]
    return stats


def compute_downloads_awards(stats: dict) -> list[dict]:
    """The downloads court's own awards: [{key, name, evidence}]."""
    awards = []
    if stats["installer_count"] >= 10:
        awards.append({
            "key": "installer_archaeologist",
            "name": "Installer Archaeologist",
            "evidence": f"{stats['installer_count']} installers "
                        f"({stats['installer_human']}) kept after their "
                        f"work was done. The court has excavated older "
                        f"civilizations with fewer setup files.",
        })
    if stats["archive_count"] >= 15:
        awards.append({
            "key": "zip_hoarder",
            "name": "Zip Hoarder",
            "evidence": f"{stats['archive_count']} archives on disk, "
                        f"still zipped. Whatever is inside them, it is "
                        f"staying inside them.",
        })
    if stats["duplicate_extra"] >= 5:
        awards.append({
            "key": "double_downloader",
            "name": "Double Downloader",
            "evidence": f"{stats['duplicate_extra']} extra copies of "
                        f"files the defendant already had, in "
                        f"{stats['duplicate_groups']} duplicate sets. "
                        f"Downloaded twice, trusted once.",
        })
    if stats["oldest_age_years"] >= 3:
        awards.append({
            "key": "ancient_relic",
            "name": "Ancient Relic",
            "evidence": f"{stats['oldest_name']} dates to "
                        f"{_fmt_date(stats['oldest_date'])}, over three "
                        f"years ago, and it is still here.",
        })
    if stats["total_bytes"] >= 5 * 1024 ** 3:
        awards.append({
            "key": "gigabyte_landfill",
            "name": "Gigabyte Landfill",
            "evidence": f"The downloads alone total "
                        f"{stats['total_human']}. The landfill has its "
                        f"own weather now.",
        })
    if stats["torrent_count"] >= 1:
        n = stats["torrent_count"]
        awards.append({
            "key": "still_seeding",
            "name": "Still Seeding",
            "evidence": f"{n} torrent file{'s' if n != 1 else ''} on "
                        f"disk. The court does not ask what they point "
                        f"to. It never asks.",
        })
    if stats["receipt_count"] >= 1:
        n = stats["receipt_count"]
        awards.append({
            "key": "receipt_keeper_dx",
            "name": "Receipt Keeper DX",
            "evidence": f"{n} file{'s' if n != 1 else ''} with invoice "
                        f"or receipt in the name. The tax folder sends "
                        f"its regards.",
        })
    if stats["count"] and stats["old_share"] >= 60:
        awards.append({
            "key": "time_capsule",
            "name": "Time Capsule",
            "evidence": f"{stats['old_share']:.1f}% of these files are "
                        f"over a year old. This is less a folder than "
                        f"a stratum.",
        })
    return awards


# One template per band (gentle, kind, honest, spicy, roast) for each
# case the downloads court can comment on. Tokens: {dup} duplicate
# extras, {installers} installer count, {age} oldest file's age in
# years, {size} total size (human), {count} total files. Priority:
# duplicate extras of 5 or more, then 10 or more installers, then an
# oldest file 3 years old or more, then 5 GB or more, then the count.
DL_COMMENTARY_TEMPLATES = {
    "dup": [
        "The court found {dup} files that were downloaded more than once, and chooses to believe the defendant simply wanted to be very sure.",
        "{dup} duplicate downloads. The defendant downloads the way nervous travelers check for their passport, repeatedly and just in case.",
        "{dup} of these files are the same download, fetched again. The internet remembers, even when the defendant does not.",
        "{dup} duplicate downloads. The defendant's working theory appears to be that a file does not exist until it has been downloaded twice.",
        "{dup} duplicate downloads. The court has now reviewed the same installer multiple times, and it was not that compelling the first time.",
    ],
    "installers": [
        "The court counts {installers} installers and sees a well-equipped machine. Every one of them set something up once.",
        "{installers} installers kept on disk. The defendant holds onto the boxes the software came in, just in case.",
        "{installers} installers. The software is already installed. The installers remain, as a tribute.",
        "{installers} installers, kept long after the software moved in. The court notes the defendant could open a small museum of setup wizards.",
        "{installers} installers. The defendant has kept every key to every door ever opened on this machine, including the doors that were demolished.",
    ],
    "old": [
        "The oldest file here is {age} years old, and the court finds something sweet in that. Some things are simply worth keeping.",
        "A file from {age} years ago still lives in this folder. The court respects a long memory, even a digital one.",
        "The oldest evidence is {age} years old. The court asks, for the record, if the defendant remembers downloading it, or why.",
        "{age} years old, the senior file in this folder has survived new machines, new updates, and several opportunities to delete it.",
        "The court presents a file {age} years old. It has been carried from machine to machine like a family heirloom, except an heirloom is occasionally looked at.",
    ],
    "size": [
        "The court weighs the evidence at {size} and sees a thorough collector at work. Every byte meant something once.",
        "{size} of downloads. The court admires the commitment, even if the filing system is more of a pile.",
        "{size}. The court notes the defendant has never once paused to think, perhaps I already have this one.",
        "{size} of downloads. Somewhere around the halfway mark this stopped being a folder and became a storage unit.",
        "{size}. The court would ask the defendant to produce one specific file, but nobody involved has that kind of time. That is rather the point.",
    ],
    "plain": [
        "The court has reviewed {count} files and finds a perfectly ordinary folder. The defendant is free to go.",
        "{count} files, and nothing the court has not seen a hundred times. A respectable, unremarkable collection.",
        "{count} files. Not a crime scene, not a model of restraint. The court records a shrug and moves on.",
        "{count} files, and the most notable thing about them is that there are {count} of them. The court expected either far more drama or far less folder.",
        "{count} files. The court searched this folder for a scandal and found only volume. Even the commentary is disappointed.",
    ],
}


def downloads_commentary(stats: dict, level: int) -> str:
    """The downloads court's commentary: one line, flavored by the
    roast intensity band, aimed at the folder's most damning stat."""
    if not stats or not stats.get("count"):
        return NO_COMMENTARY
    level = max(0, min(4, int(level)))
    values = {
        "dup": stats["duplicate_extra"],
        "installers": stats["installer_count"],
        "age": stats["oldest_age_years"],
        "size": stats["total_human"],
        "count": f"{stats['count']:,}",
    }
    if stats["duplicate_extra"] >= 5:
        key = "dup"
    elif stats["installer_count"] >= 10:
        key = "installers"
    elif stats["oldest_age_years"] >= 3:
        key = "old"
    elif stats["total_bytes"] >= 5 * 1024 ** 3:
        key = "size"
    else:
        key = "plain"
    return DL_COMMENTARY_TEMPLATES[key][level].format(**values)


def _dl_evidence_lines(stats: dict) -> list[tuple[str, str]]:
    lines = [
        ("Total files", f"{stats['count']:,}"),
        ("Total size", f"{stats['total_human']} ({stats['total_bytes']:,} bytes)"),
        ("Largest file", f"{stats['largest_name']} ({stats['largest_human']})"),
        ("Date span", f"{_fmt_date(stats['first_date'])} - {_fmt_date(stats['last_date'])} "
                      f"({stats['span_days'] + 1:,} day{'s' if stats['span_days'] else ''})"),
        ("Oldest file", f"{stats['oldest_name']} ({_fmt_date(stats['oldest_date'])})"),
        ("Installers", f"{stats['installer_count']} ({stats['installer_human']})"),
        ("Archives", f"{stats['archive_count']}"),
        ("Duplicate downloads", f"{stats['duplicate_groups']} "
                                f"group{'s' if stats['duplicate_groups'] != 1 else ''}, "
                                f"{stats['duplicate_extra']} extra copies"),
    ]
    if stats["duplicate_extra_bytes"] > 0:
        lines.append(
            ("Duplicate waste", human_bytes(stats["duplicate_extra_bytes"]))
        )
    lines.append(
        ("Over a year old", f"{stats['old_count']} "
                            f"({stats['old_share']:.1f}%)")
    )
    if stats["extensions"]:
        lines.append(
            ("Top file types", ", ".join(
                f"{entry['ext']} {entry['count']}"
                for entry in stats["extensions"][:5]
            ))
        )
    folders = stats.get("folders")
    if folders:
        for index, entry in enumerate(folders[:5]):
            label = "Folders" if index == 0 else ""
            lines.append(
                (label, f"{entry['path']} - {entry['count']:,} "
                        f"file{'s' if entry['count'] != 1 else ''}")
            )
        if len(folders) > 5:
            lines.append(("", f"and {len(folders) - 5} more"))
    if stats.get("skipped"):
        lines.append(("Files the court could not read", f"{stats['skipped']}"))
    return lines


def render_downloads_no_evidence() -> str:
    return "\n".join([
        "=" * 62,
        "SNAP JUDGMENT - THE DOWNLOADS DOCKET",
        "=" * 62,
        "",
        "THE VERDICT",
        "  No Evidence",
        "",
        "  The court finds nothing. No files were produced as",
        "  evidence, which means this folder is innocent, empty, or",
        "  not where your downloads actually live.",
        "",
        DL_FOOTER,
        "",
    ])


def render_downloads_report(stats: dict, tier: str, sentence: str,
                            awards: list[dict], voice: str = "fair",
                            commentary: str | None = None,
                            year: int | None = None) -> str:
    out = [
        "=" * 62,
        "SNAP JUDGMENT - THE DOWNLOADS DOCKET",
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
        out.append(f"  {_format_intro(intro, tier, stats['count'])}")
    out += [
        f"  {tier}",
        f"  {stats['count']:,} files, {stats['total_human']} of evidence,",
        f"  spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.",
    ]
    if year is not None:
        out.append(f"  Scope: year {year}")
    out += [
        "",
        "THE EVIDENCE",
    ]
    for label, value in _dl_evidence_lines(stats):
        out.append(f"  {label:<30} {value}")
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
    out += ["", "THE SENTENCE", f"  {sentence}"]
    if commentary is not None:
        out.append(f"  COMMENTARY: {commentary}")
    out += ["", DL_FOOTER, ""]
    return "\n".join(out)


def render_downloads_markdown(stats: dict, tier: str, sentence: str,
                              awards: list[dict], voice: str = "fair",
                              commentary: str | None = None,
                              year: int | None = None,
                              rematch: dict | None = None,
                              rematch_requested: bool = False,
                              versus: dict | None = None,
                              trend: dict | None = None,
                              rehab: dict | None = None,
                              appeal: dict | None = None,
                              plea: dict | None = None) -> str:
    out = [
        "# Snap Judgment - The Downloads Docket",
        "",
        "## The Verdict",
        "",
        f"**{tier}** - {stats['count']:,} files, {stats['total_human']} of evidence, "
        f"spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.",
    ]
    intro = JUDGE_INTROS.get(voice)
    if intro:
        out.append(_format_intro(intro, tier, stats["count"]))
    if year is not None:
        out.append(f"Scope: year {year}")
    out += ["", "## The Evidence", ""]
    for label, value in _dl_evidence_lines(stats):
        out.append(f"- **{label}:** {value}")
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
    if commentary is not None:
        out += ["## Court Commentary", "", commentary, ""]
    if versus is not None:
        vs_stats, vs_tier = versus["stats"], versus["tier"]
        out += [
            "## Co-Defendant Comparison",
            "",
            "| | Defendant | Co-defendant |",
            "| --- | --- | --- |",
        ]
        for label, main_value, vs_value in _downloads_comparison_rows(
            stats, tier, vs_stats, vs_tier, awards, versus["awards"]
        ):
            out.append(f"| {label} | {main_value} | {vs_value} |")
        out += [
            "",
            _downloads_delta_line(stats, vs_stats),
            "",
            _downloads_comparison_closing(
                stats, tier, vs_stats, vs_tier, versus["worse"]
            ),
            "",
        ]
    if rematch_requested:
        out += ["## Rematch", ""]
        if rematch is None:
            out.append("No prior hearing on record for these folders.")
        else:
            out.append(downloads_rematch_line(rematch, tier))
        out.append("")
    if trend is not None:
        out += render_trend_markdown(trend, "files")
    if rehab is not None:
        out += render_downloads_rehabilitation_markdown(rehab, tier)
    if appeal is not None:
        out += render_downloads_appeal_markdown(appeal)
    if plea is not None:
        out += render_downloads_plea_markdown(plea, tier)
    out += [DL_FOOTER, ""]
    return "\n".join(out)


def build_downloads_json(stats: dict, tier: str, sentence: str,
                         awards: list[dict], folders: list[dict],
                         judge: str = "fair",
                         commentary: str | None = None) -> dict:
    data = {
        "version": __version__,
        "court": "downloads",
        "folders": folders,
        "verdict": tier,
        "sentence": sentence,
        "judge": judge,
        "count": stats["count"],
        "total_bytes": stats["total_bytes"],
        "total_human": stats["total_human"],
        "first_date": stats["first_date"].isoformat(),
        "last_date": stats["last_date"].isoformat(),
        "span_days": stats["span_days"],
        "oldest": {
            "name": stats["oldest_name"],
            "date": stats["oldest_date"].isoformat(),
            "age_years": stats["oldest_age_years"],
        },
        "largest": {
            "name": stats["largest_name"],
            "bytes": stats["largest_size"],
            "human": stats["largest_human"],
        },
        "installers": {
            "count": stats["installer_count"],
            "bytes": stats["installer_bytes"],
            "human": stats["installer_human"],
        },
        "archives": {
            "count": stats["archive_count"],
            "bytes": stats["archive_bytes"],
        },
        "duplicate_groups": stats["duplicate_groups"],
        "duplicate_extra": stats["duplicate_extra"],
        "duplicate_extra_bytes": stats["duplicate_extra_bytes"],
        "extensions": stats["extensions"],
        "old_count": stats["old_count"],
        "old_share": stats["old_share"],
        "torrent_count": stats["torrent_count"],
        "receipt_count": stats["receipt_count"],
        "skipped": stats.get("skipped", 0),
        "awards": awards,
    }
    if commentary is not None:
        data["commentary"] = commentary
    return data


def downloads_oneline(stats: dict, tier: str) -> str:
    """Exactly one line: verdict, file count, size, span."""
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}"
    return (
        f"{tier} · {stats['count']:,} files · "
        f"{stats['total_human']} · {span}"
    )


def _dl_headline_stat(stats: dict) -> tuple[str, str]:
    """The downloads card's headline stat: duplicate waste when there
    is any, otherwise the installer count. (value, label)"""
    if stats["duplicate_extra_bytes"] > 0:
        return (
            human_bytes(stats["duplicate_extra_bytes"]),
            "Duplicate waste",
        )
    return (f"{stats['installer_count']}", "Installers")


def render_downloads_card(stats: dict, tier: str, sentence: str,
                          awards: list[dict]) -> str:
    """The downloads docket's HTML share card: the screenshots card's
    layout, speaking for the docket."""
    gb = stats["total_bytes"] / (1024 ** 3)
    headline_value, headline_label = _dl_headline_stat(stats)
    top_stats = [
        (headline_label, headline_value),
        ("Archives", f"{stats['archive_count']}"),
        ("Over a year old", f"{stats['old_share']:.1f}%"),
    ]
    stats_html = "\n".join(
        f'      <div class="stat"><div class="stat-value">{html.escape(v)}</div>'
        f'<div class="stat-label">{html.escape(k)}</div></div>'
        for k, v in top_stats
    )
    if awards:
        awards_html = "\n".join(
            f"        <li><strong>{html.escape(a['name'])}</strong> - {html.escape(a['evidence'])}</li>"
            for a in awards[:2]
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
  .install {{ text-align: center; font-size: 10px; color: #6d6878; margin-top: 4px;
             font-family: 'Courier New', monospace; }}
</style>
</head>
<body>
  <div class="card">
    <div class="court">The Downloads Docket</div>
    <h1>Snap Judgment</h1>
    <div class="tier">{html.escape(tier)}</div>
    <div class="headline">
      <div><div class="num">{stats['count']:,}</div><div class="lbl">Files</div></div>
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
    <div class="foot">Judged from file names, sizes, and dates only. No file was opened. Snap Judgment, Ghost Developer Studio.</div>
    <div class="install">pip install snap-judgment</div>
  </div>
</body>
</html>
"""


def render_downloads_svg(stats: dict, tier: str, sentence: str,
                         awards: list[dict]) -> str:
    """The downloads docket's 1200x630 SVG share card."""
    esc = _xml_escape
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}"
    headline_value, headline_label = _dl_headline_stat(stats)
    stat_cells = [
        (f"{stats['count']:,}", "FILES", 42),
        (stats["total_human"], "OF EVIDENCE", 42),
        (span, "SPAN", 27),
        (headline_value, headline_label.upper(), 42),
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
        award_names = "  ·  ".join(a["name"] for a in awards[:2])
    else:
        award_names = "None. The court is as surprised as you are."
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630" role="img" aria-label="Snap Judgment verdict card">
  <rect width="1200" height="630" fill="#0d0d12"/>
  <rect x="24" y="24" width="1152" height="582" rx="14" fill="#16161f" stroke="#b08d3c" stroke-width="3"/>
  <text x="600" y="92" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="19" letter-spacing="6" fill="#b08d3c">THE DOWNLOADS DOCKET</text>
  <text x="600" y="158" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="58" font-weight="bold" fill="#e8e3d8">SNAP JUDGMENT</text>
  <text x="600" y="228" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="46" fill="#d4af5a">{esc(tier)}</text>
  <line x1="200" y1="262" x2="1000" y2="262" stroke="#33333f" stroke-width="1"/>
{stats_svg}
  <text x="600" y="448" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="14" letter-spacing="4" fill="#b08d3c">AWARDS</text>
  <text x="600" y="486" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="25" fill="#e8e3d8">{esc(award_names)}</text>
  <text x="600" y="540" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" font-style="italic" fill="#cfc9ba">{esc(sentence)}</text>
  <text x="600" y="574" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="16" fill="#6d6878">Judged from file names, sizes, and dates only. No file was opened.</text>
  <text x="600" y="598" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="13" letter-spacing="1" fill="#6d6878">pip install snap-judgment</text>
</svg>
"""


def render_downloads_png(stats: dict, tier: str, sentence: str,
                         awards: list[dict]) -> bytes:
    """The downloads docket's 1200x630 PNG share card, drawn with the
    bitmap font."""
    canvas = _Canvas(1200, 630, (16, 16, 20))
    canvas.rect(24, 24, 1176, 606, (22, 22, 31))
    canvas.border(24, 24, 1176, 606, (176, 141, 60), 3)
    accent = (176, 141, 60)
    gold = (212, 175, 90)
    paper = (232, 227, 216)
    muted = (154, 148, 166)
    faint = (109, 104, 120)
    canvas.text(600, 62, "THE DOWNLOADS DOCKET", 2, accent)
    canvas.text(600, 104, "SNAP JUDGMENT", 7, paper)
    canvas.text(600, 186, tier, _fit_scale(tier, 1080, 6), gold)
    canvas.rect(200, 252, 1000, 254, (51, 51, 63))
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}"
    stats_line = (
        f"{stats['count']:,} files - {stats['total_human']} - {span}"
    )
    canvas.text(600, 282, stats_line, _fit_scale(stats_line, 1080, 3), paper)
    headline_value, headline_label = _dl_headline_stat(stats)
    headline_line = f"{headline_label} {headline_value}"
    canvas.text(600, 348, headline_line,
                _fit_scale(headline_line, 1080, 3), muted)
    if awards:
        awards_line = "Awards: " + ", ".join(a["name"] for a in awards[:2])
    else:
        awards_line = "Awards: none"
    canvas.text(600, 402, awards_line, _fit_scale(awards_line, 1080, 2),
                paper)
    if sentence:
        canvas.text(600, 470, sentence, _fit_scale(sentence, 1080, 2),
                    (207, 201, 186))
    canvas.text(600, 566, DL_FOOTER, 2, faint)
    canvas.text(600, 590, "pip install snap-judgment", 1, faint)
    return canvas.to_png()


def render_downloads_html_report(stats: dict, tier: str, sentence: str,
                                 awards: list[dict], voice: str = "fair",
                                 commentary: str | None = None,
                                 year: int | None = None) -> str:
    """The downloads docket's full HTML report: the screenshots
    report's patterns, the docket's evidence."""
    esc = html.escape
    rows = "\n".join(
        f"      <tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>"
        for label, value in _dl_evidence_lines(stats)
    )
    sections = []
    intro = JUDGE_INTROS.get(voice)
    if intro:
        sections.append(
            f'    <p class="intro">{esc(_format_intro(intro, tier, stats["count"]))}</p>'
        )
    if year is not None:
        sections.append(f'    <p class="intro">Scope: year {year}</p>')
    sections.append(f"""    <h2>The Evidence</h2>
    <table>
{rows}
    </table>""")
    if awards:
        items = "\n".join(
            f"        <li><strong>{esc(a['name'])}</strong> - "
            f"{esc(a['evidence'])}</li>"
            for a in awards
        )
    else:
        items = "        <li>None. The court is as surprised as you are.</li>"
    sections.append(f"""    <h2>Awards</h2>
    <ul>
{items}
    </ul>""")
    body = "\n".join(sections)
    commentary_html = ""
    if commentary is not None:
        commentary_html = (
            f'\n    <h2>Court Commentary</h2>'
            f'\n    <p>{esc(commentary)}</p>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Snap Judgment - {esc(tier)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0d0d12; color: #e8e3d8; font-family: Georgia, 'Times New Roman', serif;
         display: flex; justify-content: center; padding: 40px 16px; }}
  .report {{ max-width: 720px; width: 100%; background: #16161f; border: 2px solid #b08d3c;
            border-radius: 10px; padding: 36px 40px; box-shadow: 0 12px 40px rgba(0,0,0,.6); }}
  .court {{ text-align: center; letter-spacing: 3px; font-size: 12px; color: #b08d3c;
           text-transform: uppercase; }}
  h1 {{ text-align: center; font-size: 30px; margin: 10px 0 4px; }}
  .tier {{ text-align: center; font-size: 24px; color: #d4af5a; margin-bottom: 6px; }}
  .summary {{ text-align: center; color: #cfc9ba; margin-bottom: 10px; }}
  .intro {{ text-align: center; font-style: italic; color: #9a94a6; margin-bottom: 8px; }}
  h2 {{ font-size: 13px; letter-spacing: 2px; color: #b08d3c; text-transform: uppercase;
       margin: 22px 0 10px; border-top: 1px solid #33333f; padding-top: 16px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 5px 4px; font-size: 14px; vertical-align: top; }}
  td:first-child {{ color: #9a94a6; width: 42%; }}
  ul {{ list-style: none; }}
  li {{ padding: 4px 0; font-size: 14px; }}
  .sentence {{ font-style: italic; color: #cfc9ba; border-top: 1px solid #33333f;
              padding-top: 14px; margin-top: 22px; }}
  .foot {{ text-align: center; font-size: 11px; color: #6d6878; margin-top: 16px; }}
</style>
</head>
<body>
  <div class="report">
    <div class="court">The Downloads Docket</div>
    <h1>Snap Judgment</h1>
    <div class="tier">{esc(tier)}</div>
    <div class="summary">{stats['count']:,} files, {esc(stats['total_human'])} of evidence,
      spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.</div>
{body}
    <div class="sentence">{esc(sentence)}</div>{commentary_html}
    <div class="foot">Judged from file names, sizes, and dates only. No file was opened. Snap Judgment, Ghost Developer Studio.</div>
  </div>
</body>
</html>
"""


def build_downloads_wrapped_posts(stats: dict, tier: str,
                                  awards: list[dict], year: int) -> str:
    """The downloads Wrapped kit's post.txt: a SHORT post (<=280
    chars) and a STANDARD post, in the docket's own wording."""
    short = (
        f"My {year} in downloads, judged by Snap Judgment: "
        f"{stats['count']:,} files, verdict {tier}, "
        f"{stats['installer_count']} installers kept, "
        f"{human_bytes(stats['duplicate_extra_bytes'])} of duplicate "
        f"waste. Names, sizes, and dates only; no file was opened. "
        f"{REPO_URL}"
    )
    if len(short) > 280:  # very large numbers: fall back to a tighter draft
        short = (
            f"My {year} in downloads, judged by Snap Judgment: "
            f"{stats['count']:,} files, verdict {tier}. "
            f"Names, sizes, and dates only; no file was opened. "
            f"{REPO_URL}"
        )
    if awards:
        awards_line = "Top awards: " + ", ".join(
            a["name"] for a in awards[:2]
        )
    else:
        awards_line = "Awards: none. The court is as surprised as you are."
    standard = "\n".join([
        f"My {year} in downloads, judged by Snap Judgment.",
        "",
        f"Verdict: {tier}",
        f"{stats['count']:,} files, {stats['total_human']} of evidence.",
        awards_line,
        "",
        "Snap Judgment reads file names, sizes, and dates only. "
        "It never opens the files.",
        REPO_URL,
    ])
    return "\n".join([
        "SHORT POST",
        short,
        "",
        "STANDARD POST",
        standard,
        "",
    ])


def write_downloads_csv(path, files: list) -> None:
    """Per-file records for every judged download: one row per file,
    categorized by the docket's own extension sets."""
    rows = sorted(files, key=lambda f: f.mtime)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["name", "size_bytes", "modified", "extension", "category"]
        )
        for f in rows:
            if f.ext in DL_INSTALLER_EXTS:
                category = "installer"
            elif f.ext in DL_ARCHIVE_EXTS:
                category = "archive"
            else:
                category = "other"
            writer.writerow(
                [f.name, f.size, f.dt.isoformat(), f.ext, category]
            )


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
        ("Median per active day", f"{stats['median_per_active_day']:.1f}"),
        ("Silent months", f"{stats['silent_months']}"),
        ("Longest daily streak", f"{stats['longest_streak']} days"),
    ]
    date_filter = stats.get("date_filter") or {}
    if date_filter.get("since") or date_filter.get("until"):
        df_since, df_until = date_filter.get("since"), date_filter.get("until")
        if df_since and df_until:
            df_value = f"{_fmt_date(df_since)} - {_fmt_date(df_until)}"
        elif df_since:
            df_value = f"from {_fmt_date(df_since)}"
        else:
            df_value = f"until {_fmt_date(df_until)}"
        lines.insert(4, ("Date filter", df_value))
    if stats["longest_gap_days"] >= 2:
        lines.append(
            ("Longest clean stretch",
             f"{stats['longest_gap_days']} days "
             f"({_fmt_date(stats['longest_gap_start'])} - {_fmt_date(stats['longest_gap_end'])})")
        )
    lines.append(
        ("Biggest burst", f"{stats['burst_record']} screenshots in 60 seconds")
    )
    window = stats.get("busiest_window")
    if window and window["count"] >= 3:
        window_start = window["start"]
        lines.append(
            ("Busiest hour window",
             f"{window['count']} screenshots in the hour from "
             f"{_fmt_date(window_start)} {window_start:%H:%M}")
        )
    lines.append(
        ("Duplicate suspects", f"{stats['duplicate_groups']} "
                               f"group{'s' if stats['duplicate_groups'] != 1 else ''}, "
                               f"{stats['duplicate_files']} files")
    )
    if stats["duplicate_bytes_wasted"] > 0:
        lines.append(
            ("Duplicate waste", human_bytes(stats["duplicate_bytes_wasted"]))
        )
    top_formats = sorted(
        stats["formats"].items(), key=lambda kv: (-kv[1], kv[0])
    )[:3]
    lines.append(
        ("Formats", ", ".join(
            f"{ext.upper()} {100.0 * n / stats['count']:.0f}%"
            for ext, n in top_formats
        ))
    )
    lines.append(
        ("Screenshot season",
         f"{stats['season']['name']} - {stats['season']['share']:.1f}%")
    )
    if stats["anniversary"] is not None:
        anniv = stats["anniversary"]
        lines.append(
            ("Screenshot anniversary",
             f"{anniv['month_day']} - {anniv['count']} screenshots "
             f"across {anniv['years']} years")
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
                  court_record: str | None = None,
                  folders: list[dict] | None = None,
                  commentary: str | None = None) -> str:
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
        out.append(f"  {_format_intro(intro, tier, stats['count'])}")
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
    if folders and len(folders) > 1:
        out += ["", "FOLDERS JUDGED"]
        for entry in folders:
            out.append(
                f"  {entry['path']} - {entry['count']:,} screenshots"
            )
    if stats["count"] >= 10 and stats["sources"]:
        out += ["", "THE CHARGES"]
        for source in stats["sources"]:
            out.append(
                f"  {source['name']:<30} {source['count']} ({source['share']:.1f}%)"
            )
    if stats["count"] >= 5 and stats["exhibits"]:
        out += ["", "EXHIBITS"]
        for letter, exhibit in zip("ABCDE", stats["exhibits"]):
            out.append(
                f"  Exhibit {letter} - {exhibit['name']} "
                f"({_fmt_date(exhibit['date'])})"
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
    out += ["", "THE SENTENCE", f"  {sentence}"]
    if commentary is not None:
        out.append(f"  COMMENTARY: {commentary}")
    out.append("")
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


def _year_change_closing(earlier_year: int, earlier_stats: dict,
                         later_year: int, later_stats: dict):
    """(closing line, signed % change) for later year measured on earlier."""
    earlier, later = earlier_stats["count"], later_stats["count"]
    change = round(100.0 * (later - earlier) / earlier, 1) if earlier else 0.0
    if later < earlier:
        line = (f"The court notes improvement: {later_year} is down "
                f"{abs(change):.0f}% on {earlier_year}.")
    elif later > earlier:
        line = (f"The court notes a relapse: {later_year} is up "
                f"{change:.0f}% on {earlier_year}.")
    else:
        line = (f"The court notes no change in behavior: {later_year} "
                f"matches {earlier_year}, screenshot for screenshot.")
    return line, change


def build_vs_year(shots: list[Shot], recordings: list[Shot],
                  target_year: int, voice: str = "fair") -> dict:
    """Compare one year's screenshots against the most recent other year
    present in the same collection (or the previous year, when the target
    IS the most recent). Raises SnapError when the comparison is impossible."""
    years_present = sorted({s.dt.year for s in shots})
    if len(years_present) < 2:
        raise SnapError(
            "--vs-year needs screenshots from at least two different years"
        )
    if target_year not in years_present:
        raise SnapError(
            f"no screenshots from {target_year} in the judged folders"
        )
    if target_year == years_present[-1]:
        other_year = years_present[-2]
    else:
        other_year = years_present[-1]

    def scope(y: int) -> dict:
        year_shots = [s for s in shots if s.dt.year == y]
        year_recordings = [r for r in recordings if r.dt.year == y]
        year_stats = analyze(year_shots, year_recordings)
        year_tier, year_sentence = verdict_for(
            year_stats["count"], voice=voice
        )
        return {
            "stats": year_stats,
            "tier": year_tier,
            "sentence": year_sentence,
            "awards": compute_awards(year_stats, voice=voice),
        }

    target = scope(target_year)
    other = scope(other_year)
    if target_year < other_year:
        closing, change_pct = _year_change_closing(
            target_year, target["stats"], other_year, other["stats"]
        )
    else:
        closing, change_pct = _year_change_closing(
            other_year, other["stats"], target_year, target["stats"]
        )
    return {
        "target_year": target_year,
        "other_year": other_year,
        "target": target,
        "other": other,
        "change_pct": change_pct,
        "closing": closing,
    }


def render_year_comparison(vs_year: dict) -> str:
    target, other = vs_year["target"], vs_year["other"]
    out = [
        "YEAR COMPARISON",
        f"  {'':<22} {str(vs_year['target_year']):<30} {vs_year['other_year']}",
    ]
    for label, target_value, other_value in _comparison_rows(
        target["stats"], target["tier"], other["stats"], other["tier"]
    ):
        out.append(f"  {label:<22} {target_value:<30} {other_value}")
    out += [
        "",
        f"  {vs_year['closing']}",
        "",
    ]
    return "\n".join(out)


REPO_URL = "https://github.com/littlestjames82-sys/snap-judgment"


def build_wrapped_posts(stats: dict, tier: str, awards: list[dict],
                        year: int) -> str:
    """The post.txt for a Wrapped kit: a SHORT post (<=280 chars) and a
    STANDARD post, built only from this review's own numbers."""
    short = (
        f"My {year} in screenshots, judged by Snap Judgment: "
        f"{stats['count']:,} screenshots, verdict {tier}, "
        f"{stats['night_share']:.0f}% taken after 10 PM, "
        f"longest streak {stats['longest_streak']} days. "
        f"It reads metadata only, never the pictures. {REPO_URL}"
    )
    if len(short) > 280:  # very large numbers: fall back to a tighter draft
        short = (
            f"My {year} in screenshots, judged by Snap Judgment: "
            f"{stats['count']:,} screenshots, verdict {tier}. "
            f"It reads metadata only, never the pictures. {REPO_URL}"
        )
    if awards:
        awards_line = "Top awards: " + ", ".join(
            a["name"] for a in awards[:2]
        )
    else:
        awards_line = "Awards: none. The court is as surprised as you are."
    standard = "\n".join([
        f"My {year} in screenshots, judged by Snap Judgment.",
        "",
        f"Verdict: {tier}",
        f"{stats['count']:,} screenshots, {stats['total_human']} of evidence.",
        awards_line,
        "",
        "Snap Judgment reads file metadata only. "
        "It never looks at the pictures.",
        REPO_URL,
    ])
    return "\n".join([
        "SHORT POST",
        short,
        "",
        "STANDARD POST",
        standard,
        "",
    ])


def render_markdown(stats: dict, tier: str, sentence: str, awards: list[dict],
                    year: int | None = None, voice: str = "fair",
                    versus: dict | None = None,
                    folders: list[dict] | None = None,
                    vs_year: dict | None = None,
                    plea: dict | None = None,
                    rematch: dict | None = None,
                    rematch_requested: bool = False,
                    commentary: str | None = None,
                    trend: dict | None = None,
                    rehab: dict | None = None,
                    appeal: dict | None = None) -> str:
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
        out.append(_format_intro(intro, tier, stats["count"]))
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
    out.append("- **Screenshots per year:**")
    if stats["per_year"]:
        biggest_year = max(stats["per_year"].values())
        for y, n in stats["per_year"].items():
            bar = "█" * max(1, round(20 * n / biggest_year)) if n else ""
            out.append(f"  - {y}: {n} {bar}")
    out += [
        "",
        "## Screenshot Heatmap",
        "",
        "Rows are weekdays, columns are hours 0-23.",
        "",
        "```",
    ]
    heat_max = max((max(row) for row in stats["heatmap"]), default=0)
    shades = " ░▒▓█"
    for label, row in zip(
        ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), stats["heatmap"]
    ):
        cells = "".join(
            shades[0] if value == 0
            else shades[min(4, max(1, round(4 * value / heat_max)))]
            for value in row
        )
        out.append(f"{label} {cells}")
    out.append("```")
    if folders and len(folders) > 1:
        out += ["", "## Folders Judged", ""]
        for entry in folders:
            out.append(
                f"- **{entry['path']}:** {entry['count']:,} screenshots"
            )
    if stats["count"] >= 10 and stats["sources"]:
        out += ["", "## The Charges", ""]
        for source in stats["sources"]:
            out.append(
                f"- **{source['name']}:** {source['count']} ({source['share']:.1f}%)"
            )
    if stats["count"] >= 5 and stats["exhibits"]:
        out += ["", "## Exhibits", ""]
        for letter, exhibit in zip("ABCDE", stats["exhibits"]):
            out.append(
                f"- **Exhibit {letter}** - {exhibit['name']} "
                f"({_fmt_date(exhibit['date'])})"
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
    if commentary is not None:
        out += ["## Court Commentary", "", commentary, ""]
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
    if vs_year is not None:
        target, other = vs_year["target"], vs_year["other"]
        out += [
            "## Year Comparison",
            "",
            f"| | {vs_year['target_year']} | {vs_year['other_year']} |",
            "| --- | --- | --- |",
        ]
        for label, target_value, other_value in _comparison_rows(
            target["stats"], target["tier"], other["stats"], other["tier"]
        ):
            out.append(f"| {label} | {target_value} | {other_value} |")
        out += [
            "",
            vs_year["closing"],
            "",
        ]
    if plea is not None:
        out += [
            "",
            "## The Plea Bargain",
            "",
            f"If the defendant deleted {plea['old_removed']:,} screenshots "
            f"older than {plea['years']} "
            f"year{'s' if plea['years'] != 1 else ''} and "
            f"{plea['duplicate_removed']:,} duplicate extras - a purely "
            f"hypothetical exercise; this court deletes nothing - then "
            f"{plea['removed_count']:,} files would be removed, "
            f"{plea['removed_human']} ({plea['removed_bytes']:,} bytes) "
            f"reclaimed, and {plea['remaining_count']:,} screenshots "
            f"would remain, for a projected verdict of "
            f"**{plea['projected_tier']}**.",
            "",
            plea_closing(plea, tier),
            "",
        ]
    if rematch_requested:
        out += ["", "## Rematch", ""]
        if rematch is None:
            out.append("No prior hearing on record for these folders.")
        else:
            out.append(rematch_line(rematch, tier))
        out.append("")
    if rehab is not None:
        out += [""] + render_rehabilitation_markdown(rehab, tier)
    if appeal is not None:
        out += [""] + render_appeal_markdown(appeal)
    if trend is not None:
        out += [""] + render_trend_markdown(trend, "screenshots")
    return "\n".join(out)


def build_json(stats: dict, tier: str, sentence: str, awards: list[dict],
               folders: list[dict], year: int | None = None,
               judge: str = "fair",
               commentary: str | None = None) -> dict:
    data = {
        "version": __version__,
        "court": "screenshots",
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
        "busiest_window": {
            "count": stats["busiest_window"]["count"],
            "start": stats["busiest_window"]["start"].isoformat(),
        },
        "heatmap": stats["heatmap"],
        "date_filter": {
            "since": (
                stats["date_filter"]["since"].isoformat()
                if (stats.get("date_filter") or {}).get("since") else None
            ),
            "until": (
                stats["date_filter"]["until"].isoformat()
                if (stats.get("date_filter") or {}).get("until") else None
            ),
        },
        "duplicate_groups": stats["duplicate_groups"],
        "duplicate_files": stats["duplicate_files"],
        "duplicate_bytes_wasted": stats["duplicate_bytes_wasted"],
        "exhibits": [
            {"name": e["name"], "date": e["date"].isoformat()}
            for e in stats["exhibits"]
        ],
        "formats": stats["formats"],
        "season": stats["season"],
        "silent_months": stats["silent_months"],
        "median_per_active_day": stats["median_per_active_day"],
        "anniversary": stats["anniversary"],
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
    if commentary is not None:
        data["commentary"] = commentary
    return data


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
  .install {{ text-align: center; font-size: 10px; color: #6d6878; margin-top: 4px;
             font-family: 'Courier New', monospace; }}
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
    <div class="install">pip install snap-judgment</div>
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
  <text x="600" y="574" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="16" fill="#6d6878">Judged from file metadata only. No pixels were read.</text>
  <text x="600" y="598" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="13" letter-spacing="1" fill="#6d6878">pip install snap-judgment</text>
</svg>
"""


BADGE_TIER_COLORS = {
    "Innocent Bystander": "#4c1",
    "Petty Offender": "#97ca00",
    "Repeat Offender": "#dfb317",
    "Habitual Hoarder": "#fe7d37",
    "Felony-Level Archivist": "#e05d44",
    "Unrepentant Screenshot Felon": "#b60205",
    "No Evidence": "#9f9f9f",
}


DL_BADGE_TIER_COLORS = {
    "Tidy Desk": "#4c1",
    "Mild Clutter": "#97ca00",
    "Digital Junk Drawer": "#dfb317",
    "Landfill Adjacent": "#fe7d37",
    "Hoarding Situation": "#e05d44",
    "Superfund Site": "#b60205",
    "No Evidence": "#9f9f9f",
}


def render_badge(tier: str) -> str:
    """A shields-style verdict badge: 'snap judgment' on the left, the
    tier on the right, colored by severity."""
    return _badge_svg(tier, BADGE_TIER_COLORS)


def render_downloads_badge(tier: str) -> str:
    """The downloads docket's badge: the same shields style, colored
    by the docket's own severity ladder."""
    return _badge_svg(tier, DL_BADGE_TIER_COLORS)


def _badge_svg(tier: str, colors: dict) -> str:
    esc = _xml_escape
    left_text = "snap judgment"
    left_w = int(len(left_text) * 6.5) + 12
    right_w = int(len(tier) * 6.5) + 12
    color = colors.get(tier, "#9f9f9f")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{left_w + right_w}" height="20" role="img" aria-label="snap judgment: {esc(tier)}">
  <rect width="{left_w}" height="20" fill="#555555"/>
  <rect x="{left_w}" width="{right_w}" height="20" fill="{color}"/>
  <text x="{left_w / 2:.1f}" y="14" text-anchor="middle" font-family="Verdana, Geneva, sans-serif" font-size="11" fill="#ffffff">{esc(left_text)}</text>
  <text x="{left_w + right_w / 2:.1f}" y="14" text-anchor="middle" font-family="Verdana, Geneva, sans-serif" font-size="11" fill="#ffffff">{esc(tier)}</text>
</svg>
"""


def render_html_report(stats: dict, tier: str, sentence: str,
                       awards: list[dict], year: int | None = None,
                       voice: str = "fair",
                       commentary: str | None = None) -> str:
    """A full self-contained HTML report: verdict, evidence table,
    per-year bars, exhibits, charges, awards, sentence. Inline CSS only."""
    esc = html.escape
    rows = "\n".join(
        f"      <tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>"
        for label, value in _evidence_lines(stats)
    )
    era_rows = "\n".join(
        f"      <tr><td>Device era</td><td>{era['dims'][0]}x{era['dims'][1]}"
        f" - {era['count']} shots, {_fmt_date(era['first'])}"
        f" to {_fmt_date(era['last'])}</td></tr>"
        for era in stats["device_eras"]
    )
    if era_rows:
        rows += "\n" + era_rows
    biggest_year = max(stats["per_year"].values()) if stats["per_year"] else 1
    bars = "\n".join(
        f'      <div class="bar-row"><span class="bar-year">{y}</span>'
        f'<div class="bar-track"><div class="bar" '
        f'style="width:{100.0 * n / biggest_year:.1f}%"></div></div>'
        f'<span class="bar-count">{n}</span></div>'
        for y, n in stats["per_year"].items()
    )
    sections = []
    intro = JUDGE_INTROS.get(voice)
    if intro:
        sections.append(
            f'    <p class="intro">{esc(_format_intro(intro, tier, stats["count"]))}</p>'
        )
    if year is not None:
        sections.append(f'    <p class="intro">Scope: year {year}</p>')
    sections.append(f"""    <h2>The Evidence</h2>
    <table>
{rows}
    </table>
    <div class="bars">
{bars}
    </div>""")
    heat_max = max((max(row) for row in stats["heatmap"]), default=0)
    heat_header = "".join(f"<th>{h}</th>" for h in range(24))
    heat_rows = []
    for label, row in zip(
        ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), stats["heatmap"]
    ):
        cells = []
        for hour, value in enumerate(row):
            opacity = (0.85 * value / heat_max) if value and heat_max else 0.0
            cells.append(
                f'<td style="background:rgba(176,141,60,{opacity:.2f})" '
                f'title="{label} {hour:02d}:00 - {value}">'
                f"{value if value else ''}</td>"
            )
        heat_rows.append(
            f"      <tr><th>{label}</th>{''.join(cells)}</tr>"
        )
    heat_body = "\n".join(heat_rows)
    sections.append(f"""    <h2>Screenshot Heatmap</h2>
    <table class="heat">
      <tr><th></th>{heat_header}</tr>
{heat_body}
    </table>""")
    if stats["count"] >= 5 and stats["exhibits"]:
        items = "\n".join(
            f"        <li><strong>Exhibit {letter}</strong> - "
            f"{esc(e['name'])} ({_fmt_date(e['date'])})</li>"
            for letter, e in zip("ABCDE", stats["exhibits"])
        )
        sections.append(f"""    <h2>Exhibits</h2>
    <ul>
{items}
    </ul>""")
    if stats["count"] >= 10 and stats["sources"]:
        items = "\n".join(
            f"        <li><strong>{esc(s['name'])}</strong> - {s['count']} "
            f"({s['share']:.1f}%)</li>"
            for s in stats["sources"]
        )
        sections.append(f"""    <h2>The Charges</h2>
    <ul>
{items}
    </ul>""")
    if awards:
        items = "\n".join(
            f"        <li><strong>{esc(a['name'])}</strong> - "
            f"{esc(a['evidence'])}</li>"
            for a in awards
        )
    else:
        items = "        <li>None. The court is as surprised as you are.</li>"
    sections.append(f"""    <h2>Awards</h2>
    <ul>
{items}
    </ul>""")
    body = "\n".join(sections)
    commentary_html = ""
    if commentary is not None:
        commentary_html = (
            f'\n    <h2>Court Commentary</h2>'
            f'\n    <p>{esc(commentary)}</p>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Snap Judgment - {esc(tier)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0d0d12; color: #e8e3d8; font-family: Georgia, 'Times New Roman', serif;
         display: flex; justify-content: center; padding: 40px 16px; }}
  .report {{ max-width: 720px; width: 100%; background: #16161f; border: 2px solid #b08d3c;
            border-radius: 10px; padding: 36px 40px; box-shadow: 0 12px 40px rgba(0,0,0,.6); }}
  .court {{ text-align: center; letter-spacing: 3px; font-size: 12px; color: #b08d3c;
           text-transform: uppercase; }}
  h1 {{ text-align: center; font-size: 30px; margin: 10px 0 4px; }}
  .tier {{ text-align: center; font-size: 24px; color: #d4af5a; margin-bottom: 6px; }}
  .summary {{ text-align: center; color: #cfc9ba; margin-bottom: 10px; }}
  .intro {{ text-align: center; font-style: italic; color: #9a94a6; margin-bottom: 8px; }}
  h2 {{ font-size: 13px; letter-spacing: 2px; color: #b08d3c; text-transform: uppercase;
       margin: 22px 0 10px; border-top: 1px solid #33333f; padding-top: 16px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 5px 4px; font-size: 14px; vertical-align: top; }}
  td:first-child {{ color: #9a94a6; width: 42%; }}
  .bars {{ margin-top: 12px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 4px 0; font-size: 13px; }}
  .bar-year {{ width: 42px; color: #9a94a6; }}
  .bar-track {{ flex: 1; background: #1e1e2a; border-radius: 4px; height: 12px; }}
  .bar {{ background: #b08d3c; height: 12px; border-radius: 4px; }}
  .bar-count {{ width: 48px; text-align: right; }}
  .heat {{ border-collapse: separate; border-spacing: 2px; width: auto; }}
  .heat th {{ font-size: 9px; color: #9a94a6; padding: 1px; font-weight: normal; }}
  .heat td {{ width: 20px; height: 16px; text-align: center; font-size: 9px;
             padding: 1px 0; color: #ffffff; border-radius: 2px; }}
  ul {{ list-style: none; }}
  li {{ padding: 4px 0; font-size: 14px; }}
  .sentence {{ font-style: italic; color: #cfc9ba; border-top: 1px solid #33333f;
              padding-top: 14px; margin-top: 22px; }}
  .foot {{ text-align: center; font-size: 11px; color: #6d6878; margin-top: 16px; }}
</style>
</head>
<body>
  <div class="report">
    <div class="court">The Court of Screenshot Affairs</div>
    <h1>Snap Judgment</h1>
    <div class="tier">{esc(tier)}</div>
    <div class="summary">{stats['count']:,} screenshots, {esc(stats['total_human'])} of evidence,
      spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.</div>
{body}
    <div class="sentence">{esc(sentence)}</div>{commentary_html}
    <div class="foot">Judged from file metadata only. Pixels were never read. Snap Judgment, Ghost Developer Studio.</div>
  </div>
</body>
</html>
"""


def oneline_summary(stats: dict, tier: str) -> str:
    """Exactly one line: verdict, count, size, span."""
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}"
    return (
        f"{tier} · {stats['count']:,} screenshots · "
        f"{stats['total_human']} · {span}"
    )


# ---------------------------------------------------------------------------
# PNG share card: a minimal bitmap-font renderer, standard library only
# ---------------------------------------------------------------------------

# A compact 5x7 pixel font. Each glyph is 7 rows of 5 characters;
# "#" is a lit pixel, "." is dark. Covers A-Z, a-z, 0-9 and basic
# punctuation. Anything not in the table renders as a space.
_FONT_5X7 = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."),
    "X": ("#...#", ".#.#.", "..#..", "..#..", "..#..", ".#.#.", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "a": (".....", ".....", ".####", "....#", ".####", "#...#", ".####"),
    "b": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "####."),
    "c": (".....", ".....", ".####", "#....", "#....", "#....", ".####"),
    "d": ("....#", "....#", ".####", "#...#", "#...#", "#...#", ".####"),
    "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
    "f": ("..##.", ".#...", "#####", ".#...", ".#...", ".#...", ".#..."),
    "g": (".....", ".....", ".####", "#...#", ".####", "....#", ".###."),
    "h": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "i": ("..#..", ".....", ".##..", "..#..", "..#..", "..#..", ".###."),
    "j": ("...#.", ".....", "..##.", "...#.", "...#.", "#..#.", ".##.."),
    "k": ("#....", "#....", "#..#.", "#.#..", "##...", "#.#..", "#..#."),
    "l": (".##..", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "m": (".....", ".....", "##.#.", "#.#.#", "#.#.#", "#.#.#", "#.#.#"),
    "n": (".....", ".....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "o": (".....", ".....", ".###.", "#...#", "#...#", "#...#", ".###."),
    "p": (".....", ".....", "####.", "#...#", "####.", "#....", "#...."),
    "q": (".....", ".....", ".####", "#...#", ".####", "....#", "....#"),
    "r": (".....", ".....", "#.##.", "##..#", "#....", "#....", "#...."),
    "s": (".....", ".....", ".####", "#....", ".###.", "....#", "####."),
    "t": (".#...", ".#...", "#####", ".#...", ".#...", ".#..#", "..##."),
    "u": (".....", ".....", "#...#", "#...#", "#...#", "#..##", ".##.#"),
    "v": (".....", ".....", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "w": (".....", ".....", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."),
    "x": (".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
    "y": (".....", ".....", "#...#", "#...#", ".####", "....#", ".###."),
    "z": (".....", ".....", "#####", "...#.", "..#..", ".#...", "#####"),
    "0": (".###.", "#..##", "#.#.#", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "2": (".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".....", "..#.."),
    ",": (".....", ".....", ".....", ".....", ".....", "..#..", ".#..."),
    ":": (".....", "..#..", ".....", ".....", ".....", "..#..", "....."),
    ";": (".....", "..#..", ".....", ".....", ".....", "..#..", ".#..."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "?": (".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."),
    "'": ("..#..", "..#..", ".#...", ".....", ".....", ".....", "....."),
    "%": ("##..#", "##.#.", "....#", "...#.", "..#..", ".#...", "#..##"),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "....#", "....#", "....#", "..#..", ".#..."),
    "&": (".###.", "#...#", "#.#..", ".#...", "#.#.#", "#..#.", ".##.#"),
}

_FONT_BLANK = _FONT_5X7[" "]


def _glyph(ch: str):
    glyph = _FONT_5X7.get(ch)
    if glyph is None:
        glyph = _FONT_5X7.get(ch.upper(), _FONT_BLANK)
    return glyph


def _text_width(text: str, scale: int) -> int:
    return len(text) * 6 * scale - scale if text else 0


def _fit_scale(text: str, max_width: int, start: int) -> int:
    scale = start
    while scale > 1 and _text_width(text, scale) > max_width:
        scale -= 1
    return scale


class _Canvas:
    """A tiny RGB canvas with rectangle and bitmap-text drawing."""

    def __init__(self, width: int, height: int, color):
        self.width = width
        self.height = height
        self.pixels = bytearray(bytes(color) * (width * height))

    def rect(self, x0, y0, x1, y1, color):
        r, g, b = color
        for y in range(max(0, y0), min(self.height, y1)):
            row = y * self.width * 3
            for x in range(max(0, x0), min(self.width, x1)):
                i = row + x * 3
                self.pixels[i] = r
                self.pixels[i + 1] = g
                self.pixels[i + 2] = b

    def border(self, x0, y0, x1, y1, color, thickness):
        self.rect(x0, y0, x1, y0 + thickness, color)
        self.rect(x0, y1 - thickness, x1, y1, color)
        self.rect(x0, y0, x0 + thickness, y1, color)
        self.rect(x1 - thickness, y0, x1, y1, color)

    def text(self, center_x, y, text, scale, color):
        """Draw text centered on center_x, with its top at y."""
        x = center_x - _text_width(text, scale) // 2
        for ch in text:
            glyph = _glyph(ch)
            for row_i, row in enumerate(glyph):
                for col_i, mark in enumerate(row):
                    if mark == "#":
                        self.rect(
                            x + col_i * scale, y + row_i * scale,
                            x + (col_i + 1) * scale,
                            y + (row_i + 1) * scale, color,
                        )
            x += 6 * scale

    def to_png(self) -> bytes:
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)  # filter type 0 (none) for every row
            raw += self.pixels[y * stride:(y + 1) * stride]

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(
            ">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0
        )
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def render_png(stats: dict, tier: str, sentence: str,
               awards: list[dict]) -> bytes:
    """The 1200x630 share card as a real PNG, drawn with the bitmap font."""
    canvas = _Canvas(1200, 630, (16, 16, 20))
    canvas.rect(24, 24, 1176, 606, (22, 22, 31))
    canvas.border(24, 24, 1176, 606, (176, 141, 60), 3)
    accent = (176, 141, 60)
    gold = (212, 175, 90)
    paper = (232, 227, 216)
    muted = (154, 148, 166)
    faint = (109, 104, 120)
    canvas.text(600, 62, "THE COURT OF SCREENSHOT AFFAIRS", 2, accent)
    canvas.text(600, 104, "SNAP JUDGMENT", 7, paper)
    canvas.text(600, 186, tier, _fit_scale(tier, 1080, 6), gold)
    canvas.rect(200, 252, 1000, 254, (51, 51, 63))
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}"
    stats_line = (
        f"{stats['count']:,} screenshots - {stats['total_human']} - {span}"
    )
    canvas.text(600, 282, stats_line, _fit_scale(stats_line, 1080, 3), paper)
    canvas.text(600, 348, f"Night share {stats['night_share']:.1f}%",
                3, muted)
    if awards:
        awards_line = "Awards: " + ", ".join(a["name"] for a in awards[:3])
    else:
        awards_line = "Awards: none"
    canvas.text(600, 402, awards_line, _fit_scale(awards_line, 1080, 2),
                paper)
    if sentence:
        canvas.text(600, 470, sentence, _fit_scale(sentence, 1080, 2),
                    (207, 201, 186))
    canvas.text(600, 566,
                "Judged from file metadata only. No pixels were read.",
                2, faint)
    canvas.text(600, 590, "pip install snap-judgment", 1, faint)
    return canvas.to_png()


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


def build_rematch(records: list[dict], folder_names: list[str],
                  current_count: int, current_tier: str) -> dict | None:
    """Compare this hearing against the most recent court record whose
    folder set matches exactly. None when there is no prior hearing."""
    match = None
    for record in records:
        rec_folders = record.get("folders") or []
        if sorted(rec_folders) == sorted(folder_names):
            match = record
    if match is None:
        return None
    try:
        prev_count = int(match["count"])
        prev_tier = str(match["verdict"])
        prev_date = datetime.fromisoformat(match["timestamp"]).date()
    except Exception:
        return None
    prev_rank = TIER_RANKS.get(_normalize_tier_name(prev_tier), -1)
    cur_rank = TIER_RANKS.get(_normalize_tier_name(current_tier), -1)
    if cur_rank > prev_rank:
        change = "worsened"
    elif cur_rank < prev_rank:
        change = "improved"
    else:
        change = "unchanged"
    return {
        "previous_count": prev_count,
        "delta": current_count - prev_count,
        "previous_tier": prev_tier,
        "date": prev_date,
        "change": change,
    }


def rematch_line(rematch: dict, current_tier: str) -> str:
    delta = rematch["delta"]
    delta_text = f"+{delta}" if delta > 0 else f"{delta}"
    base = (
        f"Since your last hearing ({_fmt_date(rematch['date'])}): "
        f"{delta_text} screenshots. "
    )
    if rematch["change"] == "worsened":
        return base + (
            f"The verdict has worsened from {rematch['previous_tier']} "
            f"to {current_tier}."
        )
    if rematch["change"] == "improved":
        return base + (
            f"The verdict has improved from {rematch['previous_tier']} "
            f"to {current_tier}."
        )
    return base + f"The verdict is unchanged: {current_tier}, again."


def render_rematch_section(rematch: dict | None, current_tier: str) -> str:
    out = ["REMATCH"]
    if rematch is None:
        out.append("  No prior hearing on record for these folders.")
    else:
        out.append(f"  {rematch_line(rematch, current_tier)}")
    out.append("")
    return "\n".join(out)


def build_downloads_rematch(records: list[dict], folder_names: list[str],
                            current_count: int, current_bytes: int,
                            current_tier: str) -> dict | None:
    """Compare this downloads hearing against the most recent
    DOWNLOADS hearing in the court record whose folder set matches
    exactly. None when there is no prior downloads hearing."""
    match = None
    for record in records:
        if record.get("court") != "downloads":
            continue
        rec_folders = record.get("folders") or []
        if sorted(rec_folders) == sorted(folder_names):
            match = record
    if match is None:
        return None
    try:
        prev_count = int(match["count"])
        prev_bytes = int(match.get("total_bytes") or 0)
        prev_tier = str(match["verdict"])
        prev_date = datetime.fromisoformat(match["timestamp"]).date()
    except Exception:
        return None
    prev_rank = DL_TIER_RANKS.get(_normalize_tier_name(prev_tier), -1)
    cur_rank = DL_TIER_RANKS.get(_normalize_tier_name(current_tier), -1)
    if cur_rank > prev_rank:
        change = "worsened"
    elif cur_rank < prev_rank:
        change = "improved"
    else:
        change = "unchanged"
    return {
        "previous_count": prev_count,
        "delta": current_count - prev_count,
        "previous_bytes": prev_bytes,
        "bytes_delta": current_bytes - prev_bytes,
        "previous_tier": prev_tier,
        "date": prev_date,
        "change": change,
    }


def downloads_rematch_line(rematch: dict, current_tier: str) -> str:
    delta = rematch["delta"]
    delta_text = f"+{delta}" if delta > 0 else f"{delta}"
    bytes_delta = rematch["bytes_delta"]
    if bytes_delta > 0:
        size_text = f"+{human_bytes(bytes_delta)}"
    elif bytes_delta < 0:
        size_text = f"-{human_bytes(-bytes_delta)}"
    else:
        size_text = "0 B"
    base = (
        f"Since your last hearing ({_fmt_date(rematch['date'])}): "
        f"{delta_text} files ({size_text}). "
    )
    if rematch["change"] == "worsened":
        return base + (
            f"The verdict has worsened from {rematch['previous_tier']} "
            f"to {current_tier}."
        )
    if rematch["change"] == "improved":
        return base + (
            f"The verdict has improved from {rematch['previous_tier']} "
            f"to {current_tier}."
        )
    return base + f"The verdict is unchanged: {current_tier}, again."


def render_downloads_rematch_section(rematch: dict | None,
                                     current_tier: str) -> str:
    out = ["REMATCH"]
    if rematch is None:
        out.append("  No prior hearing on record for these folders.")
    else:
        out.append(f"  {downloads_rematch_line(rematch, current_tier)}")
    out.append("")
    return "\n".join(out)


def render_history(records: list[dict]) -> str:
    out = ["COURT RECORD - prior hearings", ""]
    for record in records:
        try:
            when = datetime.fromisoformat(record["timestamp"])
            folder = (record.get("folders") or ["?"])[0]
            unit = (
                "files" if record.get("court") == "downloads"
                else "screenshots"
            )
            out.append(
                f"  {_fmt_date(when.date())}  {folder}  "
                f"{record['count']} {unit}  {record['verdict']}"
            )
        except Exception:
            continue
    return "\n".join(out)


# ---------------------------------------------------------------------------
# the trend: the court record for one folder set, visualized
# ---------------------------------------------------------------------------

SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(counts: list[int]) -> str:
    """A sparkline of hearing counts, scaled min to max across the
    eight block heights. A flat series (a single hearing included)
    draws at full height."""
    if not counts:
        return ""
    low, high = min(counts), max(counts)
    if high == low:
        return SPARK_BLOCKS[-1] * len(counts)
    span = high - low
    return "".join(
        SPARK_BLOCKS[round((count - low) / span * 7)] for count in counts
    )


def build_trend(records: list[dict], folder_names: list[str], court: str,
                today_record: dict | None = None) -> dict | None:
    """The trend of the court record for one folder set in one court:
    every recorded hearing whose folder set matches exactly and whose
    court matches, oldest first. Records without a court field belong
    to the screenshots court. `today_record`, when given, is this run's
    own hearing: it is appended to the selection when the record on
    disk does not already end with it (the caller records the hearing
    first; this keeps the trend honest even if that write failed).
    None when no hearing is on record at all."""
    selected = []
    for record in records:
        if (record.get("court") or "screenshots") != court:
            continue
        rec_folders = record.get("folders") or []
        if sorted(rec_folders) == sorted(folder_names):
            selected.append(record)
    if today_record is not None:
        stamp = today_record.get("timestamp")
        if not selected or selected[-1].get("timestamp") != stamp:
            selected.append(today_record)
    hearings = []
    for record in selected:
        try:
            hearings.append({
                "count": int(record["count"]),
                "tier": str(record["verdict"]),
                "date": datetime.fromisoformat(
                    record["timestamp"]
                ).date(),
            })
        except Exception:
            continue
    if not hearings:
        return None
    counts = [hearing["count"] for hearing in hearings]
    first_count, latest_count = counts[0], counts[-1]
    delta = latest_count - first_count
    delta_pct = (
        round(100.0 * delta / first_count, 1) if first_count else 0.0
    )
    return {
        "hearings": len(hearings),
        "counts": counts,
        "tiers": [hearing["tier"] for hearing in hearings],
        "first_count": first_count,
        "latest_count": latest_count,
        "delta": delta,
        "delta_pct": delta_pct,
        "first_tier": hearings[0]["tier"],
        "latest_tier": hearings[-1]["tier"],
        "first_date": hearings[0]["date"],
        "latest_date": hearings[-1]["date"],
        "sparkline": sparkline(counts),
    }


def trend_json(trend: dict) -> dict:
    """The trend as it appears under the "trend" key in --json."""
    return {
        "hearings": trend["hearings"],
        "counts": trend["counts"],
        "tiers": trend["tiers"],
        "first_count": trend["first_count"],
        "latest_count": trend["latest_count"],
        "delta": trend["delta"],
        "delta_pct": trend["delta_pct"],
    }


def _trend_facts(trend: dict, unit: str) -> list[str]:
    """The trend's facts, one per line, shared by the terminal
    section and the Markdown section. `unit` is "screenshots" or
    "files"."""
    if trend["hearings"] <= 1:
        return [
            "This is the first hearing on record.",
            f"Count: {trend['latest_count']:,} {unit}, "
            f"verdict {trend['latest_tier']}.",
        ]
    delta = trend["delta"]
    delta_text = f"{delta:+,}" if delta else "0"
    pct_text = ""
    if trend["first_count"]:
        pct_text = f", {trend['delta_pct']:+.1f}%"
    return [
        f"Hearings on record: {trend['hearings']} "
        f"({_fmt_date(trend['first_date'])} - "
        f"{_fmt_date(trend['latest_date'])})",
        f"Count: {trend['first_count']:,} -> "
        f"{trend['latest_count']:,} {unit} ({delta_text}{pct_text})",
        f"Verdict: {trend['first_tier']} -> {trend['latest_tier']}",
    ]


def render_trend_section(trend: dict, unit: str,
                         folder_names: list[str] | None = None,
                         commentary: str | None = None) -> str:
    """The terminal TREND section printed by a --trend run."""
    out = ["TREND"]
    if folder_names:
        label = "Folder" if len(folder_names) == 1 else "Folders"
        out.append(f"  {label}: {', '.join(folder_names)}")
    for line in _trend_facts(trend, unit):
        out.append(f"  {line}")
    out.append(f"  {trend['sparkline']}")
    if trend["hearings"] <= 1:
        out.append("  (one hearing on record)")
    if commentary is not None:
        out.append(f"  COMMENTARY: {commentary}")
    out.append("")
    return "\n".join(out)


def render_trend_markdown(trend: dict, unit: str) -> list[str]:
    """The '## Trend' section lines for a Markdown report."""
    out = ["## Trend", ""]
    out += _trend_facts(trend, unit)
    out += ["", "```", trend["sparkline"], "```"]
    if trend["hearings"] <= 1:
        out.append("(one hearing on record)")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# the downloads docket tries a co-defendant
# ---------------------------------------------------------------------------

def downloads_worse_offender(main_stats: dict, main_tier: str,
                             vs_stats: dict, vs_tier: str) -> str:
    """Who is guiltier in the docket: 'main', 'vs', or 'tie'.
    Tier severity first, then count, then total size."""
    main_rank = DL_TIER_RANKS.get(_normalize_tier_name(main_tier), -1)
    vs_rank = DL_TIER_RANKS.get(_normalize_tier_name(vs_tier), -1)
    if main_rank != vs_rank:
        return "main" if main_rank > vs_rank else "vs"
    if main_stats["count"] != vs_stats["count"]:
        return "main" if main_stats["count"] > vs_stats["count"] else "vs"
    if main_stats["total_bytes"] != vs_stats["total_bytes"]:
        return (
            "main" if main_stats["total_bytes"] > vs_stats["total_bytes"]
            else "vs"
        )
    return "tie"


def _downloads_comparison_closing(main_stats: dict, main_tier: str,
                                  vs_stats: dict, vs_tier: str,
                                  worse: str) -> str:
    """The closing line of a downloads co-defendant comparison; it
    states the ground the decision rests on."""
    if worse == "tie":
        return (
            "Dead heat: same verdict, same count, same size. "
            "The court cannot separate them."
        )
    if worse == "main":
        who, winner, loser = "the defendant", main_stats, vs_stats
        winner_tier, loser_tier = main_tier, vs_tier
    else:
        who, winner, loser = "the co-defendant", vs_stats, main_stats
        winner_tier, loser_tier = vs_tier, main_tier
    if winner_tier != loser_tier:
        return (
            f"Bigger offender: {who}, {winner_tier} outranks "
            f"{loser_tier} on the docket's ladder."
        )
    if winner["count"] != loser["count"]:
        return (
            f"Bigger offender: {who}, on count with the verdicts "
            f"level at {winner_tier}: {winner['count']:,} files "
            f"to {loser['count']:,}."
        )
    return (
        f"Bigger offender: {who}, on total size with verdict and "
        f"count level: {winner['total_human']} to {loser['total_human']}."
    )


def _downloads_delta_line(main_stats: dict, vs_stats: dict) -> str:
    """The signed count and size deltas, defendant less co-defendant."""
    dcount = main_stats["count"] - vs_stats["count"]
    dbytes = main_stats["total_bytes"] - vs_stats["total_bytes"]
    count_text = f"{dcount:+,}" if dcount else "0"
    if dbytes > 0:
        size_text = f"+{human_bytes(dbytes)}"
    elif dbytes < 0:
        size_text = f"-{human_bytes(-dbytes)}"
    else:
        size_text = "0 B"
    return (
        f"Delta (defendant less co-defendant): {count_text} files, "
        f"{size_text}."
    )


def _downloads_comparison_rows(main_stats: dict, main_tier: str,
                               vs_stats: dict, vs_tier: str,
                               main_awards: list, vs_awards: list,
                               ) -> list[tuple[str, str, str]]:
    return [
        ("Verdict", main_tier, vs_tier),
        ("Files", f"{main_stats['count']:,}", f"{vs_stats['count']:,}"),
        ("Total size", main_stats["total_human"],
         vs_stats["total_human"]),
        ("Awards", f"{len(main_awards)}", f"{len(vs_awards)}"),
        ("Oldest file", _fmt_date(main_stats["oldest_date"]),
         _fmt_date(vs_stats["oldest_date"])),
    ]


def render_downloads_comparison(main_stats: dict, main_tier: str,
                                main_awards: list, versus: dict) -> str:
    vs_stats, vs_tier = versus["stats"], versus["tier"]
    out = [
        "CO-DEFENDANT COMPARISON",
        f"  {'':<22} {'Defendant':<30} Co-defendant",
    ]
    for label, main_value, vs_value in _downloads_comparison_rows(
        main_stats, main_tier, vs_stats, vs_tier,
        main_awards, versus["awards"],
    ):
        out.append(f"  {label:<22} {main_value:<30} {vs_value}")
    out += [
        "",
        f"  {_downloads_delta_line(main_stats, vs_stats)}",
        f"  {_downloads_comparison_closing(main_stats, main_tier, vs_stats, vs_tier, versus['worse'])}",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# the docket's rehabilitation plan and appeals court: the two newest
# proceedings, heard in the downloads court on its own evidence
# ---------------------------------------------------------------------------

DL_APPEAL_COMMUTED_SENTENCE = (
    "Sentence on appeal: commuted to time served. The downloads "
    "folder is released on its own recognizance."
)


def compute_downloads_rehabilitation(files: list, stats: dict,
                                     voice: str = "fair") -> dict:
    """The docket's staged cleanup plan, the downloads court's own
    hearing of --rehab. The files are partitioned into three steps,
    in order, and every file is claimed by at most one step:

    1. duplicate extras: every duplicate-download extra copy, the
       provable waste the cleanup order already lists (every group
       member except the keeper);
    2. ancient installers: installers older than REHAB_YEARS years,
       measured from the newest file in the folder, that step 1 did
       not already claim - an installer's work is done the day it
       runs, so an old one is dead weight in a way an old document
       is not;
    3. heavy evidence: the REHAB_HEAVY_COUNT largest files still
       standing after the first two steps, assigned only when more
       than that many would remain.

    Each step reports its own files and bytes and the cumulative
    position after serving it: how much evidence remains and what
    verdict the remainder would earn on the docket's own ladder.
    Purely a plan; this court deletes, moves, and modifies nothing,
    ever."""
    total_bytes = sum(f.size for f in files)
    newest_date = max(f.dt.date() for f in files)
    cutoff = _minus_years(newest_date, REHAB_YEARS)

    duplicate_paths = set(stats["duplicate_extra_paths"])
    duplicate_ids = {
        id(f) for f in files if f.path in duplicate_paths
    }

    ancient_ids = {
        id(f) for f in files
        if id(f) not in duplicate_ids
        and f.ext in DL_INSTALLER_EXTS
        and f.dt.date() < cutoff
    }

    claimed = duplicate_ids | ancient_ids
    pool = [f for f in files if id(f) not in claimed]
    heavy_ids = set()
    if len(pool) > REHAB_HEAVY_COUNT:
        heavy = sorted(
            pool, key=lambda f: (-f.size, f.mtime, f.name)
        )[:REHAB_HEAVY_COUNT]
        heavy_ids = {id(f) for f in heavy}

    step_specs = [
        ("duplicate_extras", "Clear the duplicate extras",
         "Every duplicate download beyond the keeper of each "
         "duplicate chain.",
         duplicate_ids),
        ("ancient_installers", "Retire the ancient installers",
         f"Installers older than {REHAB_YEARS} years, measured "
         f"from the newest file, that step 1 did not claim.",
         ancient_ids),
        ("heavy_evidence", "Move out the heavy evidence",
         f"The {REHAB_HEAVY_COUNT} largest files still standing "
         f"after the first two steps.",
         heavy_ids),
    ]
    steps = []
    cumulative_count = 0
    cumulative_bytes = 0
    for key, name, detail, ids in step_specs:
        members = [f for f in files if id(f) in ids]
        step_bytes = sum(f.size for f in members)
        cumulative_count += len(members)
        cumulative_bytes += step_bytes
        remaining = len(files) - cumulative_count
        projected_tier, _sentence = downloads_verdict_for(
            remaining, voice=voice
        )
        steps.append({
            "key": key,
            "name": name,
            "detail": detail,
            "count": len(members),
            "bytes": step_bytes,
            "human": human_bytes(step_bytes),
            "cumulative_count": cumulative_count,
            "cumulative_bytes": cumulative_bytes,
            "remaining_count": remaining,
            "projected_tier": projected_tier,
        })
    removed_count = cumulative_count
    removed_bytes = cumulative_bytes
    remaining_count = len(files) - removed_count
    projected_tier, _sentence = downloads_verdict_for(
        remaining_count, voice=voice
    )
    review_minutes = (
        -(-removed_count * REHAB_REVIEW_SECONDS // 60) if removed_count
        else 0
    )
    sessions = (
        -(-removed_count // REHAB_SESSION_SIZE) if removed_count else 0
    )
    return {
        "years": REHAB_YEARS,
        "cutoff": cutoff,
        "steps": steps,
        "removed_count": removed_count,
        "removed_bytes": removed_bytes,
        "removed_human": human_bytes(removed_bytes),
        "reclaim_share_pct": (
            round(100.0 * removed_bytes / total_bytes, 1)
            if total_bytes else 0.0
        ),
        "remaining_count": remaining_count,
        "projected_tier": projected_tier,
        "review_seconds_per_file": REHAB_REVIEW_SECONDS,
        "review_minutes": review_minutes,
        "session_size": REHAB_SESSION_SIZE,
        "sessions": sessions,
    }


def downloads_rehab_closing(rehab: dict, current_tier: str) -> str:
    """The docket's outlook on the plan, in the plea closing's
    style."""
    if rehab["removed_count"] == 0:
        return (
            "No rehabilitation required. The desk is already as "
            "clean as this court can ask."
        )
    if dl_tier_rank(rehab["projected_tier"]) < dl_tier_rank(
        current_tier
    ):
        return (
            "The court finds the defendant rehabilitable: serve "
            "the plan and the verdict improves."
        )
    return (
        "The plan reclaims space but not standing: the verdict "
        "would hold. Rehabilitation is its own reward."
    )


def render_downloads_rehabilitation(rehab: dict,
                                    current_tier: str) -> str:
    """The terminal REHABILITATION PLAN section the docket prints
    for --rehab."""
    out = [
        "THE REHABILITATION PLAN",
        "  The docket assigns the following plan, in order. Each "
        "step claims",
        "  only files no earlier step claimed, and nothing is "
        "deleted, moved,",
        "  or modified by this court; the plan is yours to serve.",
    ]
    active = [step for step in rehab["steps"] if step["count"]]
    if not active:
        out.append("  No steps assigned: there is nothing to clear.")
    for index, step in enumerate(active, start=1):
        out += [
            f"  Step {index} - {step['name']}: {step['count']:,} "
            f"files, {step['human']} ({step['bytes']:,} bytes)",
            f"    {step['detail']}",
            f"    After this step: {step['remaining_count']:,} "
            f"files remain (projected verdict: "
            f"{step['projected_tier']})",
        ]
    out += [
        f"  {'Files assigned':<30} {rehab['removed_count']:,}",
        f"  {'Space reclaimed':<30} {rehab['removed_human']} "
        f"({rehab['removed_bytes']:,} bytes, "
        f"{rehab['reclaim_share_pct']:.1f}% of the evidence)",
        f"  {'Remaining evidence':<30} "
        f"{rehab['remaining_count']:,} files",
        f"  {'Projected verdict':<30} {rehab['projected_tier']}",
        f"  {'Estimated review time':<30} {rehab['review_minutes']:,} "
        f"minute{'s' if rehab['review_minutes'] != 1 else ''} at "
        f"{rehab['review_seconds_per_file']} seconds per file",
        f"  {'Sessions to serve':<30} {rehab['sessions']:,} "
        f"(at most {rehab['session_size']} files per session)",
        "",
        f"  {downloads_rehab_closing(rehab, current_tier)}",
        "",
    ]
    return "\n".join(out)


def downloads_rehab_json(rehab: dict, current_tier: str) -> dict:
    """The docket's rehabilitation plan as it appears in --json."""
    return {
        "years": rehab["years"],
        "steps": [
            {
                "key": step["key"],
                "name": step["name"],
                "count": step["count"],
                "bytes": step["bytes"],
                "human": step["human"],
                "cumulative_count": step["cumulative_count"],
                "cumulative_bytes": step["cumulative_bytes"],
                "remaining_count": step["remaining_count"],
                "projected_tier": step["projected_tier"],
            }
            for step in rehab["steps"]
        ],
        "removed_count": rehab["removed_count"],
        "removed_bytes": rehab["removed_bytes"],
        "removed_human": rehab["removed_human"],
        "reclaim_share_pct": rehab["reclaim_share_pct"],
        "remaining_count": rehab["remaining_count"],
        "projected_tier": rehab["projected_tier"],
        "review_minutes": rehab["review_minutes"],
        "sessions": rehab["sessions"],
        "outlook": downloads_rehab_closing(rehab, current_tier),
    }


def render_downloads_rehabilitation_markdown(
    rehab: dict, current_tier: str
) -> list[str]:
    """The '## Rehabilitation Plan' section lines for the docket's
    --out report."""
    out = ["## Rehabilitation Plan", ""]
    out.append(
        "The docket assigns the following plan, in order. Each "
        "step claims only files no earlier step claimed, and "
        "nothing is deleted, moved, or modified by this court; "
        "the plan is yours to serve."
    )
    out.append("")
    active = [step for step in rehab["steps"] if step["count"]]
    if not active:
        out.append("No steps assigned: there is nothing to clear.")
    for index, step in enumerate(active, start=1):
        out.append(
            f"- **Step {index} - {step['name']}:** "
            f"{step['count']:,} files, {step['human']} "
            f"({step['bytes']:,} bytes). {step['detail']} After "
            f"this step: {step['remaining_count']:,} files remain "
            f"(projected verdict: **{step['projected_tier']}**)."
        )
    out += [
        "",
        f"Serving the whole plan would assign "
        f"{rehab['removed_count']:,} files and reclaim "
        f"{rehab['removed_human']} ({rehab['removed_bytes']:,} "
        f"bytes, {rehab['reclaim_share_pct']:.1f}% of the "
        f"evidence), leaving {rehab['remaining_count']:,} files "
        f"and a projected verdict of "
        f"**{rehab['projected_tier']}**. Estimated review time: "
        f"{rehab['review_minutes']:,} "
        f"minute{'s' if rehab['review_minutes'] != 1 else ''} at "
        f"{rehab['review_seconds_per_file']} seconds per file, in "
        f"{rehab['sessions']:,} "
        f"session{'s' if rehab['sessions'] != 1 else ''} of at "
        f"most {rehab['session_size']} files.",
        "",
        downloads_rehab_closing(rehab, current_tier),
        "",
    ]
    return out


def compute_downloads_appeal(stats: dict, tier: str,
                             sentence: str) -> dict:
    """The docket's re-hearing of its verdict on its factors.
    Every statistic the docket already computed is entered as a
    mitigating factor (it argues for mercy) or an aggravating
    factor (it argues against), each with its evidence, and the
    panel rules by the same stated rule as the screenshots court,
    never by vibes:

    - mitigating factors outnumber aggravating factors by
      APPEAL_REDUCTION_MARGIN or more: the appeal is granted and
      the verdict is reduced by exactly one rung on the docket's
      ladder (never below Tidy Desk, where it stands and the
      sentence is commuted instead);
    - mitigating factors outnumber aggravating factors by at least
      APPEAL_COMMUTATION_MARGIN: the verdict stands and the
      sentence is commuted;
    - otherwise: the appeal is denied and both stand.

    The factor thresholds are the docket's own evidence bands, the
    same ones its awards already honor: a duplicate pileup is five
    or more extra copies, an installer pileup is ten or more, an
    archive hoard is fifteen or more, a relic is three years old,
    a landfill is five gigabytes, a time capsule is sixty percent
    over a year old. The original verdict and sentence are always
    reported beside the appealed ones: an appeal re-hears the
    case, it does not rewrite the record."""
    mitigating = []
    aggravating = []

    def factor(key, name, evidence):
        return {"key": key, "name": name, "evidence": evidence}

    if stats["duplicate_extra"] == 0:
        mitigating.append(factor(
            "no_duplicate_waste", "No duplicate waste",
            "Not one duplicate download in the evidence.",
        ))
    if stats["installer_count"] <= 2:
        n = stats["installer_count"]
        mitigating.append(factor(
            "installer_restraint", "Installer restraint",
            f"Only {n} installer{'s' if n != 1 else ''} kept on "
            f"disk: the boxes went out with the software.",
        ))
    if stats["archive_count"] <= 2:
        n = stats["archive_count"]
        mitigating.append(factor(
            "few_unopened_archives", "Few unopened archives",
            f"Only {n} archive{'s' if n != 1 else ''} still zipped.",
        ))
    if stats["old_share"] < 10:
        mitigating.append(factor(
            "little_ancient_evidence", "Little ancient evidence",
            f"Only {stats['old_share']:.1f}% of the files are "
            f"over a year old.",
        ))
    if stats["total_bytes"] < 100 * 1024 ** 2:
        mitigating.append(factor(
            "a_light_load", "A light load",
            f"The whole docket weighs {stats['total_human']}.",
        ))

    if stats["duplicate_extra"] >= 5:
        aggravating.append(factor(
            "duplicate_waste", "Duplicate waste",
            f"{stats['duplicate_extra']:,} extra copies of files "
            f"the defendant already had, "
            f"{human_bytes(stats['duplicate_extra_bytes'])} of "
            f"wasted evidence.",
        ))
    if stats["installer_count"] >= 10:
        aggravating.append(factor(
            "installer_pileup", "An installer pileup",
            f"{stats['installer_count']:,} installers "
            f"({stats['installer_human']}) kept after their work "
            f"was done.",
        ))
    if stats["oldest_age_years"] >= 3:
        aggravating.append(factor(
            "ancient_relic", "An ancient relic",
            f"{stats['oldest_name']} dates to "
            f"{_fmt_date(stats['oldest_date'])}, over three years "
            f"ago, and it is still here.",
        ))
    if stats["archive_count"] >= 15:
        aggravating.append(factor(
            "archive_hoard", "An archive hoard",
            f"{stats['archive_count']:,} archives on disk, still "
            f"zipped. Whatever is inside them, it is staying "
            f"inside them.",
        ))
    if stats["total_bytes"] >= 5 * 1024 ** 3:
        aggravating.append(factor(
            "gigabyte_landfill", "A gigabyte landfill",
            f"The downloads alone total {stats['total_human']}.",
        ))
    if stats["old_share"] >= 60:
        aggravating.append(factor(
            "time_capsule", "A time capsule",
            f"{stats['old_share']:.1f}% of these files are over "
            f"a year old. This is less a folder than a stratum.",
        ))

    net = len(mitigating) - len(aggravating)
    rank = dl_tier_rank(tier)
    if net >= APPEAL_REDUCTION_MARGIN:
        ruling = "granted"
        appealed_tier = DL_TIER_ORDER[max(0, rank - 1)]
        appealed_sentence = DL_APPEAL_COMMUTED_SENTENCE
    elif net >= APPEAL_COMMUTATION_MARGIN:
        ruling = "commuted"
        appealed_tier = tier
        appealed_sentence = DL_APPEAL_COMMUTED_SENTENCE
    else:
        ruling = "denied"
        appealed_tier = tier
        appealed_sentence = sentence
    return {
        "mitigating": mitigating,
        "aggravating": aggravating,
        "net": net,
        "reduction_margin": APPEAL_REDUCTION_MARGIN,
        "commutation_margin": APPEAL_COMMUTATION_MARGIN,
        "ruling": ruling,
        "original_tier": tier,
        "original_sentence": sentence,
        "appealed_tier": appealed_tier,
        "appealed_sentence": appealed_sentence,
    }


def downloads_appeal_closing(appeal: dict) -> str:
    """The docket panel's reasoning, stated with the counts it
    rests on."""
    mitigating_count = len(appeal["mitigating"])
    aggravating_count = len(appeal["aggravating"])
    if appeal["ruling"] == "granted":
        if appeal["appealed_tier"] != appeal["original_tier"]:
            return (
                f"Appeal granted. The mitigating factors "
                f"outweighed the aggravating {mitigating_count} "
                f"to {aggravating_count}, and under the "
                f"{appeal['reduction_margin']}-factor rule the "
                f"verdict is reduced on appeal from "
                f"{appeal['original_tier']} to "
                f"{appeal['appealed_tier']}. The original verdict "
                f"stays in the court record."
            )
        return (
            f"Appeal granted. The mitigating factors outweighed "
            f"the aggravating {mitigating_count} to "
            f"{aggravating_count}, but the verdict is already "
            f"the lowest on the ladder, so it stands at "
            f"{appeal['original_tier']} and the sentence is "
            f"commuted instead."
        )
    if appeal["ruling"] == "commuted":
        return (
            f"Appeal heard. The mitigating factors outweighed "
            f"the aggravating {mitigating_count} to "
            f"{aggravating_count}: not enough for a reduction "
            f"under the {appeal['reduction_margin']}-factor "
            f"rule, but enough to commute the sentence. The "
            f"verdict stands at {appeal['original_tier']}."
        )
    return (
        f"Appeal denied. The aggravating factors matched or "
        f"outweighed the mitigating, {aggravating_count} to "
        f"{mitigating_count}. The verdict and the sentence stand."
    )


def render_downloads_appeal(appeal: dict) -> str:
    """The terminal APPEALS COURT section the docket prints for
    --appeal."""
    out = [
        "THE APPEALS COURT",
        "  The docket re-hears the verdict on its factors. "
        "Mitigating",
        "  factors argue for mercy; aggravating factors argue",
        f"  against. Under the {appeal['reduction_margin']}-factor "
        "rule, mitigating",
        "  factors must outnumber aggravating factors by that many",
        "  to reduce the verdict by one rung; outnumbering them at",
        "  all commutes the sentence.",
    ]
    for kind in ("mitigating", "aggravating"):
        factors = appeal[kind]
        out.append(f"  {kind.capitalize()} factors ({len(factors)})")
        if not factors:
            out.append("    - none entered")
        for entry in factors:
            out.append(f"    - {entry['name']}: {entry['evidence']}")
    out += [
        f"  {'Original verdict':<30} {appeal['original_tier']}",
        f"  {'Ruling':<30} "
        f"{APPEAL_RULING_LABELS[appeal['ruling']]}",
        f"  {'Verdict on appeal':<30} {appeal['appealed_tier']}",
        f"  {'Sentence on appeal':<30} "
        f"{appeal['appealed_sentence']}",
        "",
        f"  {downloads_appeal_closing(appeal)}",
        "",
    ]
    return "\n".join(out)


def downloads_appeal_json(appeal: dict) -> dict:
    """The docket's appeal as it appears in --json."""
    return {
        "mitigating": appeal["mitigating"],
        "aggravating": appeal["aggravating"],
        "mitigating_count": len(appeal["mitigating"]),
        "aggravating_count": len(appeal["aggravating"]),
        "net": appeal["net"],
        "reduction_margin": appeal["reduction_margin"],
        "commutation_margin": appeal["commutation_margin"],
        "ruling": appeal["ruling"],
        "ruling_label": APPEAL_RULING_LABELS[appeal["ruling"]],
        "original_tier": appeal["original_tier"],
        "original_sentence": appeal["original_sentence"],
        "appealed_tier": appeal["appealed_tier"],
        "appealed_sentence": appeal["appealed_sentence"],
        "closing": downloads_appeal_closing(appeal),
    }


def render_downloads_appeal_markdown(appeal: dict) -> list[str]:
    """The '## Appeals Court' section lines for the docket's --out
    report."""
    out = ["## Appeals Court", ""]
    out.append(
        f"The docket re-hears the verdict on its factors. Under "
        f"the {appeal['reduction_margin']}-factor rule, "
        f"mitigating factors must outnumber aggravating factors "
        f"by that many to reduce the verdict by one rung; "
        f"outnumbering them at all commutes the sentence."
    )
    for kind in ("mitigating", "aggravating"):
        factors = appeal[kind]
        out += ["", f"**{kind.capitalize()} factors "
                    f"({len(factors)}):**", ""]
        if not factors:
            out.append("- none entered")
        for entry in factors:
            out.append(f"- **{entry['name']}:** {entry['evidence']}")
    out += [
        "",
        f"Original verdict: **{appeal['original_tier']}**. "
        f"Ruling: **{APPEAL_RULING_LABELS[appeal['ruling']]}**. "
        f"Verdict on appeal: **{appeal['appealed_tier']}**. "
        f"{appeal['appealed_sentence']}",
        "",
        downloads_appeal_closing(appeal),
        "",
    ]
    return out


def compute_downloads_plea_bargain(files: list, stats: dict,
                                   plea_years: int,
                                   voice: str = "fair") -> dict:
    """The docket's plea bargain: what would happen IF the defendant
    deleted (a) every duplicate-download extra copy (the same
    provable waste the cleanup order lists, as computed by
    analyze_downloads) and (b) every file older than `plea_years`
    years, measured from the newest file's date. A file that is both
    old and a duplicate extra is counted once, as old, exactly as
    the screenshots court counts it. Nothing is deleted; this is
    math, on the docket's own ladder."""
    newest_date = max(f.dt.date() for f in files)
    cutoff = _minus_years(newest_date, plea_years)
    duplicate_paths = set(stats["duplicate_extra_paths"])

    removed_old = 0
    removed_duplicates = 0
    removed_bytes = 0
    removed_paths = []
    for f in files:
        is_old = f.dt.date() < cutoff
        is_duplicate = f.path in duplicate_paths
        if is_old:
            removed_old += 1
            removed_bytes += f.size
            removed_paths.append(f.path)
        elif is_duplicate:
            removed_duplicates += 1
            removed_bytes += f.size
            removed_paths.append(f.path)
    removed_count = removed_old + removed_duplicates
    remaining = len(files) - removed_count
    projected_tier, _sentence = downloads_verdict_for(
        remaining, voice=voice
    )
    return {
        "years": plea_years,
        "cutoff": cutoff,
        "old_removed": removed_old,
        "duplicate_removed": removed_duplicates,
        "removed_count": removed_count,
        "removed_bytes": removed_bytes,
        "removed_human": human_bytes(removed_bytes),
        "removed_paths": sorted(removed_paths),
        "remaining_count": remaining,
        "projected_tier": projected_tier,
    }


def downloads_plea_closing(plea: dict, current_tier: str) -> str:
    """The docket's ruling on its plea, in the screenshots plea
    closing's words, judged on the docket's ladder."""
    if dl_tier_rank(plea["projected_tier"]) < dl_tier_rank(
        current_tier
    ):
        return "The court would accept this plea."
    return (
        "The court notes this plea changes nothing. "
        "The verdict stands."
    )


def render_downloads_plea_bargain(plea: dict, current_tier: str) -> str:
    """The terminal PLEA BARGAIN section printed by --plea-bargain
    in the downloads court."""
    out = [
        "THE PLEA BARGAIN",
        f"  If the defendant deleted {plea['old_removed']:,} files older "
        f"than {plea['years']} year{'s' if plea['years'] != 1 else ''} and "
        f"{plea['duplicate_removed']:,} duplicate extras - a purely "
        f"hypothetical exercise; this court deletes nothing - then:",
        f"  {'Files removed':<30} {plea['removed_count']:,}",
        f"  {'Space reclaimed':<30} {plea['removed_human']} "
        f"({plea['removed_bytes']:,} bytes)",
        f"  {'Remaining evidence':<30} {plea['remaining_count']:,} files",
        f"  {'Projected verdict':<30} {plea['projected_tier']}",
        "",
        f"  {downloads_plea_closing(plea, current_tier)}",
        "",
    ]
    return "\n".join(out)


def downloads_plea_json(plea: dict) -> dict:
    """The docket's plea bargain as it appears in --json: the same
    shape the screenshots court uses."""
    return {
        "years": plea["years"],
        "old_removed": plea["old_removed"],
        "duplicate_removed": plea["duplicate_removed"],
        "removed_count": plea["removed_count"],
        "removed_bytes": plea["removed_bytes"],
        "removed_human": plea["removed_human"],
        "remaining_count": plea["remaining_count"],
        "projected_tier": plea["projected_tier"],
    }


def render_downloads_plea_markdown(plea: dict,
                                   current_tier: str) -> list[str]:
    """The '## The Plea Bargain' section lines for the docket's
    --out report."""
    return [
        "## The Plea Bargain",
        "",
        f"If the defendant deleted {plea['old_removed']:,} files "
        f"older than {plea['years']} "
        f"year{'s' if plea['years'] != 1 else ''} and "
        f"{plea['duplicate_removed']:,} duplicate extras - a purely "
        f"hypothetical exercise; this court deletes nothing - then "
        f"{plea['removed_count']:,} files would be removed, "
        f"{plea['removed_human']} ({plea['removed_bytes']:,} bytes) "
        f"reclaimed, and {plea['remaining_count']:,} files "
        f"would remain, for a projected verdict of "
        f"**{plea['projected_tier']}**.",
        "",
        downloads_plea_closing(plea, current_tier),
        "",
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_filter_date(raw: str, flag: str, is_until: bool) -> date:
    """Parse a --since/--until argument: a full YYYY-MM-DD date, or a
    bare four-digit year (Jan 1 for --since, Dec 31 for --until).
    Raises SnapError with a plain message on anything else."""
    if re.fullmatch(r"\d{4}", raw):
        return date(int(raw), 12, 31) if is_until else date(int(raw), 1, 1)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise SnapError(
            f"invalid date for {flag}: {raw!r} (want YYYY-MM-DD)"
        )


# ---------------------------------------------------------------------------
# configuration file (.snapjudgment.toml)
# ---------------------------------------------------------------------------

CONFIG_FILENAME = ".snapjudgment.toml"


def find_config_path(folder_args):
    """The config file in force, or None.

    The current directory wins; failing that, when exactly one folder
    was given on the command line, a config file inside that folder
    applies.
    """
    cwd_candidate = Path.cwd() / CONFIG_FILENAME
    if cwd_candidate.is_file():
        return cwd_candidate
    if len(folder_args) == 1:
        folder_candidate = Path(folder_args[0]).expanduser() / CONFIG_FILENAME
        if folder_candidate.is_file():
            return folder_candidate
    return None


def _strip_toml_comment(line: str) -> str:
    """Remove a trailing # comment, honoring quoted strings."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            if quote == '"' and ch == "\\":
                i += 1  # skip the escaped character
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
        i += 1
    return line


def _flat_toml_string(raw: str) -> str:
    quote = raw[0]
    if len(raw) < 2 or raw[-1] != quote or quote in raw[1:-1]:
        raise ValueError(f"malformed string: {raw!r}")
    return raw[1:-1]


def _split_flat_list(inner: str) -> list:
    parts = []
    buf = []
    quote = None
    for ch in inner:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if quote is not None:
        raise ValueError("unterminated string in list")
    parts.append("".join(buf))
    return parts


def _flat_toml_value(raw: str):
    """One value of the flat config subset: a string, an integer, a
    boolean, or a list of strings. Raises ValueError on anything else."""
    raw = raw.strip()
    if not raw:
        raise ValueError("missing value")
    if raw[0] in "\"'":
        return _flat_toml_string(raw)
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw[0] == "[":
        if not raw.endswith("]"):
            raise ValueError(f"malformed list: {raw!r}")
        inner = raw[1:-1].strip()
        if not inner:
            return []
        values = []
        for part in _split_flat_list(inner):
            value = _flat_toml_value(part)
            if not isinstance(value, str):
                raise ValueError("config lists hold strings only")
            values.append(value)
        return values
    if re.fullmatch(r"[+-]?\d[\d_]*", raw):
        return int(raw.replace("_", ""))
    raise ValueError(f"unsupported value: {raw!r}")


def parse_flat_toml(text: str) -> dict:
    """A tolerant parser for the flat subset of TOML the config file
    uses, for Python 3.9/3.10 where tomllib does not exist. Only
    top-level `key = value` lines are read; tables and anything fancier
    are ignored. Raises ValueError on lines it cannot make sense of
    at all, so a garbled file is still reported as invalid.
    """
    data = {}
    in_table = False
    for raw_line in text.splitlines():
        line = _strip_toml_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("["):
            in_table = True  # keys under a table are not ours to read
            continue
        if in_table:
            continue
        if "=" not in line:
            raise ValueError(f"cannot parse line: {raw_line.strip()!r}")
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"cannot parse line: {raw_line.strip()!r}")
        data[key] = _flat_toml_value(raw_value)
    return data


def read_config(path):
    """Parse a config file into a dict, or None when it is not valid."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except ValueError:  # tomllib.TOMLDecodeError
            return None
    else:
        try:
            data = parse_flat_toml(text)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _config_warning(path, reason):
    print(
        f"snapjudgment: warning: ignoring config file {path}: {reason}",
        file=sys.stderr,
    )


def load_config(folder_args, validate_judge_file=True):
    """The validated contents of the config file in force, as a dict.

    A config file that cannot be parsed, names an unknown judge or
    fail-above tier, carries an invalid intensity, or points at a
    judge pack that does not load is warned about and ignored as a
    whole: a config file must never break a run. Individual keys with
    values of the wrong type are skipped later, when applied.
    """
    path = find_config_path(folder_args)
    if path is None:
        return {}
    data = read_config(path)
    if data is None:
        _config_warning(path, "not valid TOML")
        return {}
    judge = data.get("judge")
    if judge is not None and judge not in VOICES:
        _config_warning(path, f"unknown judge: {judge!r}")
        return {}
    court = data.get("court")
    if court is not None and court not in ("screenshots", "downloads"):
        _config_warning(path, f"unknown court: {court!r}")
        return {}
    intensity = data.get("intensity")
    if intensity is not None:
        try:
            parse_intensity(intensity)
        except SnapError:
            _config_warning(path, f"invalid intensity: {intensity!r}")
            return {}
    fail_above = data.get("fail_above")
    if fail_above is not None and (
        not isinstance(fail_above, str)
        or _normalize_tier_name(fail_above) not in TIER_RANKS
    ):
        _config_warning(path, f"unknown tier for fail_above: {fail_above!r}")
        return {}
    judge_file = data.get("judge_file")
    if validate_judge_file and isinstance(judge_file, str):
        try:
            load_judge_pack(Path(judge_file).expanduser())
        except SnapError as exc:
            _config_warning(path, str(exc))
            return {}
    return data


def explicitly_set(argv):
    """Names of the options actually typed on the command line.

    Parses argv a second time against a twin of the real parser with
    every default suppressed, so only provided options appear. main()
    has already parsed argv successfully before this runs, so the twin
    parse cannot fail on user input.
    """
    parser = build_parser()
    for action in parser._actions:
        action.default = argparse.SUPPRESS
    namespace = argparse.Namespace()
    parser.parse_args(argv, namespace)
    return set(vars(namespace))


def apply_config(args, explicit):
    """Fill options the command line left unset from the config file.

    Command-line flags always win; the config only supplies values for
    options that were not typed. Supported keys: folders, judge,
    judge_file, plea_years, fail_above, all, intensity, court.
    """
    data = load_config(
        args.folders, validate_judge_file="judge_file" not in explicit
    )
    if not data:
        return
    if "court" not in explicit and isinstance(data.get("court"), str):
        args.court = data["court"]
    folders = data.get("folders")
    if not args.folders and isinstance(folders, list) and all(
        isinstance(item, str) for item in folders
    ):
        args.folders = list(folders)
    if "judge" not in explicit and "judge" in data:
        args.judge = data["judge"]
    if "judge_file" not in explicit and isinstance(
        data.get("judge_file"), str
    ):
        args.judge_file = data["judge_file"]
    plea_years = data.get("plea_years")
    if (
        "plea_years" not in explicit
        and isinstance(plea_years, int)
        and not isinstance(plea_years, bool)
        and plea_years >= 0
    ):
        args.plea_years = plea_years
    if "fail_above" not in explicit and "fail_above" in data:
        args.fail_above = data["fail_above"]
    if "all" not in explicit and isinstance(data.get("all"), bool):
        args.all = data["all"]
    if "intensity" not in explicit and "intensity" in data:
        args.intensity = data["intensity"]


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
    parser.add_argument("--court", choices=["screenshots", "downloads"],
                        default="screenshots",
                        help="which court sits: the screenshots court "
                             "(default) or the downloads docket, which "
                             "judges a downloads folder (default folder: "
                             "~/Downloads) from names, sizes, and dates")
    parser.add_argument("--all", action="store_true",
                        help="treat every image in the folders as a screenshot")
    parser.add_argument("--grand-jury", action="store_true",
                        dest="grand_jury",
                        help="convene both courts in one hearing: the "
                             "screenshots court and the downloads docket, "
                             "with a combined FULL DOCKET verdict (pass "
                             "both folders, screenshots first, or pass "
                             "neither and let the court look in the "
                             "usual places)")
    parser.add_argument("--recursive", action="store_true",
                        help="downloads court only: judge subfolders "
                             "too, not just the surface; duplicate "
                             "downloads are grouped within each folder")
    parser.add_argument("--json", action="store_true",
                        help="print the verdict as JSON instead of the report")
    parser.add_argument("--card", metavar="FILE",
                        help="write a self-contained HTML share card to FILE")
    parser.add_argument("--svg", metavar="FILE",
                        help="write an SVG share card (1200x630) to FILE")
    parser.add_argument("--png", metavar="FILE",
                        help="write a PNG share card (1200x630) to FILE")
    parser.add_argument("--out", metavar="FILE",
                        help="write a Markdown report to FILE")
    parser.add_argument("--html", metavar="FILE", dest="html",
                        help="write a full HTML report to FILE")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="only count screenshots on or after this date "
                             "(a bare year, e.g. 2024, also works)")
    parser.add_argument("--until", metavar="YYYY-MM-DD",
                        help="only count screenshots on or before this date "
                             "(a bare year, e.g. 2024, also works)")
    parser.add_argument("--year", metavar="YYYY",
                        help="only count screenshots from this single year")
    parser.add_argument("--plea-bargain", action="store_true",
                        dest="plea_bargain",
                        help="also compute the hypothetical verdict if "
                             "duplicates and old files were deleted "
                             "(nothing is actually deleted)")
    parser.add_argument("--plea-years", metavar="N", dest="plea_years",
                        type=int, default=2,
                        help="with --plea-bargain: files older than "
                             "this many years would go (default: 2)")
    parser.add_argument("--cleanup-list", metavar="FILE",
                        dest="cleanup_list",
                        help="write the cleanup order to FILE: the exact "
                             "list of files a plea bargain would remove "
                             "(duplicate extras and screenshots older than "
                             "--plea-years years), one path per line. "
                             "Nothing is ever deleted.")
    parser.add_argument("--rematch", action="store_true",
                        help="compare this hearing against the last court "
                             "record for the same folders")
    parser.add_argument("--trend", action="store_true",
                        help="record this hearing, then report the trend "
                             "of the court record for the same folders "
                             "and court instead of the full report: "
                             "hearing count, count and verdict "
                             "trajectory, and a sparkline of the counts")
    parser.add_argument("--rehab", action="store_true",
                        help="also assign a rehabilitation plan: a "
                             "staged cleanup plan (duplicate extras, "
                             "then ancient evidence, then the largest "
                             "remaining files; in the downloads court "
                             "the ancient step is ancient installers) "
                             "with the space each step reclaims, the "
                             "projected verdict after each step, a "
                             "review-time estimate, and a session "
                             "schedule (nothing is actually deleted)")
    parser.add_argument("--appeal", action="store_true",
                        help="also convene the appeals court: the "
                             "verdict is re-heard on its mitigating "
                             "and aggravating factors, and under the "
                             "three-factor rule a strong enough case "
                             "reduces the verdict by one rung or "
                             "commutes the sentence (the original "
                             "verdict stays in the record)")
    parser.add_argument("--badge", metavar="FILE",
                        help="write a shields-style verdict badge (SVG) "
                             "to FILE")
    parser.add_argument("--vs", metavar="FOLDER", dest="vs",
                        help="also judge FOLDER as a co-defendant and compare")
    parser.add_argument("--vs-year", metavar="YYYY", dest="vs_year",
                        help="compare year YYYY against the most recent other "
                             "year in the same folders")
    parser.add_argument("--wrapped-kit", metavar="DIR", dest="wrapped_kit",
                        help="write a Wrapped kit (SVG card + post drafts) "
                             "for the current year into DIR")
    parser.add_argument("--fail-above", metavar="TIER", dest="fail_above",
                        help="exit 1 when the verdict ranks above this tier, "
                             "e.g. --fail-above \"Repeat Offender\"")
    parser.add_argument("--judge", choices=list(VOICES), default="fair",
                        help="the presiding judge's voice (default: fair)")
    parser.add_argument("--judge-file", metavar="FILE", dest="judge_file",
                        help="load a custom judge voice from a JSON pack")
    parser.add_argument("--intensity", metavar="LEVEL", default=None,
                        help="Roast intensity for the court's commentary: "
                             "0-100 or gentle|kind|honest|spicy|roast. "
                             "Wording only; the verdict math never changes.")
    parser.add_argument("--oneline", action="store_true",
                        help="print exactly one summary line "
                             "(ignored when --json is given)")
    parser.add_argument("--csv", metavar="FILE",
                        help="write per-file records to FILE as CSV")
    parser.add_argument("--history", action="store_true",
                        help="print the court record (prior hearings) and exit")
    parser.add_argument("--version", action="version",
                        version=f"snapjudgment {__version__}")
    return parser


def _run_downloads_court(args) -> int:
    """One hearing of the downloads docket. Returns the exit code.

    The docket judges every file in the folder (~/Downloads when no
    folder is given), from names, sizes, and dates only. Options that
    belong to the screenshots court are refused with a plain error.
    """
    unsupported = [
        ("--vs-year", args.vs_year is not None),
        ("--judge-file", args.judge_file is not None),
    ]
    for flag, present in unsupported:
        if present:
            print(
                f"snapjudgment: {flag} is not supported in the "
                f"downloads court",
                file=sys.stderr,
            )
            return 2

    if args.plea_years < 0:
        print(
            "snapjudgment: --plea-years must be 0 or more",
            file=sys.stderr,
        )
        return 2

    intensity = None
    if args.intensity is not None:
        try:
            intensity = parse_intensity(args.intensity)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    year = None
    if args.year is not None:
        if len(args.year) != 4 or not args.year.isdigit():
            print(
                "snapjudgment: --year must be a four-digit year, "
                "e.g. --year 2026",
                file=sys.stderr,
            )
            return 2
        year = int(args.year)

    since_date = None
    if args.since is not None:
        try:
            since_date = _parse_filter_date(args.since, "--since", False)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    until_date = None
    if args.until is not None:
        try:
            until_date = _parse_filter_date(args.until, "--until", True)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    kit_dir = None
    if args.wrapped_kit is not None:
        kit_dir = Path(args.wrapped_kit).expanduser()
        if year is None:
            year = datetime.now().year

    fail_rank = None
    if args.fail_above is not None:
        fail_key = _normalize_tier_name(args.fail_above)
        if fail_key not in DL_TIER_RANKS:
            print(
                f"snapjudgment: unknown verdict tier for --fail-above: "
                f"{args.fail_above!r}; valid tiers: "
                f"{', '.join(DL_TIER_ORDER)}",
                file=sys.stderr,
            )
            return 2
        fail_rank = DL_TIER_RANKS[fail_key]

    if args.folders:
        folders = []
        for raw in args.folders:
            folder = Path(raw).expanduser()
            if not folder.is_dir():
                print(
                    f"snapjudgment: folder not found: {raw}",
                    file=sys.stderr,
                )
                return 2
            folders.append(folder)
    else:
        default_folder = Path.home() / "Downloads"
        if not default_folder.is_dir():
            print(
                f"snapjudgment: folder not found: {default_folder}",
                file=sys.stderr,
            )
            return 2
        folders = [default_folder]

    vs_folder = None
    if args.vs is not None:
        vs_folder = Path(args.vs).expanduser()
        if not vs_folder.is_dir():
            print(
                f"snapjudgment: folder not found: {args.vs}",
                file=sys.stderr,
            )
            return 2

    skipped_box = [0]
    files = collect_downloads(
        folders, skipped=skipped_box, recursive=args.recursive
    )
    if year is not None:
        files = [f for f in files if f.dt.year == year]
    if since_date is not None:
        files = [f for f in files if f.dt.date() >= since_date]
    if until_date is not None:
        files = [f for f in files if f.dt.date() <= until_date]

    vs_files: list = []
    if vs_folder is not None:
        vs_files = collect_downloads(
            [vs_folder], recursive=args.recursive
        )
        if year is not None:
            vs_files = [f for f in vs_files if f.dt.year == year]
        if since_date is not None:
            vs_files = [
                f for f in vs_files if f.dt.date() >= since_date
            ]
        if until_date is not None:
            vs_files = [
                f for f in vs_files if f.dt.date() <= until_date
            ]
    folder_names = [str(f) for f in folders]
    folder_stats = [
        {
            "path": str(folder),
            "count": sum(
                1 for f in files if Path(f.path).is_relative_to(folder)
            ),
        }
        for folder in folders
    ]
    judge_label = args.judge
    voice = args.judge

    if not files:
        if args.cleanup_list is not None:
            print(
                "snapjudgment: --cleanup-list cannot be issued: the "
                "downloads folder produced no files to judge",
                file=sys.stderr,
            )
            return 2
        if kit_dir is not None:
            print(
                f"snapjudgment: no files from {year} to wrap; "
                "the kit was not written",
                file=sys.stderr,
            )
            return 2
        if args.json:
            empty_data = {
                "version": __version__,
                "court": "downloads",
                "folders": [
                    {"path": name, "count": 0} for name in folder_names
                ],
                "verdict": "No Evidence",
                "judge": judge_label,
                "count": 0,
                "skipped": skipped_box[0],
                "awards": [],
            }
            if year is not None:
                empty_data["year"] = year
            if intensity is not None:
                empty_data["commentary"] = downloads_commentary(
                    {}, intensity
                )
            print(json.dumps(empty_data, indent=2))
        else:
            if args.oneline:
                print("No Evidence · 0 files")
            else:
                print(render_downloads_no_evidence())
                if intensity is not None:
                    print(
                        "  COMMENTARY: "
                        + downloads_commentary({}, intensity)
                    )
            append_history({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "folders": folder_names,
                "count": 0,
                "total_bytes": 0,
                "verdict": "No Evidence",
                "judge": judge_label,
                "court": "downloads",
            })
        return 2

    if vs_folder is not None and not vs_files:
        print(
            f"snapjudgment: co-defendant folder produced no files: "
            f"{args.vs}",
            file=sys.stderr,
        )
        return 2

    stats = analyze_downloads(files, recursive=args.recursive)
    stats["skipped"] = skipped_box[0]
    tier, sentence = downloads_verdict_for(stats["count"], voice=voice)
    awards = compute_downloads_awards(stats)
    commentary = None
    if intensity is not None:
        commentary = downloads_commentary(stats, intensity)

    versus = None
    if vs_folder is not None:
        vs_stats = analyze_downloads(vs_files, recursive=args.recursive)
        vs_tier, vs_sentence = downloads_verdict_for(
            vs_stats["count"], voice=voice
        )
        versus = {
            "folder": str(vs_folder),
            "stats": vs_stats,
            "tier": vs_tier,
            "sentence": vs_sentence,
            "awards": compute_downloads_awards(vs_stats),
            "worse": downloads_worse_offender(
                stats, tier, vs_stats, vs_tier
            ),
        }

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "folders": folder_names,
        "count": stats["count"],
        "total_bytes": stats["total_bytes"],
        "verdict": tier,
        "judge": judge_label,
        "court": "downloads",
    }
    trend = None
    history_recorded = False
    if args.trend:
        # The hearing is recorded FIRST, so the trend of the court
        # record includes today; the trend is then read back from
        # the record itself.
        append_history(record)
        history_recorded = True
        trend = build_trend(
            read_history(), folder_names, "downloads", record
        )

    cleanup = None
    if args.cleanup_list is not None:
        # The docket's cleanup order lists only the provable waste:
        # the duplicate-download extra copies, every group member
        # except the keeper. Nothing is ever deleted.
        cleanup = {
            "file": args.cleanup_list,
            "count": stats["duplicate_extra"],
            "bytes": stats["duplicate_extra_bytes"],
            "paths": stats["duplicate_extra_paths"],
        }
        header = [
            "# Cleanup order issued by Snap Judgment.",
            f"# {cleanup['count']} files, {cleanup['bytes']} bytes "
            f"({human_bytes(cleanup['bytes'])}) would be removed: "
            f"duplicate download extras, every copy but the keeper.",
            "# The court deletes nothing. This list is yours to "
            "act on, or not.",
        ]
        try:
            Path(args.cleanup_list).write_text(
                "\n".join(header + cleanup["paths"]) + "\n",
                encoding="utf-8",
            )
        except OSError:
            print(
                f"snapjudgment: could not write cleanup list: "
                f"{args.cleanup_list}",
                file=sys.stderr,
            )
            return 2

    rematch = None
    if args.rematch:
        rematch = build_downloads_rematch(
            read_history(), folder_names, stats["count"],
            stats["total_bytes"], tier,
        )

    dl_rehab = None
    if args.rehab:
        # The docket's own rehabilitation plan: duplicate extras,
        # ancient installers, heavy evidence, projected on the
        # docket's ladder. Nothing is ever deleted.
        dl_rehab = compute_downloads_rehabilitation(
            files, stats, voice=voice
        )

    dl_appeal = None
    if args.appeal:
        # The docket's own appeals court: the verdict re-heard on
        # the docket's factors, ruled by the same stated rule.
        dl_appeal = compute_downloads_appeal(stats, tier, sentence)

    dl_plea = None
    if args.plea_bargain:
        # The docket's own plea bargain: duplicate extras and files
        # older than --plea-years, projected on the docket's ladder.
        # Nothing is ever deleted. (The docket's cleanup order is a
        # narrower instrument: it lists only the duplicate extras,
        # the provable waste, and is not the plea.)
        dl_plea = compute_downloads_plea_bargain(
            files, stats, args.plea_years, voice=voice
        )

    if args.card:
        Path(args.card).write_text(
            render_downloads_card(stats, tier, sentence, awards),
            encoding="utf-8",
        )
    if args.svg:
        Path(args.svg).write_text(
            render_downloads_svg(stats, tier, sentence, awards),
            encoding="utf-8",
        )
    if args.png:
        Path(args.png).write_bytes(
            render_downloads_png(stats, tier, sentence, awards)
        )
    if args.badge:
        Path(args.badge).write_text(
            render_downloads_badge(tier), encoding="utf-8"
        )
    if args.csv:
        write_downloads_csv(args.csv, files)
    if args.html:
        Path(args.html).write_text(
            render_downloads_html_report(
                stats, tier, sentence, awards, voice=voice,
                commentary=commentary, year=year,
            ),
            encoding="utf-8",
        )
    if args.out:
        Path(args.out).write_text(
            render_downloads_markdown(
                stats, tier, sentence, awards, voice=voice,
                commentary=commentary, year=year,
                rematch=rematch, rematch_requested=args.rematch,
                versus=versus, trend=trend,
                rehab=dl_rehab, appeal=dl_appeal, plea=dl_plea,
            ),
            encoding="utf-8",
        )
    kit_written = []
    if kit_dir is not None:
        kit_dir.mkdir(parents=True, exist_ok=True)
        kit_svg = kit_dir / f"wrapped-{year}.svg"
        kit_svg.write_text(
            render_downloads_svg(stats, tier, sentence, awards),
            encoding="utf-8",
        )
        kit_post = kit_dir / "post.txt"
        kit_post.write_text(
            build_downloads_wrapped_posts(stats, tier, awards, year),
            encoding="utf-8",
        )
        kit_written = [kit_svg, kit_post]

    if args.json:
        main_folders_json = (
            stats.get("folders", folder_stats) if args.recursive
            else folder_stats
        )
        data = build_downloads_json(
            stats, tier, sentence, awards, main_folders_json,
            judge=judge_label, commentary=commentary,
        )
        if versus is not None:
            data["versus"] = {
                "main": build_downloads_json(
                    stats, tier, sentence, awards, main_folders_json,
                    judge=judge_label, commentary=commentary,
                ),
                "vs": build_downloads_json(
                    versus["stats"], versus["tier"],
                    versus["sentence"], versus["awards"],
                    [{"path": str(vs_folder),
                      "count": versus["stats"]["count"]}],
                    judge=judge_label,
                ),
                "worse_offender": versus["worse"],
            }
        if trend is not None:
            data["trend"] = trend_json(trend)
        if dl_rehab is not None:
            data["rehabilitation"] = downloads_rehab_json(
                dl_rehab, tier
            )
        if dl_appeal is not None:
            data["appeal"] = downloads_appeal_json(dl_appeal)
        if dl_plea is not None:
            data["plea_bargain"] = downloads_plea_json(dl_plea)
        if year is not None:
            data["year"] = year
        if cleanup is not None:
            data["cleanup_order"] = {
                "file": cleanup["file"],
                "count": cleanup["count"],
                "bytes": cleanup["bytes"],
            }
        if args.rematch:
            data["rematch"] = (
                {
                    "previous_count": rematch["previous_count"],
                    "delta": rematch["delta"],
                    "previous_bytes": rematch["previous_bytes"],
                    "bytes_delta": rematch["bytes_delta"],
                    "previous_tier": rematch["previous_tier"],
                    "date": rematch["date"].isoformat(),
                }
                if rematch is not None else None
            )
        print(json.dumps(data, indent=2))
    else:
        if args.trend and trend is not None:
            banner = [
                "=" * 62,
                "SNAP JUDGMENT - THE DOWNLOADS DOCKET",
            ]
            if voice != "fair":
                banner.append(f"Presiding: {JUDGE_NAMES[voice]}")
            banner += ["=" * 62, ""]
            print("\n".join(banner))
            print(render_trend_section(
                trend, "files", folder_names, commentary=commentary,
            ))
        elif args.oneline:
            print(downloads_oneline(stats, tier))
        else:
            print(render_downloads_report(
                stats, tier, sentence, awards, voice=voice,
                commentary=commentary, year=year,
            ))
            if versus is not None:
                print(render_downloads_comparison(
                    stats, tier, awards, versus
                ))
            if args.rematch:
                print(render_downloads_rematch_section(rematch, tier))
            if dl_rehab is not None:
                print(render_downloads_rehabilitation(dl_rehab, tier))
            if dl_appeal is not None:
                print(render_downloads_appeal(dl_appeal))
            if dl_plea is not None:
                print(render_downloads_plea_bargain(dl_plea, tier))
            if cleanup is not None:
                print(
                    f"  CLEANUP ORDER: {cleanup['count']} files "
                    f"({human_bytes(cleanup['bytes'])}) listed in "
                    f"{cleanup['file']}. The court does not swing "
                    f"the axe itself."
                )
            if args.badge:
                print(args.badge)
            for kit_path in kit_written:
                print(kit_path)
        if not history_recorded:
            append_history(record)
    if fail_rank is not None and dl_tier_rank(tier) > fail_rank:
        return 1
    return 0


# ---------------------------------------------------------------------------
# the grand jury: both courts, one hearing, a combined FULL DOCKET
# ---------------------------------------------------------------------------

# The standing of the full docket, keyed by the presiding judge's
# voice and by the pair of severities: "low" when both courts sit in
# their bottom two tiers, "high" when both sit in their top two, and
# "mixed" otherwise. {worse} names the folder carrying the heavier
# evidence ("both folders" when the pair is tied). A court with no
# evidence counts as below the bottom tier.
GRAND_JURY_STANDING = {
    "strict": {
        "low": (
            "The court searched both folders for a case and found "
            "only housekeeping. Suspiciously clean. Both folders "
            "remain under observation."
        ),
        "mixed": (
            "The court weighs both folders and finds the heavier "
            "evidence in {worse}. That is where the sentence lands, "
            "and it will not be light."
        ),
        "high": (
            "Both folders stand guilty, and the combined docket is "
            "the worst this court has entered into the record all "
            "year. The court is almost impressed."
        ),
    },
    "fair": {
        "low": (
            "The court finds little to prosecute. Both folders are "
            "disappointingly tidy, and the court accepts the "
            "disappointment."
        ),
        "mixed": (
            "The court's verdict is split, and the weight of it "
            "lands on {worse}. The sentence falls where the evidence "
            "is heaviest."
        ),
        "high": (
            "The court has seen enough. Two folders, two crime "
            "scenes, one defendant. The full docket is an archive "
            "of good intentions and no deletions."
        ),
    },
    "lenient": {
        "low": (
            "The court finds two tidy folders and very little to "
            "forgive. The case is nearly dismissed, with genuine "
            "affection."
        ),
        "mixed": (
            "The court has weighed the whole docket, and the "
            "heavier heart belongs to {worse}. The court prescribes "
            "a gentle sort, starting there."
        ),
        "high": (
            "The court loves a rich archive, and this is two of "
            "them at once. Nothing here is a crime; it is simply a "
            "great deal of keeping, in stereo."
        ),
    },
}


def grand_jury_standing(voice: str, shots_tier: str, dl_tier: str) -> str:
    """The FULL DOCKET standing line for a pair of verdicts, in the
    presiding judge's voice."""
    shots_rank = (
        tier_rank(shots_tier) if shots_tier != "No Evidence" else -1
    )
    dl_rank = (
        dl_tier_rank(dl_tier) if dl_tier != "No Evidence" else -1
    )
    if max(shots_rank, dl_rank) <= 1:
        pair_class = "low"
    elif min(shots_rank, dl_rank) >= 4:
        pair_class = "high"
    else:
        pair_class = "mixed"
    if shots_rank > dl_rank:
        worse = "the screenshots folder"
    elif dl_rank > shots_rank:
        worse = "the downloads folder"
    else:
        worse = "both folders"
    return GRAND_JURY_STANDING[voice][pair_class].format(worse=worse)


def _hear_screenshots(folder, voice, intensity, all_images=False,
                      prefound=None) -> dict:
    """One screenshots-court hearing for the grand jury. `folder` is
    a Path or None (None, a missing folder, or a folder with no
    screenshots all produce a No Evidence hearing). `prefound` is an
    optional (shots, recordings, skipped) triple from an earlier
    scan of the same folder."""
    hearing = {
        "court": "screenshots",
        "folder": str(folder) if folder is not None else None,
        "has_evidence": False,
        "stats": None,
        "tier": "No Evidence",
        "sentence": "",
        "awards": [],
        "commentary": None,
        "count": 0,
        "total_bytes": 0,
        "recordings": 0,
        "skipped": 0,
        "folders_json": [],
    }
    shots, recordings, skipped = [], [], 0
    if prefound is not None:
        shots, recordings, skipped = prefound
    elif folder is not None and folder.is_dir():
        box = [0]
        shots, recordings = collect(
            [folder], all_images=all_images, skipped=box
        )
        skipped = box[0]
    hearing["recordings"] = len(recordings)
    hearing["skipped"] = skipped
    if folder is not None:
        hearing["folders_json"] = [
            {"path": str(folder), "count": len(shots)}
        ]
    if not shots:
        if intensity is not None:
            hearing["commentary"] = court_commentary({}, intensity)
        return hearing
    stats = analyze(shots, recordings)
    stats["skipped"] = skipped
    stats["date_filter"] = {"since": None, "until": None}
    tier, sentence = verdict_for(stats["count"], voice=voice)
    hearing.update({
        "has_evidence": True,
        "stats": stats,
        "tier": tier,
        "sentence": sentence,
        "awards": compute_awards(stats, voice=voice),
        "count": stats["count"],
        "total_bytes": stats["total_bytes"],
    })
    if intensity is not None:
        hearing["commentary"] = court_commentary(stats, intensity)
    return hearing


def _hear_downloads(folder, voice, intensity, recursive=False) -> dict:
    """One downloads-docket hearing for the grand jury. `folder` is
    a Path or None; a missing or empty folder is a No Evidence
    hearing."""
    hearing = {
        "court": "downloads",
        "folder": str(folder) if folder is not None else None,
        "has_evidence": False,
        "stats": None,
        "tier": "No Evidence",
        "sentence": "",
        "awards": [],
        "commentary": None,
        "count": 0,
        "total_bytes": 0,
        "recordings": 0,
        "skipped": 0,
        "folders_json": [],
    }
    files = []
    skipped = 0
    if folder is not None and folder.is_dir():
        box = [0]
        files = collect_downloads(
            [folder], skipped=box, recursive=recursive
        )
        skipped = box[0]
    hearing["skipped"] = skipped
    if folder is not None:
        hearing["folders_json"] = [
            {"path": str(folder), "count": len(files)}
        ]
    if not files:
        if intensity is not None:
            hearing["commentary"] = downloads_commentary({}, intensity)
        return hearing
    stats = analyze_downloads(files, recursive=recursive)
    stats["skipped"] = skipped
    tier, sentence = downloads_verdict_for(stats["count"], voice=voice)
    hearing.update({
        "has_evidence": True,
        "stats": stats,
        "tier": tier,
        "sentence": sentence,
        "awards": compute_downloads_awards(stats),
        "count": stats["count"],
        "total_bytes": stats["total_bytes"],
    })
    if recursive:
        hearing["folders_json"] = stats.get(
            "folders", hearing["folders_json"]
        )
    if intensity is not None:
        hearing["commentary"] = downloads_commentary(stats, intensity)
    return hearing


def _grand_jury_screenshots_json(hearing: dict, judge_label: str) -> dict:
    """The screenshots hearing as a JSON object: the court's usual
    shape, including its No Evidence shape."""
    if not hearing["has_evidence"]:
        data = {
            "version": __version__,
            "court": "screenshots",
            "folders": hearing["folders_json"],
            "verdict": "No Evidence",
            "judge": judge_label,
            "year": None,
            "count": 0,
            "recordings": hearing["recordings"],
            "skipped": hearing["skipped"],
            "awards": [],
        }
        if hearing["commentary"] is not None:
            data["commentary"] = hearing["commentary"]
        return data
    return build_json(
        hearing["stats"], hearing["tier"], hearing["sentence"],
        hearing["awards"], hearing["folders_json"], year=None,
        judge=judge_label, commentary=hearing["commentary"],
    )


def _grand_jury_downloads_json(hearing: dict, judge_label: str) -> dict:
    """The downloads hearing as a JSON object: the docket's usual
    shape, including its No Evidence shape."""
    if not hearing["has_evidence"]:
        data = {
            "version": __version__,
            "court": "downloads",
            "folders": hearing["folders_json"],
            "verdict": "No Evidence",
            "judge": judge_label,
            "count": 0,
            "skipped": hearing["skipped"],
            "awards": [],
        }
        if hearing["commentary"] is not None:
            data["commentary"] = hearing["commentary"]
        return data
    return build_downloads_json(
        hearing["stats"], hearing["tier"], hearing["sentence"],
        hearing["awards"], hearing["folders_json"],
        judge=judge_label, commentary=hearing["commentary"],
    )


def _grand_jury_combined(shots_h: dict, dl_h: dict) -> dict:
    total_files = shots_h["count"] + dl_h["count"]
    total_bytes = shots_h["total_bytes"] + dl_h["total_bytes"]
    return {
        "files": total_files,
        "bytes": total_bytes,
        "human": human_bytes(total_bytes),
    }


def _grand_jury_card_awards(shots_h: dict, dl_h: dict) -> list:
    """Up to two award names for the FULL DOCKET card: the first
    award from each court."""
    names = []
    if shots_h["awards"]:
        names.append(shots_h["awards"][0]["name"])
    if dl_h["awards"]:
        names.append(dl_h["awards"][0]["name"])
    return names


def _grand_jury_shots_block(hearing: dict) -> str:
    out = ["THE SCREENSHOTS COURT"]
    if not hearing["has_evidence"]:
        out.append("  No Evidence")
        out.append("  No screenshots were produced as evidence.")
    else:
        stats = hearing["stats"]
        out.append(f"  {hearing['tier']}")
        out.append(
            f"  {stats['count']:,} screenshots, "
            f"{stats['total_human']} of evidence,"
        )
        out.append(
            f"  spanning {_fmt_date(stats['first_date'])} to "
            f"{_fmt_date(stats['last_date'])}."
        )
        names = ", ".join(a["name"] for a in hearing["awards"])
        out.append(f"  Awards: {names if names else 'none'}")
        out.append(f"  {hearing['sentence']}")
    if hearing["commentary"] is not None:
        out.append(f"  COMMENTARY: {hearing['commentary']}")
    return "\n".join(out)


def _grand_jury_dl_block(hearing: dict) -> str:
    out = ["THE DOWNLOADS DOCKET"]
    if not hearing["has_evidence"]:
        out.append("  No Evidence")
        out.append("  No files were produced as evidence.")
    else:
        stats = hearing["stats"]
        out.append(f"  {hearing['tier']}")
        out.append(
            f"  {stats['count']:,} files, "
            f"{stats['total_human']} of evidence,"
        )
        out.append(
            f"  spanning {_fmt_date(stats['first_date'])} to "
            f"{_fmt_date(stats['last_date'])}."
        )
        names = ", ".join(a["name"] for a in hearing["awards"])
        out.append(f"  Awards: {names if names else 'none'}")
        out.append(f"  {hearing['sentence']}")
    if hearing["commentary"] is not None:
        out.append(f"  COMMENTARY: {hearing['commentary']}")
    return "\n".join(out)


def render_grand_jury_report(shots_h: dict, dl_h: dict, standing: str,
                             voice: str = "fair") -> str:
    out = [
        "=" * 62,
        "SNAP JUDGMENT - THE GRAND JURY",
    ]
    if voice != "fair":
        out.append(f"Presiding: {JUDGE_NAMES[voice]}")
    out += ["=" * 62, ""]
    intro = JUDGE_INTROS.get(voice)
    if intro:
        out.append(
            f"  {_format_intro(intro, shots_h['tier'], shots_h['count'])}"
        )
        out.append("")
    combined = _grand_jury_combined(shots_h, dl_h)
    out.append(_grand_jury_shots_block(shots_h))
    out.append("")
    out.append(_grand_jury_dl_block(dl_h))
    out.append("")
    out.append("THE FULL DOCKET")
    for label, value in [
        ("Combined files", f"{combined['files']:,}"),
        ("Combined size",
         f"{combined['human']} ({combined['bytes']:,} bytes)"),
        ("Screenshots verdict", shots_h["tier"]),
        ("Downloads verdict", dl_h["tier"]),
    ]:
        out.append(f"  {label:<30} {value}")
    out += ["", f"  {standing}", ""]
    return "\n".join(out)


def render_grand_jury_markdown(shots_h: dict, dl_h: dict,
                               standing: str) -> str:
    out = ["# Snap Judgment - The Grand Jury", ""]
    out += ["## The Screenshots Court", ""]
    if shots_h["has_evidence"]:
        stats = shots_h["stats"]
        out.append(
            f"**{shots_h['tier']}** - {stats['count']:,} screenshots, "
            f"{stats['total_human']} of evidence, spanning "
            f"{_fmt_date(stats['first_date'])} to "
            f"{_fmt_date(stats['last_date'])}."
        )
        names = ", ".join(a["name"] for a in shots_h["awards"])
        out += ["", f"Awards: {names if names else 'none'}",
                "", shots_h["sentence"]]
    else:
        out.append(
            "**No Evidence** - no screenshots were produced as evidence."
        )
    if shots_h["commentary"] is not None:
        out += ["", f"Commentary: {shots_h['commentary']}"]
    out += ["", "## The Downloads Docket", ""]
    if dl_h["has_evidence"]:
        stats = dl_h["stats"]
        out.append(
            f"**{dl_h['tier']}** - {stats['count']:,} files, "
            f"{stats['total_human']} of evidence, spanning "
            f"{_fmt_date(stats['first_date'])} to "
            f"{_fmt_date(stats['last_date'])}."
        )
        names = ", ".join(a["name"] for a in dl_h["awards"])
        out += ["", f"Awards: {names if names else 'none'}",
                "", dl_h["sentence"]]
    else:
        out.append("**No Evidence** - no files were produced as evidence.")
    if dl_h["commentary"] is not None:
        out += ["", f"Commentary: {dl_h['commentary']}"]
    combined = _grand_jury_combined(shots_h, dl_h)
    out += [
        "",
        "## The Full Docket",
        "",
        f"- **Combined files:** {combined['files']:,}",
        f"- **Combined size:** {combined['human']} "
        f"({combined['bytes']:,} bytes)",
        f"- **Screenshots verdict:** {shots_h['tier']}",
        f"- **Downloads verdict:** {dl_h['tier']}",
        "",
        standing,
        "",
        "Judged from file metadata only. No pixels were read, "
        "and no file was opened.",
        "",
    ]
    return "\n".join(out)


def render_grand_jury_card(shots_h: dict, dl_h: dict,
                           standing: str) -> str:
    """The FULL DOCKET HTML share card: both verdicts, the combined
    evidence, one award from each court."""
    esc = html.escape
    combined = _grand_jury_combined(shots_h, dl_h)
    award_names = _grand_jury_card_awards(shots_h, dl_h)
    if award_names:
        awards_html = "\n".join(
            f"        <li><strong>{esc(name)}</strong></li>"
            for name in award_names
        )
    else:
        awards_html = (
            "        <li>None. The court is as surprised as you are.</li>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Snap Judgment - The Full Docket</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0d0d12; color: #e8e3d8; font-family: Georgia, 'Times New Roman', serif;
         display: flex; justify-content: center; padding: 40px 16px; }}
  .card {{ max-width: 640px; width: 100%; background: #16161f; border: 2px solid #b08d3c;
          border-radius: 10px; padding: 36px 40px; box-shadow: 0 12px 40px rgba(0,0,0,.6); }}
  .court {{ text-align: center; letter-spacing: 3px; font-size: 12px; color: #b08d3c;
           text-transform: uppercase; }}
  h1 {{ text-align: center; font-size: 30px; margin: 10px 0 4px; }}
  .tier {{ text-align: center; font-size: 22px; color: #d4af5a; margin: 10px 0 0; }}
  .summary {{ text-align: center; color: #cfc9ba; margin-bottom: 8px; }}
  .headline {{ display: flex; justify-content: space-around; text-align: center;
              border-top: 1px solid #33333f; border-bottom: 1px solid #33333f;
              padding: 16px 0; margin: 18px 0; }}
  .headline .num {{ font-size: 26px; color: #fff; }}
  .headline .lbl {{ font-size: 11px; letter-spacing: 2px; color: #9a94a6; text-transform: uppercase; }}
  h2 {{ font-size: 13px; letter-spacing: 2px; color: #b08d3c; text-transform: uppercase; margin-bottom: 8px; }}
  ul {{ list-style: none; margin-bottom: 18px; }}
  li {{ padding: 4px 0; font-size: 14px; }}
  .sentence {{ font-style: italic; color: #cfc9ba; border-top: 1px solid #33333f; padding-top: 14px; }}
  .foot {{ text-align: center; font-size: 11px; color: #6d6878; margin-top: 16px; }}
  .install {{ text-align: center; font-size: 10px; color: #6d6878; margin-top: 4px;
             font-family: 'Courier New', monospace; }}
</style>
</head>
<body>
  <div class="card">
    <div class="court">The Full Docket</div>
    <h1>Snap Judgment</h1>
    <div class="tier">{esc(shots_h['tier'])}</div>
    <div class="summary">{shots_h['count']:,} screenshots</div>
    <div class="tier">{esc(dl_h['tier'])}</div>
    <div class="summary">{dl_h['count']:,} files</div>
    <div class="headline">
      <div><div class="num">{combined['files']:,}</div><div class="lbl">Files combined</div></div>
      <div><div class="num">{esc(combined['human'])}</div><div class="lbl">Of evidence</div></div>
    </div>
    <h2>Awards</h2>
    <ul>
{awards_html}
    </ul>
    <div class="sentence">{esc(standing)}</div>
    <div class="foot">Judged from file metadata only. No pixels were read, and no file was opened. Snap Judgment, Ghost Developer Studio.</div>
    <div class="install">pip install snap-judgment</div>
  </div>
</body>
</html>
"""


def render_grand_jury_svg(shots_h: dict, dl_h: dict,
                          standing: str) -> str:
    """The FULL DOCKET 1200x630 SVG share card."""
    esc = _xml_escape
    combined = _grand_jury_combined(shots_h, dl_h)
    award_names = "  ·  ".join(_grand_jury_card_awards(shots_h, dl_h))
    if not award_names:
        award_names = "None. The court is as surprised as you are."
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630" role="img" aria-label="Snap Judgment verdict card">
  <rect width="1200" height="630" fill="#0d0d12"/>
  <rect x="24" y="24" width="1152" height="582" rx="14" fill="#16161f" stroke="#b08d3c" stroke-width="3"/>
  <text x="600" y="92" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="19" letter-spacing="6" fill="#b08d3c">SNAP JUDGMENT - THE FULL DOCKET</text>
  <text x="600" y="158" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="58" font-weight="bold" fill="#e8e3d8">SNAP JUDGMENT</text>
  <text x="600" y="222" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="38" fill="#d4af5a">{esc(shots_h['tier'])}</text>
  <text x="600" y="256" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" fill="#e8e3d8">{shots_h['count']:,} screenshots</text>
  <text x="600" y="316" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="38" fill="#d4af5a">{esc(dl_h['tier'])}</text>
  <text x="600" y="350" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" fill="#e8e3d8">{dl_h['count']:,} files</text>
  <line x1="200" y1="384" x2="1000" y2="384" stroke="#33333f" stroke-width="1"/>
  <text x="600" y="436" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="30" fill="#ffffff">{combined['files']:,} files - {esc(combined['human'])} combined</text>
  <text x="600" y="486" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="14" letter-spacing="4" fill="#b08d3c">AWARDS</text>
  <text x="600" y="520" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="24" fill="#e8e3d8">{esc(award_names)}</text>
  <text x="600" y="566" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="15" fill="#6d6878">Judged from file metadata only. No pixels were read, and no file was opened.</text>
  <text x="600" y="592" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="13" letter-spacing="1" fill="#6d6878">pip install snap-judgment</text>
</svg>
"""


def render_grand_jury_png(shots_h: dict, dl_h: dict,
                          standing: str) -> bytes:
    """The FULL DOCKET 1200x630 PNG share card, drawn with the
    bitmap font."""
    canvas = _Canvas(1200, 630, (16, 16, 20))
    canvas.rect(24, 24, 1176, 606, (22, 22, 31))
    canvas.border(24, 24, 1176, 606, (176, 141, 60), 3)
    accent = (176, 141, 60)
    gold = (212, 175, 90)
    paper = (232, 227, 216)
    faint = (109, 104, 120)
    canvas.text(600, 62, "SNAP JUDGMENT - THE FULL DOCKET", 2, accent)
    canvas.text(600, 104, "SNAP JUDGMENT", 7, paper)
    shots_line = (
        f"{shots_h['tier']} - {shots_h['count']:,} screenshots"
    )
    canvas.text(600, 176, shots_line,
                _fit_scale(shots_line, 1080, 4), gold)
    dl_line = f"{dl_h['tier']} - {dl_h['count']:,} files"
    canvas.text(600, 232, dl_line, _fit_scale(dl_line, 1080, 4), gold)
    canvas.rect(200, 268, 1000, 270, (51, 51, 63))
    combined = _grand_jury_combined(shots_h, dl_h)
    combined_line = (
        f"{combined['files']:,} files - {combined['human']} combined"
    )
    canvas.text(600, 300, combined_line,
                _fit_scale(combined_line, 1080, 3), paper)
    names = _grand_jury_card_awards(shots_h, dl_h)
    awards_line = "Awards: " + (", ".join(names) if names else "none")
    canvas.text(600, 356, awards_line,
                _fit_scale(awards_line, 1080, 2), paper)
    canvas.text(600, 440, standing, _fit_scale(standing, 1080, 2),
                (207, 201, 186))
    canvas.text(
        600, 566,
        "Judged from file metadata only. No pixels were read; "
        "no file was opened.",
        2, faint,
    )
    canvas.text(600, 590, "pip install snap-judgment", 1, faint)
    return canvas.to_png()


def _run_grand_jury(args) -> int:
    """One hearing, both courts. Returns the exit code.

    With two positional folders the first is tried by the
    screenshots court and the second by the downloads docket; with
    none, the screenshots court looks in the usual places and the
    docket sits on ~/Downloads. A court whose folder is missing or
    holds no evidence reports No Evidence and the combined report
    still issues for the other.
    """
    voice = args.judge

    intensity = None
    if args.intensity is not None:
        try:
            intensity = parse_intensity(args.intensity)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    shots_fail_rank = None
    dl_fail_rank = None
    if args.fail_above is not None:
        fail_key = _normalize_tier_name(args.fail_above)
        if fail_key in TIER_RANKS:
            shots_fail_rank = TIER_RANKS[fail_key]
        if fail_key in DL_TIER_RANKS:
            dl_fail_rank = DL_TIER_RANKS[fail_key]
        if shots_fail_rank is None and dl_fail_rank is None:
            print(
                f"snapjudgment: unknown verdict tier for --fail-above: "
                f"{args.fail_above!r}; valid tiers: "
                f"{', '.join(TIER_ORDER + DL_TIER_ORDER)}",
                file=sys.stderr,
            )
            return 2

    if len(args.folders) not in (0, 2):
        print(
            "snapjudgment: --grand-jury needs both folders or none: "
            "pass the screenshots folder and the downloads folder, "
            "or pass neither and let the court look in the usual "
            "places",
            file=sys.stderr,
        )
        return 2

    prefound = None
    if len(args.folders) == 2:
        shots_folder = Path(args.folders[0]).expanduser()
        dl_folder = Path(args.folders[1]).expanduser()
    else:
        dl_folder = Path.home() / "Downloads"
        shots_folder = None
        for candidate in default_candidates():
            if not candidate.is_dir():
                continue
            if shots_folder is None:
                shots_folder = candidate
            box = [0]
            found_shots, found_recordings = collect(
                [candidate], all_images=args.all, skipped=box
            )
            if found_shots:
                shots_folder = candidate
                prefound = (found_shots, found_recordings, box[0])
                break

    shots_h = _hear_screenshots(
        shots_folder, voice, intensity,
        all_images=args.all, prefound=prefound,
    )
    dl_h = _hear_downloads(
        dl_folder, voice, intensity, recursive=args.recursive
    )
    standing = grand_jury_standing(voice, shots_h["tier"], dl_h["tier"])
    combined = _grand_jury_combined(shots_h, dl_h)

    if args.card:
        Path(args.card).write_text(
            render_grand_jury_card(shots_h, dl_h, standing),
            encoding="utf-8",
        )
    if args.svg:
        Path(args.svg).write_text(
            render_grand_jury_svg(shots_h, dl_h, standing),
            encoding="utf-8",
        )
    if args.png:
        Path(args.png).write_bytes(
            render_grand_jury_png(shots_h, dl_h, standing)
        )
    if args.out:
        Path(args.out).write_text(
            render_grand_jury_markdown(shots_h, dl_h, standing),
            encoding="utf-8",
        )

    if args.json:
        data = {
            "grand_jury": True,
            "screenshots": _grand_jury_screenshots_json(shots_h, voice),
            "downloads": _grand_jury_downloads_json(dl_h, voice),
            "combined": {
                "files": combined["files"],
                "bytes": combined["bytes"],
                "screenshots_tier": shots_h["tier"],
                "downloads_tier": dl_h["tier"],
            },
        }
        print(json.dumps(data, indent=2))
    else:
        if args.oneline:
            if shots_h["has_evidence"]:
                print(oneline_summary(shots_h["stats"], shots_h["tier"]))
            else:
                print("No Evidence · 0 screenshots")
            if dl_h["has_evidence"]:
                print(downloads_oneline(dl_h["stats"], dl_h["tier"]))
            else:
                print("No Evidence · 0 files")
            print(
                f"Full Docket · {combined['files']:,} files · "
                f"{combined['human']}"
            )
        else:
            print(render_grand_jury_report(shots_h, dl_h, standing,
                                           voice=voice))
        timestamp = datetime.now().isoformat(timespec="seconds")
        append_history({
            "timestamp": timestamp,
            "folders": [shots_h["folder"]] if shots_h["folder"] else [],
            "count": shots_h["count"],
            "total_bytes": shots_h["total_bytes"],
            "verdict": shots_h["tier"],
            "judge": voice,
        })
        append_history({
            "timestamp": timestamp,
            "folders": [dl_h["folder"]] if dl_h["folder"] else [],
            "count": dl_h["count"],
            "total_bytes": dl_h["total_bytes"],
            "verdict": dl_h["tier"],
            "judge": voice,
            "court": "downloads",
        })

    if not shots_h["has_evidence"] and not dl_h["has_evidence"]:
        return 2
    tripped = False
    if (
        shots_fail_rank is not None
        and shots_h["has_evidence"]
        and tier_rank(shots_h["tier"]) > shots_fail_rank
    ):
        tripped = True
    if (
        dl_fail_rank is not None
        and dl_h["has_evidence"]
        and dl_tier_rank(dl_h["tier"]) > dl_fail_rank
    ):
        tripped = True
    return 1 if tripped else 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.grand_jury:
        # The grand jury is one combined hearing; options that belong
        # to a single court's procedure do not combine with it.
        refused = [
            ("--vs", args.vs is not None),
            ("--vs-year", args.vs_year is not None),
            ("--rematch", bool(args.rematch)),
            ("--plea-bargain", bool(args.plea_bargain)),
            ("--cleanup-list", args.cleanup_list is not None),
            ("--wrapped-kit", args.wrapped_kit is not None),
            ("--year", args.year is not None),
            ("--since", args.since is not None),
            ("--until", args.until is not None),
            ("--history", bool(args.history)),
            ("--judge-file", args.judge_file is not None),
            ("--csv", args.csv is not None),
            ("--badge", args.badge is not None),
            ("--html", args.html is not None),
            ("--trend", bool(args.trend)),
            ("--rehab", bool(args.rehab)),
            ("--appeal", bool(args.appeal)),
        ]
        for flag, present in refused:
            if present:
                print(
                    f"snapjudgment: {flag} is not supported "
                    f"with --grand-jury",
                    file=sys.stderr,
                )
                return 2

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

    explicit = explicitly_set(sys.argv[1:] if argv is None else argv)
    apply_config(args, explicit)

    if args.rehab:
        # A rehabilitation plan is one folder set's own staged
        # sentence; proceedings that compare, project, or combine
        # do not combine with it.
        refused = [
            ("--vs", args.vs is not None),
            ("--vs-year", args.vs_year is not None),
            ("--rematch", bool(args.rematch)),
            ("--grand-jury", bool(args.grand_jury)),
            ("--plea-bargain", bool(args.plea_bargain)),
            ("--cleanup-list", args.cleanup_list is not None),
            ("--wrapped-kit", args.wrapped_kit is not None),
            ("--trend", bool(args.trend)),
            ("--appeal", bool(args.appeal)),
        ]
        for flag, present in refused:
            if present:
                print(
                    f"snapjudgment: {flag} is not supported "
                    f"with --rehab",
                    file=sys.stderr,
                )
                return 2

    if args.trend:
        # A trend is a report about one folder set's own history in
        # one court; proceedings that compare, project, or combine
        # do not combine with it.
        refused = [
            ("--vs", args.vs is not None),
            ("--vs-year", args.vs_year is not None),
            ("--rematch", bool(args.rematch)),
            ("--grand-jury", bool(args.grand_jury)),
            ("--plea-bargain", bool(args.plea_bargain)),
            ("--wrapped-kit", args.wrapped_kit is not None),
            ("--rehab", bool(args.rehab)),
            ("--appeal", bool(args.appeal)),
        ]
        for flag, present in refused:
            if present:
                print(
                    f"snapjudgment: {flag} is not supported "
                    f"with --trend",
                    file=sys.stderr,
                )
                return 2

    if args.appeal:
        # An appeal re-hears one folder set's own verdict in one
        # court; proceedings that compare, project, or combine
        # do not combine with it.
        refused = [
            ("--vs", args.vs is not None),
            ("--vs-year", args.vs_year is not None),
            ("--rematch", bool(args.rematch)),
            ("--grand-jury", bool(args.grand_jury)),
            ("--plea-bargain", bool(args.plea_bargain)),
            ("--cleanup-list", args.cleanup_list is not None),
            ("--wrapped-kit", args.wrapped_kit is not None),
            ("--trend", bool(args.trend)),
            ("--rehab", bool(args.rehab)),
        ]
        for flag, present in refused:
            if present:
                print(
                    f"snapjudgment: {flag} is not supported "
                    f"with --appeal",
                    file=sys.stderr,
                )
                return 2

    if args.grand_jury:
        return _run_grand_jury(args)

    if args.court == "downloads":
        return _run_downloads_court(args)

    since_year = None
    since_date = None
    if args.since is not None:
        try:
            since_date = _parse_filter_date(args.since, "--since", False)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    until_date = None
    if args.until is not None:
        try:
            until_date = _parse_filter_date(args.until, "--until", True)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    if args.plea_years < 0:
        print(
            "snapjudgment: --plea-years must be 0 or more",
            file=sys.stderr,
        )
        return 2

    intensity = None
    if args.intensity is not None:
        try:
            intensity = parse_intensity(args.intensity)
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2

    year = None
    until_year = None
    if args.year is not None:
        if len(args.year) != 4 or not args.year.isdigit():
            parser.error("--year must be a four-digit year, e.g. --year 2026")
        year = int(args.year)
        since_year = year
        until_year = year

    vs_year_target = None
    if args.vs_year is not None:
        if len(args.vs_year) != 4 or not args.vs_year.isdigit():
            parser.error("--vs-year must be a four-digit year, e.g. --vs-year 2025")
        vs_year_target = int(args.vs_year)

    fail_rank = None
    if args.fail_above is not None:
        fail_key = _normalize_tier_name(args.fail_above)
        if fail_key not in TIER_RANKS:
            print(
                f"snapjudgment: unknown verdict tier for --fail-above: "
                f"{args.fail_above!r}; valid tiers: {', '.join(TIER_ORDER)}",
                file=sys.stderr,
            )
            return 2
        fail_rank = TIER_RANKS[fail_key]

    kit_dir = None
    if args.wrapped_kit is not None:
        kit_dir = Path(args.wrapped_kit).expanduser()
        if year is None:
            year = datetime.now().year
            since_year = year
            until_year = year

    voice = args.judge
    judge_label = args.judge
    if args.judge_file is not None:
        try:
            pack = load_judge_pack(Path(args.judge_file).expanduser())
        except SnapError as exc:
            print(f"snapjudgment: {exc}", file=sys.stderr)
            return 2
        voice = register_judge_pack(pack)
        judge_label = pack["name"]

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

        all_shots = list(shots)
        vs_year = None
        if vs_year_target is not None and shots:
            vs_year = build_vs_year(shots, recordings, vs_year_target,
                                    voice=voice)

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
        if since_date is not None:
            shots = [s for s in shots if s.dt.date() >= since_date]
            recordings = [r for r in recordings if r.dt.date() >= since_date]
            vs_shots = [s for s in vs_shots if s.dt.date() >= since_date]
            vs_recordings = [
                r for r in vs_recordings if r.dt.date() >= since_date
            ]
        if until_date is not None:
            shots = [s for s in shots if s.dt.date() <= until_date]
            recordings = [r for r in recordings if r.dt.date() <= until_date]
            vs_shots = [s for s in vs_shots if s.dt.date() <= until_date]
            vs_recordings = [
                r for r in vs_recordings if r.dt.date() <= until_date
            ]

        folder_names = [str(f) for f in folders]
        folder_stats = [
            {
                "path": str(folder),
                "count": sum(
                    1 for s in shots if Path(s.path).is_relative_to(folder)
                ),
            }
            for folder in folders
        ]

        if not shots:
            if kit_dir is not None:
                print(
                    f"snapjudgment: no screenshots from {year} to wrap; "
                    "the kit was not written",
                    file=sys.stderr,
                )
                return 2
            if args.json:
                empty_data = {
                    "version": __version__,
                    "court": "screenshots",
                    "folders": [
                        {"path": name, "count": 0} for name in folder_names
                    ],
                    "verdict": "No Evidence",
                    "judge": judge_label,
                    "year": year,
                    "count": 0,
                    "recordings": len(recordings),
                    "skipped": skipped_box[0],
                    "awards": [],
                }
                if intensity is not None:
                    empty_data["commentary"] = court_commentary(
                        {}, intensity
                    )
                print(json.dumps(empty_data, indent=2))
            else:
                if args.oneline:
                    print("No Evidence · 0 screenshots")
                else:
                    print(render_no_evidence())
                    if intensity is not None:
                        print(
                            "  COMMENTARY: "
                            + court_commentary({}, intensity)
                        )
                append_history({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "folders": folder_names,
                    "count": 0,
                    "total_bytes": 0,
                    "verdict": "No Evidence",
                    "judge": judge_label,
                })
            return 2

        if args.vs is not None and not vs_shots:
            raise SnapError(
                f"co-defendant folder produced no screenshots: {args.vs}"
            )

        stats = analyze(shots, recordings)
        stats["skipped"] = skipped_box[0]
        stats["date_filter"] = {"since": since_date, "until": until_date}
        tier, sentence = verdict_for(stats["count"], voice=voice)
        awards = compute_awards(stats, voice=voice)
        commentary = None
        if intensity is not None:
            commentary = court_commentary(stats, intensity)

        plea = None
        if args.plea_bargain:
            plea = compute_plea_bargain(shots, args.plea_years, voice=voice)

        rehab = None
        if args.rehab:
            rehab = compute_rehabilitation(shots, voice=voice)

        appeal = None
        if args.appeal:
            appeal = compute_appeal(stats, tier, sentence)

        cleanup = None
        if args.cleanup_list is not None:
            # The cleanup order IS the plea bargain, computed silently
            # when the plea itself was not requested. Same function,
            # same years: the two can never disagree.
            cleanup_plea = (
                plea if plea is not None
                else compute_plea_bargain(
                    shots, args.plea_years, voice=voice
                )
            )
            cleanup = {
                "file": args.cleanup_list,
                "count": cleanup_plea["removed_count"],
                "bytes": cleanup_plea["removed_bytes"],
                "paths": cleanup_plea["removed_paths"],
            }
            header = [
                "# Cleanup order issued by Snap Judgment.",
                f"# {cleanup['count']} files, {cleanup['bytes']} bytes "
                f"({human_bytes(cleanup['bytes'])}) would be removed: "
                f"duplicate extras and screenshots older than "
                f"{cleanup_plea['years']} "
                f"year{'s' if cleanup_plea['years'] != 1 else ''}.",
                "# The court deletes nothing. This list is yours to "
                "act on, or not.",
            ]
            try:
                Path(args.cleanup_list).write_text(
                    "\n".join(header + cleanup["paths"]) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                raise SnapError(
                    f"could not write cleanup list: {args.cleanup_list}"
                )

        versus = None
        vs_stats = None
        if args.vs is not None:
            vs_stats = analyze(vs_shots, vs_recordings)
            vs_tier, vs_sentence = verdict_for(
                vs_stats["count"], voice=voice
            )
            versus = {
                "folder": str(vs_folder),
                "stats": vs_stats,
                "tier": vs_tier,
                "sentence": vs_sentence,
                "awards": compute_awards(vs_stats, voice=voice),
                "worse": worse_offender(stats, vs_stats),
            }

        history_records = read_history()
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "folders": folder_names,
            "count": stats["count"],
            "total_bytes": stats["total_bytes"],
            "verdict": tier,
            "judge": judge_label,
        }
        trend = None
        history_recorded = False
        if args.trend:
            # The hearing is recorded FIRST, so the trend of the
            # court record includes today; the trend is then read
            # back from the record itself.
            append_history(record)
            history_recorded = True
            trend = build_trend(
                read_history(), folder_names, "screenshots", record
            )
        court_line = None
        if not args.json and folder_names:
            court_line = court_record_line(
                history_records, folder_names[0], stats["count"]
            )
        rematch = None
        if args.rematch:
            rematch = build_rematch(
                history_records, folder_names, stats["count"], tier
            )

        if args.card:
            Path(args.card).write_text(
                render_card(stats, tier, sentence, awards), encoding="utf-8"
            )
        if args.svg:
            Path(args.svg).write_text(
                render_svg(stats, tier, sentence, awards), encoding="utf-8"
            )
        if args.png:
            Path(args.png).write_bytes(
                render_png(stats, tier, sentence, awards)
            )
        if args.badge:
            Path(args.badge).write_text(
                render_badge(tier), encoding="utf-8"
            )
        if args.csv:
            write_csv(args.csv, shots, recordings)
        if args.out:
            Path(args.out).write_text(
                render_markdown(stats, tier, sentence, awards, year=year,
                                voice=voice, versus=versus,
                                folders=folder_stats, vs_year=vs_year,
                                plea=plea, rematch=rematch,
                                rematch_requested=args.rematch,
                                commentary=commentary, trend=trend,
                                rehab=rehab, appeal=appeal),
                encoding="utf-8"
            )
        if args.html:
            Path(args.html).write_text(
                render_html_report(stats, tier, sentence, awards, year=year,
                                   voice=voice, commentary=commentary),
                encoding="utf-8"
            )
        kit_written = []
        if kit_dir is not None:
            kit_dir.mkdir(parents=True, exist_ok=True)
            kit_svg = kit_dir / f"wrapped-{year}.svg"
            kit_svg.write_text(
                render_svg(stats, tier, sentence, awards), encoding="utf-8"
            )
            kit_post = kit_dir / "post.txt"
            kit_post.write_text(
                build_wrapped_posts(stats, tier, awards, year),
                encoding="utf-8",
            )
            kit_written = [kit_svg, kit_post]

        if args.json:
            data = build_json(stats, tier, sentence, awards, folder_stats,
                              year=year, judge=judge_label,
                              commentary=commentary)
            if versus is not None:
                data["versus"] = {
                    "main": build_json(stats, tier, sentence, awards,
                                       folder_stats, year=year,
                                       judge=judge_label),
                    "vs": build_json(vs_stats, versus["tier"],
                                     versus["sentence"], versus["awards"],
                                     [{"path": str(vs_folder),
                                       "count": vs_stats["count"]}],
                                     year=year, judge=judge_label),
                    "worse_offender": versus["worse"],
                }
            if vs_year is not None:
                def _year_folder_stats(y):
                    return [
                        {
                            "path": str(folder),
                            "count": sum(
                                1 for s in all_shots
                                if s.dt.year == y
                                and Path(s.path).is_relative_to(folder)
                            ),
                        }
                        for folder in folders
                    ]

                data["vs_year"] = {
                    "target_year": vs_year["target_year"],
                    "other_year": vs_year["other_year"],
                    "target": build_json(
                        vs_year["target"]["stats"], vs_year["target"]["tier"],
                        vs_year["target"]["sentence"],
                        vs_year["target"]["awards"],
                        _year_folder_stats(vs_year["target_year"]),
                        year=vs_year["target_year"], judge=judge_label),
                    "other": build_json(
                        vs_year["other"]["stats"], vs_year["other"]["tier"],
                        vs_year["other"]["sentence"],
                        vs_year["other"]["awards"],
                        _year_folder_stats(vs_year["other_year"]),
                        year=vs_year["other_year"], judge=judge_label),
                    "change_pct": vs_year["change_pct"],
                }
            if plea is not None:
                data["plea_bargain"] = {
                    "years": plea["years"],
                    "old_removed": plea["old_removed"],
                    "duplicate_removed": plea["duplicate_removed"],
                    "removed_count": plea["removed_count"],
                    "removed_bytes": plea["removed_bytes"],
                    "removed_human": plea["removed_human"],
                    "remaining_count": plea["remaining_count"],
                    "projected_tier": plea["projected_tier"],
                }
            if cleanup is not None:
                data["cleanup_order"] = {
                    "file": cleanup["file"],
                    "count": cleanup["count"],
                    "bytes": cleanup["bytes"],
                }
            if args.rematch:
                data["rematch"] = (
                    {
                        "previous_count": rematch["previous_count"],
                        "delta": rematch["delta"],
                        "previous_tier": rematch["previous_tier"],
                        "date": rematch["date"].isoformat(),
                    }
                    if rematch is not None else None
                )
            if trend is not None:
                data["trend"] = trend_json(trend)
            if rehab is not None:
                data["rehabilitation"] = rehab_json(rehab, tier)
            if appeal is not None:
                data["appeal"] = appeal_json(appeal)
            print(json.dumps(data, indent=2))
        else:
            if args.trend and trend is not None:
                banner = [
                    "=" * 62,
                    "SNAP JUDGMENT - The Court of Screenshot Affairs",
                ]
                if voice != "fair":
                    banner.append(f"Presiding: {JUDGE_NAMES[voice]}")
                banner += ["=" * 62, ""]
                print("\n".join(banner))
                print(render_trend_section(
                    trend, "screenshots", folder_names,
                    commentary=commentary,
                ))
            elif args.oneline:
                print(oneline_summary(stats, tier))
            else:
                print(render_report(stats, tier, sentence, awards, year=year,
                                    voice=voice, court_record=court_line,
                                    folders=folder_stats,
                                    commentary=commentary))
                if versus is not None:
                    print(render_comparison(stats, tier, versus))
                if vs_year is not None:
                    print(render_year_comparison(vs_year))
                if plea is not None:
                    print(render_plea_bargain(plea, tier))
                if rehab is not None:
                    print(render_rehabilitation(rehab, tier))
                if appeal is not None:
                    print(render_appeal(appeal))
                if cleanup is not None:
                    print(
                        f"  CLEANUP ORDER: {cleanup['count']} files "
                        f"({human_bytes(cleanup['bytes'])}) listed in "
                        f"{cleanup['file']}. The court does not swing "
                        f"the axe itself."
                    )
                if args.rematch:
                    print(render_rematch_section(rematch, tier))
                if args.badge:
                    print(args.badge)
                for kit_path in kit_written:
                    print(kit_path)
            if not history_recorded:
                append_history(record)
        if fail_rank is not None and tier_rank(tier) > fail_rank:
            return 1
        return 0
    except SnapError as exc:
        print(f"snapjudgment: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
