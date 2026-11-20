#!/usr/bin/env python3
"""Build the Snap Judgment media pack: pictures and motion exhibits for
the repo, the site, and social posts. Usage:

    python3 tools/make_media.py

Every image is drawn with the tool's own pure-stdlib renderer (the
5x7 bitmap font and RGB canvas imported from snap_judgment) and the
GIF89a encoder pattern from tools/make_demo_gif.py. No image library,
no external assets. All numbers come from judging the deterministic
demo folder (/tmp/snapdemo) in-process, so the exhibits can never
drift from what the tool actually says. Re-running this script
reproduces every file byte for byte (the optional MP4 is encoded
with ffmpeg's -bitexact flag for the same reason).

Outputs:
    examples/media/verdict-ladder.png    the six verdict tiers
    examples/media/awards-grid.png       all sixteen awards
    examples/media/plea-before-after.png the demo folder's plea bargain
    examples/media/heatmap.png           the demo folder's 7x24 heatmap
    examples/media/verdict-reveal.gif    the verdict card, assembling
    examples/media/demo.mp4              the demo GIF as MP4 (if ffmpeg)
    examples/media/authors-verdict.png   the author's own real card
    examples/media/README.md             the index of all of the above
    docs/assets/{verdict-ladder,awards-grid,heatmap}.png,
    docs/assets/verdict-reveal.gif       copies for the site

Standard library only, like everything else in this repo (ffmpeg is
invoked only when it is already installed; nothing is downloaded).
"""
import os
import time

os.environ["TZ"] = "UTC"
time.tzset()

import calendar
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_REAL_HOME = os.path.expanduser("~")
_SCRATCH_HOME = tempfile.mkdtemp(prefix="snap-media-home-")
os.environ["HOME"] = _SCRATCH_HOME

import snap_judgment as sj  # noqa: E402

MEDIA_DIR = os.path.join(ROOT, "examples", "media")
ASSETS_DIR = os.path.join(ROOT, "docs", "assets")
DEMO_FOLDER = "/tmp/snapdemo"
AUTHORS_CARD = os.path.join(
    _REAL_HOME, "workspace", "user", "files",
    "snap-judgment-card_1_6u67.png")
FIXED_NOW = datetime(2026, 11, 19, 12, 0, 0)

# The courtroom palette, matching snap_judgment.render_png.
BG = (16, 16, 20)
PANEL = (22, 22, 31)
ACCENT = (176, 141, 60)
GOLD = (212, 175, 90)
PAPER = (232, 227, 216)
MUTED = (154, 148, 166)
FAINT = (109, 104, 120)
DIVIDER = (51, 51, 63)
SENTENCE = (207, 201, 186)
HEAT_BASE = (26, 26, 36)


def _hex_rgb(value):
    """'#4c1' or '#dfb317' -> (r, g, b), CSS shorthand aware."""
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


TIER_COLORS = {name: _hex_rgb(color)
               for name, color in sj.BADGE_TIER_COLORS.items()}

# (tier, range label) in ascending severity, matching TIERS / TIER_ORDER.
LADDER = [
    ("Innocent Bystander", "1 - 49 SCREENSHOTS"),
    ("Petty Offender", "50 - 199 SCREENSHOTS"),
    ("Repeat Offender", "200 - 499 SCREENSHOTS"),
    ("Habitual Hoarder", "500 - 999 SCREENSHOTS"),
    ("Felony-Level Archivist", "1000 - 2499 SCREENSHOTS"),
    ("Unrepentant Screenshot Felon", "2500+ SCREENSHOTS"),
]

# The sixteen awards, in compute_awards order, each with a description
# of six words or fewer.
AWARDS = [
    ("Night Owl", "Bulk screenshots taken after dark"),
    ("The 3 AM Club", "Up at 3 AM, screenshotting anyway"),
    ("Burst Mode", "Rapid fire inside sixty seconds"),
    ("The Archivist", "Oldest screenshot over three years"),
    ("Receipt Keeper", "Weekday nine-to-five evidence"),
    ("Repeat Customer", "The same screenshot, taken again"),
    ("Creature of Habit", "One favorite hour, every day"),
    ("Gigabyte Club", "Over a gigabyte of evidence"),
    ("Time Traveler", "A screenshot from the future"),
    ("The Long Haul", "Four-plus years of screenshots"),
    ("Lifer", "Eight-plus years in the game"),
    ("Ancient Relic", "A decade-old screenshot survives"),
    ("Anniversary", "One date attracts screenshots yearly"),
    ("Screenshot Streak", "Seven straight days of screenshots"),
    ("Clean Stretch", "Thirty days without a screenshot"),
    ("Minimalist", "Fewer than fifty screenshots total"),
]


