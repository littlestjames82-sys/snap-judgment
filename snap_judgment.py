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

__version__ = "0.7.0"

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

CUSTOM_VOICE_KEY = "custom"


def load_judge_pack(path) -> dict:
    """Load and validate a custom judge pack (JSON). Raises SnapError
    with a plain message on any problem.

    Format: {"name": str, "intro": str (may use {tier} and {count}),
    "sentences": {tier name: sentence}, "award_lead": str}. Every field
    except "name" is optional; tiers missing from "sentences" fall back
    to the fair judge's sentence for that tier.
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
    return {
        "name": name,
        "intro": intro,
        "sentences": sentences,
        "award_lead": award_lead,
    }


def register_judge_pack(pack: dict) -> str:
    """Install a loaded pack as the custom voice. Returns its voice key."""
    key = CUSTOM_VOICE_KEY
    JUDGE_NAMES[key] = pack["name"]
    VOICE_SENTENCES[key] = dict(pack["sentences"])
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
    if stats["three_am_count"] >= 10:
        awards.append({
            "name": "The 3 AM Club",
            "evidence": f"{stats['three_am_count']} screenshots taken between "
                        f"2 AM and 4 AM, when nothing on screen can be urgent.",
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
    habit_share = (
        round(100.0 * stats["busiest_hour_count"] / count, 1) if count else 0.0
    )
    if habit_share >= 25:
        awards.append({
            "name": "Creature of Habit",
            "evidence": f"{habit_share:.1f}% of all screenshots were taken in the "
                        f"{_fmt_hour(stats['busiest_hour'])} hour. Same time, same impulse.",
        })
    if stats["total_bytes"] >= 1024 ** 3:
        awards.append({
            "name": "Gigabyte Club",
            "evidence": f"The screenshots alone total {stats['total_human']}. "
                        f"The evidence locker needs a bigger room.",
        })
    if stats.get("future_shot") is not None:
        future = stats["future_shot"]
        awards.append({
            "name": "Time Traveler",
            "evidence": f"{future['name']} is dated {_fmt_date(future['date'])}, "
                        f"which has not happened yet. The court admits it anyway.",
        })
    if stats["span_days"] >= 4 * 365:
        awards.append({
            "name": "The Long Haul",
            "evidence": f"The collection spans {stats['span_days'] / 365.25:.1f} years of screenshots.",
        })
    if stats["span_days"] >= 8 * 365:
        awards.append({
            "name": "Lifer",
            "evidence": f"The collection spans {stats['span_days'] / 365.25:.1f} years. "
                        f"The screenshots have seniority now.",
        })
    if stats["span_days"] >= 10 * 365:
        awards.append({
            "name": "Ancient Relic",
            "evidence": f"{stats['oldest_name']} dates to "
                        f"{_fmt_date(stats['oldest_date'])}, over ten years "
                        f"before the newest screenshot in evidence.",
        })
    if stats["anniversary"] is not None and stats["anniversary"]["count"] >= 15:
        anniv = stats["anniversary"]
        awards.append({
            "name": "Anniversary",
            "evidence": f"{anniv['count']} screenshots fall on {anniv['month_day']}, "
                        f"gathered across {anniv['years']} different years. "
                        f"Some dates just attract evidence.",
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
    for s in shots:
        is_old = s.dt.date() < cutoff
        is_duplicate = id(s) in duplicate_extra_ids
        if is_old:
            removed_old += 1
            removed_bytes += s.size
        elif is_duplicate:
            removed_duplicates += 1
            removed_bytes += s.size
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
                  folders: list[dict] | None = None) -> str:
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
            "awards": compute_awards(year_stats),
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
                    rematch_requested: bool = False) -> str:
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
    return "\n".join(out)


def build_json(stats: dict, tier: str, sentence: str, awards: list[dict],
               folders: list[dict], year: int | None = None,
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


def render_badge(tier: str) -> str:
    """A shields-style verdict badge: 'snap judgment' on the left, the
    tier on the right, colored by severity."""
    esc = _xml_escape
    left_text = "snap judgment"
    left_w = int(len(left_text) * 6.5) + 12
    right_w = int(len(tier) * 6.5) + 12
    color = BADGE_TIER_COLORS.get(tier, "#9f9f9f")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{left_w + right_w}" height="20" role="img" aria-label="snap judgment: {esc(tier)}">
  <rect width="{left_w}" height="20" fill="#555555"/>
  <rect x="{left_w}" width="{right_w}" height="20" fill="{color}"/>
  <text x="{left_w / 2:.1f}" y="14" text-anchor="middle" font-family="Verdana, Geneva, sans-serif" font-size="11" fill="#ffffff">{esc(left_text)}</text>
  <text x="{left_w + right_w / 2:.1f}" y="14" text-anchor="middle" font-family="Verdana, Geneva, sans-serif" font-size="11" fill="#ffffff">{esc(tier)}</text>
</svg>
"""


