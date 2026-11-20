#!/usr/bin/env python3
"""Build the README demo GIF: a terminal session rendered from the tool's
REAL output (no hand-typed fake verdicts). Usage:

    python3 tools/make_demo_gif.py [out.gif]

A small synthetic screenshot folder is generated from a fixed seed and
judged in-process; the report lines are excerpted for the terminal the
same way a person would scroll them. Frames are drawn with the same 5x7
bitmap font as the PNG share card (imported from snap_judgment, with a
few extra glyphs in the same style for shell characters the card never
needs), and encoded as GIF89a by the pure-stdlib LZW encoder below.

Standard library only, like everything else in this repo.
"""
import os
import time

os.environ["TZ"] = "UTC"
time.tzset()

import random
import shutil
import struct
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_SCRATCH_HOME = tempfile.mkdtemp(prefix="snap-gif-home-")
os.environ["HOME"] = _SCRATCH_HOME

import snap_judgment as sj  # noqa: E402

W, H = 1000, 560
FIXED_NOW = datetime(2026, 11, 19, 12, 0, 0)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 19, 12, 0, 0)


sj.datetime = _FixedDateTime

# ---------------------------------------------------------------------------
# palette and drawing
# ---------------------------------------------------------------------------

BG = (10, 10, 14)
PANEL = (22, 22, 31)
PAPER = (232, 227, 216)
GOLD = (212, 175, 90)
ACCENT = (176, 141, 60)
MUTED = (154, 148, 166)
FAINT = (109, 104, 120)
GREEN = (96, 196, 116)
DOT_RED = (255, 95, 86)
DOT_YELLOW = (255, 189, 46)
DOT_GREEN = (39, 201, 63)

PALETTE = [BG, PAPER, GOLD, ACCENT, MUTED, FAINT, GREEN,
           DOT_RED, DOT_YELLOW, DOT_GREEN, PANEL]
PALETTE_INDEX = {color: i for i, color in enumerate(PALETTE)}

# Extra glyphs in the same 5x7 string-art style, for shell characters the
# share card's font never needs. snap_judgment._glyph covers the rest.
_EXTRA_GLYPHS = {
    "$": ("..#..", ".####", "#.#..", ".###.", "..#.#", "####.", "..#.."),
    "~": (".....", ".....", ".#..#", "#.#.#", ".....", ".....", "....."),
    "*": (".....", "#.#.#", ".###.", "#####", ".###.", "#.#.#", "....."),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    ">": ("#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."),
    "#": (".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."),
    "[": ("..##.", "..#..", "..#..", "..#..", "..#..", "..#..", "..##."),
    "]": ("..##.", "...#.", "...#.", "...#.", "...#.", "...#.", "..##."),
    "@": (".###.", "#...#", "#.###", "#.#.#", "#.###", "#....", ".###."),
}


def _glyph(ch):
    if ch in _EXTRA_GLYPHS:
        return _EXTRA_GLYPHS[ch]
    return sj._glyph(ch)


def draw_text_left(canvas, x, y, text, scale, color):
    for ch in text:
        glyph = _glyph(ch)
        for row_i, row in enumerate(glyph):
            for col_i, mark in enumerate(row):
                if mark == "#":
                    canvas.rect(
                        x + col_i * scale, y + row_i * scale,
                        x + (col_i + 1) * scale, y + (row_i + 1) * scale,
                        color,
                    )
        x += 6 * scale


SCALE = 2
LINE_H = 19
PAD_X = 28
TOP = 54