# ---------------------------------------------------------------------------
# drawing helpers (the canvas itself is snap_judgment._Canvas)
# ---------------------------------------------------------------------------

def draw_text_left(canvas, x, y, text, scale, color):
    """Bitmap text with its left edge at x (canvas.text only centers)."""
    for ch in text:
        glyph = sj._glyph(ch)
        for row_i, row in enumerate(glyph):
            for col_i, mark in enumerate(row):
                if mark == "#":
                    canvas.rect(
                        x + col_i * scale, y + row_i * scale,
                        x + (col_i + 1) * scale, y + (row_i + 1) * scale,
                        color,
                    )
        x += 6 * scale


def wrap_words(text, max_chars):
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def framed(width, height):
    canvas = sj._Canvas(width, height, BG)
    canvas.rect(24, 24, width - 24, height - 24, PANEL)
    canvas.border(24, 24, width - 24, height - 24, ACCENT, 3)
    return canvas


# ---------------------------------------------------------------------------
# the demo folder: use /tmp/snapdemo; recreate it deterministically if gone
# ---------------------------------------------------------------------------

def _png_header(w, h):
    def chunk(tag, data):
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def build_demo_folder(folder):
    """Fallback recreation of the deterministic demo set: 310
    screenshots, 2024-2026, two dimension pairs, a burst of 12, a
    10-day streak, ~29% at night, one 12-file duplicate group, and
    two screen recordings."""
    rng = random.Random(20261119)
    os.makedirs(folder, exist_ok=True)
    start, end = datetime(2024, 1, 5), datetime(2026, 11, 10)
    span = (end - start).days

    def dims_for(dt):
        return (1170, 2532) if dt < datetime(2025, 7, 1) else (1920, 1080)

    records = []
    for _ in range(274):
        day = start + timedelta(days=rng.randrange(0, span))
        if rng.random() < 0.29:
            hour = rng.choice([22, 23, 0, 1, 2, 3, 4])
        else:
            hour = rng.randrange(6, 22)
        dt = day.replace(hour=hour, minute=rng.randrange(60),
                         second=rng.randrange(60))
        records.append((dt, dims_for(dt), rng.randrange(20_000, 120_000)))
    for d in range(10):  # the 10-day streak
        dt = datetime(2026, 8, 5 + d, 12, 30, 0)
        records.append((dt, (1920, 1080), rng.randrange(20_000, 120_000)))
    base = datetime(2026, 8, 14, 21, 14, 0)  # the burst of 12
    for i in range(12):
        records.append((base + timedelta(seconds=4 * i), (1920, 1080),
                        rng.randrange(20_000, 120_000)))
    for _ in range(14):
        day = start + timedelta(days=rng.randrange(0, span))
        dt = day.replace(hour=rng.randrange(7, 22),
                         minute=rng.randrange(60),
                         second=rng.randrange(60))
        records.append((dt, dims_for(dt), rng.randrange(20_000, 120_000)))
    for i in range(12):  # one duplicate group of 12 (size + dimensions)
        dt, _dims, _size = records[i]
        records[i] = (dt, (1170, 2532), 51_500)

    used = set()
    for dt, dims, size in records:
        name = "Screenshot {:%Y-%m-%d} at {:%H.%M.%S}.png".format(dt, dt)
        while name in used:
            dt += timedelta(seconds=1)
            name = "Screenshot {:%Y-%m-%d} at {:%H.%M.%S}.png".format(dt, dt)
        used.add(name)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(_png_header(*dims))
            fh.truncate(size)
        epoch = calendar.timegm(dt.timetuple())
        os.utime(path, (epoch, epoch))
    for name, dt, size in (
        ("Screen Recording 2026-05-01 at 20.00.00.mov",
         datetime(2026, 5, 1, 20, 0, 0), 2_400_000),
        ("Screen Recording 2026-09-09 at 22.30.00.mov",
         datetime(2026, 9, 9, 22, 30, 0), 3_100_000),
    ):
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.truncate(size)
        epoch = calendar.timegm(dt.timetuple())
        os.utime(path, (epoch, epoch))


