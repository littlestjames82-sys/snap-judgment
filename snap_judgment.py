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
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

__version__ = "0.1.0"

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

def collect(folders, all_images: bool = False):
    """Walk folders recursively. Returns (screenshots, recordings)."""
    shots: list[Shot] = []
    recordings: list[Shot] = []
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
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
    return shots, recordings


def default_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / "Pictures" / "Screenshots",
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


def _fmt_hour(hour: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {suffix}"


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

    night_count = sum(1 for s in shots if s.dt.hour >= 22 or s.dt.hour < 5)
    night_share = round(100.0 * night_count / count, 1) if count else 0.0

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
        "busiest_hour": busiest_hour,
        "busiest_hour_count": busiest_hour_count,
        "night_count": night_count,
        "night_share": night_share,
        "work_count": work_count,
        "work_share": work_share,
        "longest_streak": longest_streak,
        "burst_record": burst,
        "duplicate_groups": dup_groups,
        "duplicate_files": dup_files,
        "device_eras": device_eras,
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


def verdict_for(count: int):
    """(tier name, sentence) for a screenshot count."""
    if count <= 0:
        return ("No Evidence", "")
    for minimum, name, sentence in TIERS:
        if count >= minimum:
            return (name, sentence)
    return ("Innocent Bystander", TIERS[-1][2])


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
        ("Date span", f"{_fmt_date(stats['first_date'])} - {_fmt_date(stats['last_date'])} "
                      f"({stats['span_days'] + 1:,} day{'s' if stats['span_days'] else ''})"),
        ("Oldest screenshot", f"{stats['oldest_name']} ({_fmt_date(stats['oldest_date'])})"),
        ("Busiest day", f"{_fmt_date(stats['busiest_day'])} ({stats['busiest_day_count']} screenshots)"),
        ("Busiest hour", f"{_fmt_hour(stats['busiest_hour'])} ({stats['busiest_hour_count']} screenshots)"),
        ("Taken at night (10 PM-5 AM)", f"{stats['night_count']} ({stats['night_share']:.1f}%)"),
        ("Longest daily streak", f"{stats['longest_streak']} days"),
        ("Biggest burst", f"{stats['burst_record']} screenshots in 60 seconds"),
        ("Duplicate suspects", f"{stats['duplicate_groups']} "
                               f"group{'s' if stats['duplicate_groups'] != 1 else ''}, "
                               f"{stats['duplicate_files']} files"),
        ("Screen recordings", f"{stats['recordings']}"),
        ("Device eras", f"{len(stats['device_eras'])}"),
    ]
    return lines


def render_report(stats: dict, tier: str, sentence: str, awards: list[dict]) -> str:
    out = [
        "=" * 62,
        "SNAP JUDGMENT - The Court of Screenshot Affairs",
        "=" * 62,
        "",
        "THE VERDICT",
        f"  {tier}",
        f"  {stats['count']:,} screenshots, {stats['total_human']} of evidence,",
        f"  spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.",
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
    out += ["", "AWARDS"]
    if awards:
        for award in awards:
            out.append(f"  * {award['name']} - {award['evidence']}")
    else:
        out.append("  None. The court is as surprised as you are.")
    out += ["", "THE SENTENCE", f"  {sentence}", ""]
    return "\n".join(out)


def render_markdown(stats: dict, tier: str, sentence: str, awards: list[dict]) -> str:
    out = [
        "# Snap Judgment",
        "",
        "## The Verdict",
        "",
        f"**{tier}** - {stats['count']:,} screenshots, {stats['total_human']} of evidence, "
        f"spanning {_fmt_date(stats['first_date'])} to {_fmt_date(stats['last_date'])}.",
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
    out += ["", "## Awards", ""]
    if awards:
        for award in awards:
            out.append(f"- **{award['name']}** - {award['evidence']}")
    else:
        out.append("None. The court is as surprised as you are.")
    out += ["", "## The Sentence", "", sentence, ""]
    return "\n".join(out)


def build_json(stats: dict, tier: str, sentence: str, awards: list[dict],
               folders: list[str]) -> dict:
    return {
        "version": __version__,
        "folders": folders,
        "verdict": tier,
        "sentence": sentence,
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
        "per_year": {str(y): n for y, n in stats["per_year"].items()},
        "busiest_day": {
            "date": stats["busiest_day"].isoformat(),
            "count": stats["busiest_day_count"],
        },
        "busiest_hour": {
            "hour": stats["busiest_hour"],
            "count": stats["busiest_hour_count"],
        },
        "night_count": stats["night_count"],
        "night_share_pct": stats["night_share"],
        "work_share_pct": stats["work_share"],
        "longest_streak_days": stats["longest_streak"],
        "burst_record": stats["burst_record"],
        "duplicate_groups": stats["duplicate_groups"],
        "duplicate_files": stats["duplicate_files"],
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
    parser.add_argument("--out", metavar="FILE",
                        help="write a Markdown report to FILE")
    parser.add_argument("--since", metavar="YYYY",
                        help="only count screenshots from this year onward")
    parser.add_argument("--version", action="version",
                        version=f"snapjudgment {__version__}")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    since_year = None
    if args.since is not None:
        if len(args.since) != 4 or not args.since.isdigit():
            parser.error("--since must be a four-digit year, e.g. --since 2024")
        since_year = int(args.since)

    try:
        if args.folders:
            folders = []
            for raw in args.folders:
                folder = Path(raw).expanduser()
                if not folder.is_dir():
                    raise SnapError(f"folder not found: {raw}")
                folders.append(folder)
            shots, recordings = collect(folders, all_images=args.all)
        else:
            shots, recordings = [], []
            folders = []
            for candidate in default_candidates():
                if candidate.is_dir():
                    found_shots, found_recordings = collect(
                        [candidate], all_images=args.all
                    )
                    if found_shots:
                        shots, recordings = found_shots, found_recordings
                        folders = [candidate]
                        break
            if not folders:
                raise SnapError(
                    "no screenshots found in the usual places "
                    "(~/Pictures/Screenshots, ~/Desktop, ~/Pictures, ~/Downloads); "
                    "pass a folder: snapjudgment /path/to/folder"
                )

        if since_year is not None:
            shots = [s for s in shots if s.dt.year >= since_year]
            recordings = [r for r in recordings if r.dt.year >= since_year]

        folder_names = [str(f) for f in folders]

        if not shots:
            if args.json:
                print(json.dumps({
                    "version": __version__,
                    "folders": folder_names,
                    "verdict": "No Evidence",
                    "count": 0,
                    "recordings": len(recordings),
                    "awards": [],
                }, indent=2))
            else:
                print(render_no_evidence())
            return 2

        stats = analyze(shots, recordings)
        tier, sentence = verdict_for(stats["count"])
        awards = compute_awards(stats)

        if args.card:
            Path(args.card).write_text(
                render_card(stats, tier, sentence, awards), encoding="utf-8"
            )
        if args.out:
            Path(args.out).write_text(
                render_markdown(stats, tier, sentence, awards), encoding="utf-8"
            )

        if args.json:
            print(json.dumps(
                build_json(stats, tier, sentence, awards, folder_names), indent=2
            ))
        else:
            print(render_report(stats, tier, sentence, awards))
        return 0
    except SnapError as exc:
        print(f"snapjudgment: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