def render_html_report(stats: dict, tier: str, sentence: str,
                       awards: list[dict], year: int | None = None,
                       voice: str = "fair") -> str:
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
    <div class="sentence">{esc(sentence)}</div>
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
    fail-above tier, or points at a judge pack that does not load is
    warned about and ignored as a whole: a config file must never
    break a run. Individual keys with values of the wrong type are
    skipped later, when applied.
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
    judge_file, plea_years, fail_above, all.
    """
    data = load_config(
        args.folders, validate_judge_file="judge_file" not in explicit
    )
    if not data:
        return
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
                             "duplicates and old screenshots were deleted "
                             "(nothing is actually deleted)")
    parser.add_argument("--plea-years", metavar="N", dest="plea_years",
                        type=int, default=2,
                        help="with --plea-bargain: screenshots older than "
                             "this many years would go (default: 2)")
    parser.add_argument("--rematch", action="store_true",
                        help="compare this hearing against the last court "
                             "record for the same folders")
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

    explicit = explicitly_set(sys.argv[1:] if argv is None else argv)
    apply_config(args, explicit)

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
                print(json.dumps({
                    "version": __version__,
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
                }, indent=2))
            else:
                if args.oneline:
                    print("No Evidence · 0 screenshots")
                else:
                    print(render_no_evidence())
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
        awards = compute_awards(stats)

        plea = None
        if args.plea_bargain:
            plea = compute_plea_bargain(shots, args.plea_years, voice=voice)

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
                "awards": compute_awards(vs_stats),
                "worse": worse_offender(stats, vs_stats),
            }

        history_records = read_history()
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
                                rematch_requested=args.rematch),
                encoding="utf-8"
            )
        if args.html:
            Path(args.html).write_text(
                render_html_report(stats, tier, sentence, awards, year=year,
                                   voice=voice),
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
                              year=year, judge=judge_label)
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
            print(json.dumps(data, indent=2))
        else:
            if args.oneline:
                print(oneline_summary(stats, tier))
            else:
                print(render_report(stats, tier, sentence, awards, year=year,
                                    voice=voice, court_record=court_line,
                                    folders=folder_stats))
                if versus is not None:
                    print(render_comparison(stats, tier, versus))
                if vs_year is not None:
                    print(render_year_comparison(vs_year))
                if plea is not None:
                    print(render_plea_bargain(plea, tier))
                if args.rematch:
                    print(render_rematch_section(rematch, tier))
                if args.badge:
                    print(args.badge)
                for kit_path in kit_written:
                    print(kit_path)
            append_history({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "folders": folder_names,
                "count": stats["count"],
                "total_bytes": stats["total_bytes"],
                "verdict": tier,
                "judge": judge_label,
            })
        if fail_rank is not None and tier_rank(tier) > fail_rank:
            return 1
        return 0
    except SnapError as exc:
        print(f"snapjudgment: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