def load_demo():
    if not os.path.isdir(DEMO_FOLDER):
        build_demo_folder(DEMO_FOLDER)
    skipped = [0]
    shots, recordings = sj.collect([DEMO_FOLDER], skipped=skipped)
    stats = sj.analyze(shots, recordings)
    stats["skipped"] = skipped[0]
    tier, sentence = sj.verdict_for(stats["count"])
    awards = sj.compute_awards(stats, now=FIXED_NOW)
    plea = sj.compute_plea_bargain(shots, 2)
    return stats, tier, sentence, awards, plea


# ---------------------------------------------------------------------------
# exhibit 1: the verdict ladder
# ---------------------------------------------------------------------------

def verdict_ladder_png():
    canvas = framed(1200, 800)
    canvas.text(600, 54, "THE VERDICTS", 6, GOLD)
    canvas.text(600, 118, "SIX TIERS OF SCREENSHOT JUSTICE", 2, MUTED)
    y = 168
    for tier, label in LADDER:
        color = TIER_COLORS[tier]
        canvas.rect(64, y, 92, y + 58, color)
        scale = sj._fit_scale(tier.upper(), 760, 4)
        draw_text_left(canvas, 112, y, tier.upper(), scale, color)
        draw_text_left(canvas, 112, y + 38, label, 2, MUTED)
        y += 94
    canvas.text(600, 744, "pip install snap-judgment", 1, FAINT)
    return canvas.to_png()


# ---------------------------------------------------------------------------
# exhibit 2: the awards grid
# ---------------------------------------------------------------------------

def awards_grid_png():
    canvas = framed(1200, 1000)
    canvas.text(600, 46, "THE AWARDS", 6, GOLD)
    canvas.text(600, 108, "SIXTEEN HONORS THE COURT MAY BESTOW", 2, MUTED)
    for i, (name, desc) in enumerate(AWARDS):
        row, col = divmod(i, 4)
        x0 = 48 + col * 276 + 6
        y0 = 148 + row * 192
        x1, y1 = x0 + 264, y0 + 176
        canvas.border(x0, y0, x1, y1, DIVIDER, 2)
        cx = (x0 + x1) // 2
        canvas.text(cx, y0 + 18, "*", 2, GOLD)
        canvas.text(cx, y0 + 48, name, sj._fit_scale(name, 240, 3), GOLD)
        line_y = y0 + 94
        for line in wrap_words(desc, 20):
            canvas.text(cx, line_y, line, 2, MUTED)
            line_y += 24
    canvas.text(600, 940,
                "Earned from file metadata only - no pixels were read.",
                1, FAINT)
    return canvas.to_png()


# ---------------------------------------------------------------------------
# exhibit 3: the plea bargain, before and after
# ---------------------------------------------------------------------------

_ARROW = ("#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#....")


def _draw_arrow(canvas, x, y, scale, color):
    for row_i, row in enumerate(_ARROW):
        for col_i, mark in enumerate(row):
            if mark == "#":
                canvas.rect(x + col_i * scale, y + row_i * scale,
                            x + (col_i + 1) * scale, y + (row_i + 1) * scale,
                            color)


def plea_png(stats, tier, plea):
    canvas = sj._Canvas(1200, 630, BG)
    canvas.rect(24, 24, 580, 470, PANEL)
    canvas.border(24, 24, 580, 470, ACCENT, 3)
    canvas.rect(620, 24, 1176, 470, PANEL)
    canvas.border(620, 24, 1176, 470, GOLD, 3)
    _draw_arrow(canvas, 588, 232, 5, GOLD)

    tier_color = TIER_COLORS[tier]
    projected_color = TIER_COLORS[plea["projected_tier"]]
    span = f"{stats['first_date']:%b %Y} - {stats['last_date']:%b %Y}".upper()

    canvas.text(302, 52, "BEFORE", 5, GOLD)
    canvas.text(302, 128, f"{stats['count']} SCREENSHOTS", 4, PAPER)
    canvas.text(302, 190, tier.upper(),
                sj._fit_scale(tier.upper(), 500, 4), tier_color)
    canvas.rect(122, 256, 482, 258, DIVIDER)
    canvas.text(302, 292, f"{stats['total_human']} OF EVIDENCE", 2, MUTED)
    canvas.text(302, 334,
                f"NIGHT SHARE {stats['night_share']:.1f}%", 2, MUTED)
    canvas.text(302, 376, span, 2, MUTED)

    canvas.text(898, 52, "THE PLEA", 5, GOLD)
    canvas.text(898, 128, f"{plea['remaining_count']} SCREENSHOTS", 4, PAPER)
    canvas.text(898, 190, plea["projected_tier"].upper(),
                sj._fit_scale(plea["projected_tier"].upper(), 500, 4),
                projected_color)
    canvas.rect(718, 256, 1078, 258, DIVIDER)
    canvas.text(898, 292, f"{plea['removed_human']} RECLAIMED", 2, MUTED)
    canvas.text(898, 334, f"{plea['removed_count']} FILES REMOVED", 2, MUTED)
    if plea["projected_tier"] == tier:
        note = "VERDICT UNCHANGED"
    else:
        note = "VERDICT IMPROVED"
    canvas.text(898, 376, note, 2, projected_color)

    closing = sj.plea_closing(plea, tier)
    canvas.text(600, 510, closing, sj._fit_scale(closing, 1100, 2), SENTENCE)
    canvas.text(600, 552,
                "A purely hypothetical exercise - this court deletes "
                "nothing.", 1, FAINT)
    canvas.text(600, 584, "pip install snap-judgment", 1, FAINT)
    return canvas.to_png()