def render_frame(lines):
    """lines: list of (text, color). Returns palette-index bytes."""
    canvas = sj._Canvas(W, H, BG)
    canvas.rect(0, 0, W, 36, PANEL)
    for i, dot in enumerate((DOT_RED, DOT_YELLOW, DOT_GREEN)):
        canvas.rect(16 + i * 20, 13, 26 + i * 20, 23, dot)
    title = "snapjudgment"
    canvas.text(W // 2, 12, title, 1, FAINT)
    y = TOP
    for text, color in lines:
        draw_text_left(canvas, PAD_X, y, text, SCALE, color)
        y += LINE_H
    pixels = canvas.pixels
    return bytes(
        PALETTE_INDEX[(pixels[i], pixels[i + 1], pixels[i + 2])]
        for i in range(0, len(pixels), 3)
    )


# ---------------------------------------------------------------------------
# GIF89a encoding (LZW, standard library only)
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


def write_gif(path, frames):
    """frames: list of (index_bytes, delay_centiseconds)."""
    palette = bytearray()
    for i in range(256):
        palette += bytes(PALETTE[i]) if i < len(PALETTE) else b"\x00\x00\x00"
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", W, H, 0xF7, 0, 0)
    out += bytes(palette)
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    for indices, delay in frames:
        out += b"\x21\xf9\x04\x00" + struct.pack("<H", delay) + b"\x00\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, W, H, 0)
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


def validate_gif(path):
    """Read the file back: magic, logical size, frame count."""
    with open(path, "rb") as fh:
        blob = fh.read()
    assert blob[:6] == b"GIF89a", "bad magic"
    width, height = struct.unpack("<HH", blob[6:10])
    assert (width, height) == (W, H), "bad logical screen size"
    frames = blob.count(b"\x2c")  # rough count, refined below
    # Count image descriptors properly by walking the block structure.
    pos = 13 + 256 * 3
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
            pos += 10
            pos += 1  # minimum code size byte
            while blob[pos]:
                pos += 1 + blob[pos]
            pos += 1
        else:
            raise AssertionError(f"unexpected block {marker:#x} at {pos}")
    assert count >= 2, "need at least 2 frames"
    return count, len(blob)


# ---------------------------------------------------------------------------
# the demo itself
# ---------------------------------------------------------------------------

def build_demo_folder(folder):
    """A seeded synthetic collection rich enough to earn a real verdict."""
    import struct as _struct
    import zlib as _zlib

    def chunk(tag, data):
        return (
            _struct.pack(">I", len(data)) + tag + data
            + _struct.pack(">I", _zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    def png_header(w, h):
        ihdr = _struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")

    rng = random.Random(20261119)
    os.makedirs(folder, exist_ok=True)
    used = set()
    records = []
    start = datetime(2024, 3, 1)
    for i in range(236):
        day = start + timedelta(days=rng.randrange(0, 990))
        if rng.random() < 0.30:
            hour = rng.choice([22, 23, 0, 1, 2, 3])
        else:
            hour = rng.randrange(6, 22)
        dt = day.replace(hour=hour, minute=rng.randrange(60),
                         second=rng.randrange(60))
        dims = (1170, 2532) if dt.year < 2026 else (1920, 1080)
        records.append((dt, dims, rng.randrange(300_000, 2_800_000)))
    base = datetime(2026, 8, 14, 21, 3, 0)  # one burst of 12 in a minute
    for i in range(12):
        records.append((base + timedelta(seconds=4 * i), (1170, 2532),
                        rng.randrange(800_000, 1_400_000)))
    dup = records[10]  # a few exact duplicates
    for j in range(11):
        dt, dims, size = dup
        records.append((dt + timedelta(days=30 * (j + 1)), dims, size))
    import calendar as _calendar
    for dt, dims, size in records:
        name = "Screenshot {:%Y-%m-%d} at {:%H.%M.%S}.png".format(dt, dt)
        while name in used:
            dt = dt + timedelta(seconds=1)
            name = "Screenshot {:%Y-%m-%d} at {:%H.%M.%S}.png".format(dt, dt)
        used.add(name)
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(png_header(*dims))
            fh.truncate(size)
        epoch = _calendar.timegm(dt.timetuple())
        os.utime(path, (epoch, epoch))


_EVIDENCE_KEEP = (
    "Total screenshots", "Total size", "Date span", "Busiest day",
    "Taken at night", "Longest daily streak", "Biggest burst",
    "Duplicate suspects",
)


def excerpt(report_text):
    """Trim the real report to what fits the terminal: the verdict, the
    headline evidence, the awards, and the sentence."""
    lines = report_text.splitlines()
    picked = []
    section = None
    for line in lines:
        stripped = line.strip()
        if stripped in ("THE VERDICT", "THE EVIDENCE", "THE CHARGES",
                        "EXHIBITS", "AWARDS", "THE SENTENCE"):
            section = stripped
            picked.append(line)
            continue
        if section == "THE EVIDENCE" and stripped:
            label = stripped.split("  ")[0]
            if any(stripped.startswith(k) for k in _EVIDENCE_KEEP):
                picked.append(line)
            continue
        if section in ("THE CHARGES", "EXHIBITS"):
            continue
        picked.append(line)
    while picked and not picked[-1].strip():
        picked.pop()
    return picked


def style(line, tier):
    stripped = line.strip()
    if line.startswith("$"):
        return line, GREEN
    if stripped.startswith("=") or "SNAP JUDGMENT -" in line:
        return line, ACCENT
    if stripped in ("THE VERDICT", "THE EVIDENCE", "AWARDS", "THE SENTENCE"):
        return line, GOLD
    if stripped == tier:
        return line, GOLD
    if stripped.startswith("Sentence:"):
        return line, MUTED
    if stripped.startswith("*"):
        return line, PAPER
    return line, PAPER


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "examples", "demo.gif")
    tmp_root = tempfile.mkdtemp(prefix="snap-gif-")
    try:
        folder = os.path.join(tmp_root, "demo-shots")
        build_demo_folder(folder)
        skipped = [0]
        shots, recordings = sj.collect([folder], skipped=skipped)
        stats = sj.analyze(shots, recordings)
        stats["skipped"] = skipped[0]
        tier, sentence = sj.verdict_for(stats["count"])
        awards = sj.compute_awards(stats, now=FIXED_NOW)
        report = sj.render_report(stats, tier, sentence, awards)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        shutil.rmtree(_SCRATCH_HOME, ignore_errors=True)

    body = [style(ln, tier) for ln in excerpt(report)]
    cmd = "$ snapjudgment ~/Pictures/Screenshots"

    frames = []

    def push(lines, delay):
        frames.append((render_frame(lines), delay))

    for i in range(4, len(cmd) + 1, 7):
        push([(cmd[:i], GREEN)], 9)
    push([(cmd, GREEN)], 60)
    shown = [(cmd, GREEN), ("", PAPER)]
    push(shown, 35)
    step = 3
    for i in range(0, len(body), step):
        shown = [(cmd, GREEN), ("", PAPER)] + body[:i + step]
        push(shown, 22)
    push(shown, 280)

    write_gif(out_path, frames)
    count, size = validate_gif(out_path)
    print(f"wrote {out_path} ({size / 1024:.0f} KB, {count} frames, "
          f"{W}x{H}, verdict: {tier}, {stats['count']} screenshots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