# ---------------------------------------------------------------------------
# exhibit 4: the heatmap
# ---------------------------------------------------------------------------

def _blend(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def heatmap_png(stats):
    canvas = framed(1200, 700)
    canvas.text(600, 42, "WHEN THE DEFENDANT STRIKES", 5, GOLD)
    heat = stats["heatmap"]
    max_v = max(max(row) for row in heat)
    day_labels = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    day_names = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY",
                 "FRIDAY", "SATURDAY", "SUNDAY"]
    x0, y0, pitch_x, cell_w = 124, 120, 42, 39
    pitch_y, cell_h = 62, 54
    peak = (0, 0, -1)
    for r in range(7):
        label = day_labels[r]
        lx = 112 - sj._text_width(label, 2)
        draw_text_left(canvas, lx, y0 + r * pitch_y + 20, label, 2, MUTED)
        for c in range(24):
            v = heat[r][c]
            if v > peak[2]:
                peak = (r, c, v)
            color = HEAT_BASE if v == 0 else _blend(
                HEAT_BASE, GOLD, 0.12 + 0.88 * (v / max_v))
            cx = x0 + c * pitch_x
            cy = y0 + r * pitch_y
            canvas.rect(cx, cy, cx + cell_w, cy + cell_h, color)
    hour_labels = {0: "12A", 3: "3A", 6: "6A", 9: "9A",
                   12: "12P", 15: "3P", 18: "6P", 21: "9P"}
    for hour, label in hour_labels.items():
        cx = x0 + hour * pitch_x + cell_w // 2
        canvas.text(cx, 560, label, 1, FAINT)
    r, c, v = peak
    peak_line = (f"PEAK: {v} SCREENSHOTS - {day_names[r]} AT "
                 f"{sj._fmt_hour(c).upper()}")
    canvas.text(600, 602, peak_line, 2, MUTED)
    canvas.text(600, 644, "pip install snap-judgment", 1, FAINT)
    return canvas.to_png()


# ---------------------------------------------------------------------------
# GIF89a encoding (LZW, standard library only) — the encoder pattern
# from tools/make_demo_gif.py, generalized for size and palette
# ---------------------------------------------------------------------------

class _BitWriter:
    def __init__(self):
        self.buf = bytearray()
        self.acc = 0
        self.bits = 0

    def write(self, code, size):
        self.acc |= code << self.bits
        self.bits += size
        while self.bits >= 8:
            self.buf.append(self.acc & 0xFF)
            self.acc >>= 8
            self.bits -= 8

    def finish(self):
        if self.bits:
            self.buf.append(self.acc & 0xFF)
        return bytes(self.buf)


def _lzw_encode(indices, min_code_size=8):
    """GIF LZW compression. The code-size rule (grow when the next code
    to assign passes the current size's limit) is the one validated
    against independent decoders; do not 'tidy' it."""
    clear = 1 << min_code_size
    eoi = clear + 1
    writer = _BitWriter()
    size = min_code_size + 1
    next_code = eoi + 1
    table = {}
    writer.write(clear, size)
    prefix = indices[0]
    for k in indices[1:]:
        key = (prefix, k)
        if key in table:
            prefix = table[key]
            continue
        writer.write(prefix, size)
        table[key] = next_code
        next_code += 1
        if next_code > (1 << size) and size < 12:
            size += 1
        if next_code > 4096:
            writer.write(clear, size)
            table = {}
            size = min_code_size + 1
            next_code = eoi + 1
        prefix = k
    writer.write(prefix, size)
    writer.write(eoi, size)
    return writer.finish()


def write_gif(path, frames, width, height, palette):
    """frames: list of (index_bytes, delay_centiseconds)."""
    gct = bytearray()
    for i in range(256):
        gct += bytes(palette[i]) if i < len(palette) else b"\x00\x00\x00"
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", width, height, 0xF7, 0, 0)
    out += bytes(gct)
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    for indices, delay in frames:
        out += b"\x21\xf9\x04\x00" + struct.pack("<H", delay) + b"\x00\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0)
        data = _lzw_encode(indices)
        out.append(8)  # minimum code size
        for i in range(0, len(data), 255):
            block = data[i:i + 255]
            out.append(len(block))
            out += block
        out.append(0)
    out.append(0x3B)
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def count_gif_frames(path):
    """Walk the block structure and count image descriptors."""
    with open(path, "rb") as fh:
        blob = fh.read()
    assert blob[:6] == b"GIF89a", "bad GIF magic"
    width, height = struct.unpack("<HH", blob[6:10])
    pos = 13
    if blob[10] & 0x80:  # global color table present
        pos += 3 * (2 ** ((blob[10] & 0x07) + 1))
    count = 0
    while pos < len(blob):
        marker = blob[pos]
        if marker == 0x3B:
            break
        if marker == 0x21:  # extension: label + sub-blocks
            pos += 2
            while blob[pos]:
                pos += 1 + blob[pos]
            pos += 1
        elif marker == 0x2C:  # image descriptor
            count += 1
            packed = blob[pos + 9]
            pos += 10
            if packed & 0x80:  # local color table
                pos += 3 * (2 ** ((packed & 0x07) + 1))
            pos += 1  # minimum code size byte
            while blob[pos]:
                pos += 1 + blob[pos]
            pos += 1
        else:
            raise AssertionError(f"unexpected block {marker:#x} at {pos}")
    return count, (width, height), len(blob)


# ---------------------------------------------------------------------------
# exhibit 5: the verdict reveal GIF — the share card, assembling itself
# ---------------------------------------------------------------------------

GIF_W, GIF_H = 1000, 560
GIF_PALETTE = [BG, PANEL, ACCENT, GOLD, PAPER, MUTED, FAINT, DIVIDER,
               SENTENCE, (223, 179, 23)]
GIF_INDEX = {color: i for i, color in enumerate(GIF_PALETTE)}


def _card_canvas(stats, tier, sentence, awards, stage,
                 verdict_scale=6, verdict_y=160, verdict_color=GOLD):
    """The 1200x630 share card's layout, redrawn at 1000x560 and built
    up in stages. Stage 9 adds the verdict itself (the stamp)."""
    canvas = sj._Canvas(GIF_W, GIF_H, BG)
    canvas.rect(20, 20, GIF_W - 20, GIF_H - 20, PANEL)
    if stage >= 1:
        canvas.border(20, 20, GIF_W - 20, GIF_H - 20, ACCENT, 3)
    if stage >= 2:
        canvas.text(500, 50, "THE COURT OF SCREENSHOT AFFAIRS", 2, ACCENT)
    if stage >= 3:
        canvas.text(500, 86, "SNAP JUDGMENT", 7, PAPER)
    if stage >= 9:
        canvas.text(500, verdict_y, tier, verdict_scale, verdict_color)
    if stage >= 4:
        canvas.rect(170, 224, 830, 226, DIVIDER)
    if stage >= 5:
        span = (f"{stats['first_date']:%b %Y} - "
                f"{stats['last_date']:%b %Y}")
        line = (f"{stats['count']:,} screenshots - "
                f"{stats['total_human']} - {span}")
        canvas.text(500, 248, line, sj._fit_scale(line, 920, 3), PAPER)
    if stage >= 6:
        canvas.text(500, 302, f"Night share {stats['night_share']:.1f}%",
                    3, MUTED)
    if stage >= 7:
        if awards:
            line = "Awards: " + ", ".join(a["name"] for a in awards[:3])
        else:
            line = "Awards: none"
        canvas.text(500, 352, line, sj._fit_scale(line, 920, 2), PAPER)
    if stage >= 8:
        if sentence:
            canvas.text(500, 404, sentence,
                        sj._fit_scale(sentence, 920, 2), SENTENCE)
        canvas.text(500, 492,
                    "Judged from file metadata only. No pixels were read.",
                    2, FAINT)
        canvas.text(500, 520, "pip install snap-judgment", 1, FAINT)
    return canvas


def _to_indices(canvas):
    pixels = canvas.pixels
    return bytes(
        GIF_INDEX[(pixels[i], pixels[i + 1], pixels[i + 2])]
        for i in range(0, len(pixels), 3)
    )


def verdict_reveal_gif(path, stats, tier, sentence, awards):
    stamp_color = TIER_COLORS[tier]
    plan = [
        # (stage, verdict scale, verdict y, verdict color, delay cs)
        (0, 6, 160, GOLD, 30),      # the empty bench
        (1, 6, 160, GOLD, 25),      # border
        (2, 6, 160, GOLD, 25),      # the court convenes
        (3, 6, 160, GOLD, 40),      # SNAP JUDGMENT
        (4, 6, 160, GOLD, 20),      # divider
        (5, 6, 160, GOLD, 30),      # the count
        (6, 6, 160, GOLD, 30),      # night share
        (7, 6, 160, GOLD, 30),      # awards
        (8, 6, 160, GOLD, 45),      # sentence and footers
        (9, 9, 146, stamp_color, 16),   # the stamp lands, oversized
        (9, 8, 150, stamp_color, 16),   # ... and settles
        (9, 6, 160, GOLD, 25),      # verdict in place
        (9, 6, 160, GOLD, 60),
        (9, 6, 160, GOLD, 120),
        (9, 6, 160, GOLD, 220),     # hold on the verdict
    ]
    frames = []
    for stage, scale, y, color, delay in plan:
        canvas = _card_canvas(stats, tier, sentence, awards, stage,
                              verdict_scale=scale, verdict_y=y,
                              verdict_color=color)
        frames.append((_to_indices(canvas), delay))
    write_gif(path, frames, GIF_W, GIF_H, GIF_PALETTE)


# ---------------------------------------------------------------------------
# validation, the MP4, the index, and the build itself
# ---------------------------------------------------------------------------

def png_dimensions(path):
    with open(path, "rb") as fh:
        blob = fh.read(33)
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", f"{path}: bad PNG signature"
    assert blob[12:16] == b"IHDR", f"{path}: first chunk is not IHDR"
    return struct.unpack(">II", blob[16:24]), os.path.getsize(path)


def jpeg_dimensions(path):
    """(width, height) from the first SOF marker of a JPEG stream."""
    with open(path, "rb") as fh:
        blob = fh.read()
    assert blob[:2] == b"\xff\xd8", f"{path}: bad JPEG signature"
    pos = 2
    while pos + 9 < len(blob):
        if blob[pos] != 0xFF:
            pos += 1
            continue
        marker = blob[pos + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", blob[pos + 5:pos + 9])
            return (width, height)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD9:
            pos += 2
            continue
        length = struct.unpack(">H", blob[pos + 2:pos + 4])[0]
        pos += 2 + length
    raise AssertionError(f"{path}: no SOF marker found")


def convert_mp4(gif_path, mp4_path):
    """examples/demo.gif -> MP4 via the system ffmpeg, when present.
    -bitexact keeps the muxer from stamping times into the file, so
    re-runs stay byte-identical like everything else in the pack."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    subprocess.run(
        [ffmpeg, "-y", "-bitexact", "-i", gif_path,
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-vf", "scale=1000:560", mp4_path],
        check=True, capture_output=True)
    return mp4_path


MEDIA_README = """# Media pack

Every picture and video in this folder was drawn by the project's own
pure-stdlib renderer (the 5x7 bitmap font from `snap_judgment.py`) from
the deterministic demo folder's real numbers. No image library, no
mockups, no AI generation. Regenerate the whole pack with:

    python3 tools/make_media.py

| File | What it shows | Use it for |
|---|---|---|
| `verdict-ladder.png` | The six verdict tiers as a ladder, colored by severity, mildest to felon | README hero companion, site evidence locker, social posts explaining the tiers |
| `awards-grid.png` | All sixteen awards in a 4x4 grid, each with a plain-language description | Site evidence locker, Product Hunt gallery, "which ones have you earned?" posts |
| `plea-before-after.png` | The demo folder's plea bargain: 310 screenshots before, 210 after deleting duplicates and anything over two years old, 4.5 MB reclaimed, verdict unchanged | Social posts, Product Hunt gallery, anywhere the plea bargain needs explaining in one glance |
| `heatmap.png` | The demo folder's hour-by-weekday heatmap: when the defendant strikes | Site evidence locker, blog and dev.to illustrations |
| `verdict-reveal.gif` | The share card assembling itself, ending with the verdict stamping in | README, site evidence locker, social posts, Product Hunt gallery (motion beats stills) |
| `demo.mp4` | The terminal demo GIF re-encoded as H.264 MP4 | Video-first platforms (X, LinkedIn, TikTok uploads) that prefer MP4 over GIF |
| `authors-verdict.png` | The author's own real verdict card: 266 screenshots, 164 MB, Repeat Offender, 47.7% at night | Launch post #1, anywhere a real (non-demo) verdict is the honest exhibit |

The courtroom palette throughout: near-black `#101014`, panel `#16161f`,
gold `#d4af37` family, off-white text. The demo defendant's numbers
(310 screenshots, Repeat Offender) are the synthetic demo set, labeled
as such wherever they appear; `authors-verdict.png` is a real folder.
"""


def main():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    stats, tier, sentence, awards, plea = load_demo()

    written = {}

    def emit(name, data):
        path = os.path.join(MEDIA_DIR, name)
        with open(path, "wb") as fh:
            fh.write(data)
        written[name] = path
        return path

    emit("verdict-ladder.png", verdict_ladder_png())
    emit("awards-grid.png", awards_grid_png())
    emit("plea-before-after.png", plea_png(stats, tier, plea))
    emit("heatmap.png", heatmap_png(stats))
    gif_path = os.path.join(MEDIA_DIR, "verdict-reveal.gif")
    verdict_reveal_gif(gif_path, stats, tier, sentence, awards)
    written["verdict-reveal.gif"] = gif_path

    # The author's own real verdict card, shared for exactly this use.
    authors_path = os.path.join(MEDIA_DIR, "authors-verdict.png")
    shutil.copyfile(AUTHORS_CARD, authors_path)
    written["authors-verdict.png"] = authors_path

    mp4 = convert_mp4(os.path.join(ROOT, "examples", "demo.gif"),
                      os.path.join(MEDIA_DIR, "demo.mp4"))
    if mp4:
        written["demo.mp4"] = mp4

    with open(os.path.join(MEDIA_DIR, "README.md"), "w",
              encoding="utf-8") as fh:
        fh.write(MEDIA_README)

    # The site's evidence locker serves these four from docs/assets/.
    for name in ("verdict-ladder.png", "awards-grid.png", "heatmap.png",
                 "verdict-reveal.gif"):
        shutil.copyfile(written[name], os.path.join(ASSETS_DIR, name))

    print(f"demo folder: {stats['count']} screenshots, verdict: {tier}, "
          f"plea: {plea['removed_count']} removed, "
          f"{plea['removed_human']} reclaimed, "
          f"{plea['remaining_count']} remaining")
    for name in sorted(written):
        path = written[name]
        size = os.path.getsize(path)
        if name == "authors-verdict.png":
            # Copied byte for byte, never re-encoded. The chat upload
            # pipeline stored it as JPEG data under a .png name, so
            # verify the copy and read the JPEG's own dimensions.
            with open(AUTHORS_CARD, "rb") as fh:
                original = fh.read()
            with open(path, "rb") as fh:
                copied = fh.read()
            assert copied == original, "authors card copy differs"
            dims = jpeg_dimensions(path)
            print(f"  {name}: {size:,} bytes, {dims[0]}x{dims[1]} "
                  f"(JPEG data, copied unchanged)")
        elif name.endswith(".png"):
            dims, _ = png_dimensions(path)
            print(f"  {name}: {size:,} bytes, {dims[0]}x{dims[1]}")
        elif name.endswith(".gif"):
            frames, dims, _ = count_gif_frames(path)
            assert frames >= 10, "reveal GIF needs at least 10 frames"
            print(f"  {name}: {size:,} bytes, {dims[0]}x{dims[1]}, "
                  f"{frames} frames")
        else:
            print(f"  {name}: {size:,} bytes")
    if not mp4:
        print("  demo.mp4: SKIPPED (ffmpeg not found on PATH)")
    shutil.rmtree(_SCRATCH_HOME, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
