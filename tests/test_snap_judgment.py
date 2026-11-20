"""Tests for snap_judgment. Stdlib unittest only.

All fixtures are synthetic: minimal header-only image files written to
temp folders, with exact mtimes set via os.utime.
"""

import io
import json
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snap_judgment as sj


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------

def make_png(width, height, pad=0):
    """Minimal PNG: signature + IHDR (CRCs zeroed) + IEND + optional pad."""
    ihdr = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
    data = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
        + struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"
    )
    return data + b"\x00" * pad


def make_jpeg(width, height):
    """Minimal JPEG byte string: SOI + APP0 + SOF0 (with dims) + EOI."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof_body = (
        bytes([8]) + struct.pack(">HH", height, width)
        + bytes([3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1])
    )
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof_body) + 2) + sof_body
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def make_gif(width, height):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 16


def make_webp_vp8(width, height):
    body = (
        b"WEBP" + b"VP8 " + struct.pack("<I", 10)
        + b"\x00\x00\x00" + b"\x9d\x01\x2a"
        + struct.pack("<HH", width, height)
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_webp_vp8l(width, height):
    packed = (width - 1) | ((height - 1) << 14)
    body = (
        b"WEBP" + b"VP8L" + struct.pack("<I", 5)
        + b"\x2f" + packed.to_bytes(4, "little") + b"\x00" * 8
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_webp_vp8x(width, height):
    body = (
        b"WEBP" + b"VP8X" + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_bmp(width, height):
    header = (
        b"BM" + struct.pack("<IHHI", 54, 0, 0, 54)
        + struct.pack("<IiiHH", 40, width, height, 1, 24)
        + b"\x00" * 24
    )
    return header


def write_file(folder, name, data, when=None):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if when is not None:
        ts = when.timestamp()
        os.utime(path, (ts, ts))
    return path


def award_names(stats):
    return {a["name"] for a in sj.compute_awards(stats)}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def stats_for(self, files, all_images=False):
        """files: list of (name, data, datetime). Returns (stats, shots)."""
        for name, data, when in files:
            write_file(self.root, name, data, when)
        shots, recordings = sj.collect([self.root], all_images=all_images)
        return sj.analyze(shots, recordings), shots


# ---------------------------------------------------------------------------
# dimension parsers
# ---------------------------------------------------------------------------

class DimensionParserTests(TempDirCase):
    def dims(self, name, data):
        path = write_file(self.root, name, data)
        return sj.read_dimensions(path, path.suffix.lower())

    def test_png_dimensions(self):
        self.assertEqual(self.dims("a.png", make_png(1170, 2532)), (1170, 2532))

    def test_jpeg_dimensions(self):
        self.assertEqual(self.dims("a.jpg", make_jpeg(1920, 1080)), (1920, 1080))

    def test_gif_dimensions(self):
        self.assertEqual(self.dims("a.gif", make_gif(800, 600)), (800, 600))

    def test_webp_vp8_dimensions(self):
        self.assertEqual(self.dims("a.webp", make_webp_vp8(640, 480)), (640, 480))

    def test_webp_vp8l_dimensions(self):
        self.assertEqual(self.dims("a.webp", make_webp_vp8l(300, 200)), (300, 200))

    def test_webp_vp8x_dimensions(self):
        self.assertEqual(self.dims("a.webp", make_webp_vp8x(1024, 768)), (1024, 768))

    def test_bmp_dimensions(self):
        self.assertEqual(self.dims("a.bmp", make_bmp(1440, 900)), (1440, 900))

    def test_heic_dimensions_not_attempted(self):
        self.assertIsNone(self.dims("a.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32))

    def test_corrupt_png_returns_none(self):
        self.assertIsNone(self.dims("a.png", b"this is not a png at all........"))

    def test_truncated_jpeg_returns_none(self):
        self.assertIsNone(self.dims("a.jpg", b"\xff\xd8\xff"))


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

class DetectionTests(TempDirCase):
    def collect_names(self, all_images=False):
        shots, recordings = sj.collect([self.root], all_images=all_images)
        return [s.name for s in shots], [r.name for r in recordings]

    def test_android_name(self):
        write_file(self.root, "Screenshot_20260101_120000.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(shots, ["Screenshot_20260101_120000.png"])

    def test_macos_name(self):
        write_file(self.root, "Screenshot 2026-01-01 at 10.00.00.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(len(shots), 1)

    def test_windows_numbered_name(self):
        write_file(self.root, "Screenshot (3).png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(len(shots), 1)

    def test_cleanshot_name(self):
        write_file(self.root, "CleanShot 2026-01-01 at 09.15.00.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(len(shots), 1)

    def test_bildschirmfoto_name(self):
        write_file(self.root, "Bildschirmfoto 2026-01-01 um 09.15.00.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(len(shots), 1)

    def test_folder_name_rule(self):
        write_file(self.root / "My Screenshots", "IMG_1234.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(shots, ["IMG_1234.png"])

    def test_recursion_into_subfolders(self):
        write_file(self.root / "deep" / "deeper", "screenshot_nested.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(len(shots), 1)

    def test_plain_image_excluded(self):
        write_file(self.root, "vacation.jpg", make_jpeg(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(shots, [])

    def test_all_flag_includes_plain_images(self):
        write_file(self.root, "vacation.jpg", make_jpeg(10, 10))
        shots, _ = self.collect_names(all_images=True)
        self.assertEqual(shots, ["vacation.jpg"])

    def test_recording_in_screenshots_folder(self):
        write_file(self.root / "Screenshots", "clip.mp4", b"\x00" * 64)
        shots, recordings = self.collect_names()
        self.assertEqual(shots, [])
        self.assertEqual(recordings, ["clip.mp4"])

    def test_recording_by_name(self):
        write_file(self.root, "Screen Recording 2026-01-01.mov", b"\x00" * 64)
        shots, recordings = self.collect_names()
        self.assertEqual(len(recordings), 1)

    def test_unrelated_video_excluded(self):
        write_file(self.root, "birthday.mp4", b"\x00" * 64)
        shots, recordings = self.collect_names()
        self.assertEqual(recordings, [])


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

class StatsTests(TempDirCase):
    def test_night_share(self):
        files = []
        for i in range(4):
            files.append((f"screenshot_n{i}.png", make_png(10, 10),
                          datetime(2026, 3, 1 + i, 23, 30)))
        for i in range(4):
            files.append((f"screenshot_d{i}.png", make_png(10, 10),
                          datetime(2026, 3, 10 + i, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["night_count"], 4)
        self.assertEqual(stats["night_share"], 50.0)

    def test_longest_streak(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10), datetime(2026, 4, 1 + i, 12, 0))
            for i in range(7)
        ]
        files.append(("screenshot_gap.png", make_png(10, 10), datetime(2026, 4, 20, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["longest_streak"], 7)

    def test_burst_window(self):
        base = datetime(2026, 5, 1, 12, 0, 0)
        files = [
            (f"screenshot_b{i}.png", make_png(10, 10), base + timedelta(seconds=off))
            for i, off in enumerate([0, 10, 20, 30, 50, 61])
        ]
        files.append(("screenshot_far.png", make_png(10, 10), datetime(2026, 5, 2, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["burst_record"], 5)

    def test_duplicate_groups(self):
        files = []
        for i in range(3):
            files.append((f"screenshot_a{i}.png", make_png(100, 100),
                          datetime(2026, 1, 1 + i, 12, 0)))
        for i in range(2):
            files.append((f"screenshot_b{i}.png", make_png(200, 200, pad=50),
                          datetime(2026, 2, 1 + i, 12, 0)))
        files.append(("screenshot_unique.png", make_png(300, 300),
                      datetime(2026, 3, 1, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["duplicate_groups"], 2)
        self.assertEqual(stats["duplicate_files"], 5)

    def test_per_year_counts(self):
        files = [
            ("screenshot_1.png", make_png(10, 10), datetime(2024, 6, 1, 12, 0)),
            ("screenshot_2.png", make_png(10, 10), datetime(2024, 7, 1, 12, 0)),
            ("screenshot_3.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_4.png", make_png(10, 10), datetime(2026, 2, 1, 12, 0)),
            ("screenshot_5.png", make_png(10, 10), datetime(2026, 3, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["per_year"], {2024: 2, 2026: 3})

    def test_busiest_day_and_hour(self):
        files = [
            ("screenshot_1.png", make_png(10, 10), datetime(2026, 6, 1, 15, 0)),
            ("screenshot_2.png", make_png(10, 10), datetime(2026, 6, 1, 15, 30)),
            ("screenshot_3.png", make_png(10, 10), datetime(2026, 6, 1, 9, 0)),
            ("screenshot_4.png", make_png(10, 10), datetime(2026, 6, 2, 15, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(str(stats["busiest_day"]), "2026-06-01")
        self.assertEqual(stats["busiest_day_count"], 3)
        self.assertEqual(stats["busiest_hour"], 15)
        self.assertEqual(stats["busiest_hour_count"], 3)

    def test_device_eras(self):
        files = [
            ("screenshot_1.png", make_png(100, 200), datetime(2025, 1, 10, 12, 0)),
            ("screenshot_2.png", make_png(100, 200), datetime(2025, 2, 10, 12, 0)),
            ("screenshot_3.png", make_png(300, 400), datetime(2025, 3, 10, 12, 0)),
            ("screenshot_4.png", make_png(300, 400), datetime(2025, 4, 10, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        eras = stats["device_eras"]
        self.assertEqual(len(eras), 2)
        self.assertEqual(eras[0]["dims"], (100, 200))
        self.assertEqual(eras[0]["count"], 2)
        self.assertEqual(str(eras[0]["first"]), "2025-01-10")
        self.assertEqual(str(eras[0]["last"]), "2025-02-10")
        self.assertEqual(eras[1]["dims"], (300, 400))

    def test_oldest_and_span(self):
        files = [
            ("screenshot_old.png", make_png(10, 10), datetime(2024, 1, 15, 12, 0)),
            ("screenshot_new.png", make_png(10, 10), datetime(2026, 1, 15, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["oldest_name"], "screenshot_old.png")
        self.assertEqual(str(stats["oldest_date"]), "2024-01-15")
        self.assertEqual(stats["span_days"], 731)


# ---------------------------------------------------------------------------
# verdict tiers
# ---------------------------------------------------------------------------

class VerdictTierTests(unittest.TestCase):
    def test_no_evidence(self):
        self.assertEqual(sj.verdict_for(0)[0], "No Evidence")

    def test_innocent_bystander_boundaries(self):
        for count in (1, 25, 49):
            with self.subTest(count=count):
                self.assertEqual(sj.verdict_for(count)[0], "Innocent Bystander")

    def test_petty_offender_boundaries(self):
        for count in (50, 100, 199):
            with self.subTest(count=count):
                self.assertEqual(sj.verdict_for(count)[0], "Petty Offender")

    def test_repeat_offender_boundaries(self):
        for count in (200, 350, 499):
            with self.subTest(count=count):
                self.assertEqual(sj.verdict_for(count)[0], "Repeat Offender")

    def test_habitual_hoarder_boundaries(self):
        for count in (500, 750, 999):
            with self.subTest(count=count):
                self.assertEqual(sj.verdict_for(count)[0], "Habitual Hoarder")

    def test_felony_archivist_boundaries(self):
        for count in (1000, 2000, 2499):
            with self.subTest(count=count):
                self.assertEqual(sj.verdict_for(count)[0], "Felony-Level Archivist")

    def test_unrepentant_felon_boundaries(self):
        for count in (2500, 10000):
            with self.subTest(count=count):
                self.assertEqual(sj.verdict_for(count)[0], "Unrepentant Screenshot Felon")


# ---------------------------------------------------------------------------
# awards
# ---------------------------------------------------------------------------

class AwardTests(TempDirCase):
    def test_night_owl(self):
        files = []
        for i in range(20):
            files.append((f"screenshot_n{i}.png", make_png(10, 10),
                          datetime(2026, 1, 1 + (i % 20), 23, 0) if i < 20 else None))
        files = files[:20]
        for i in range(60):
            files.append((f"screenshot_d{i}.png", make_png(10, 10),
                          datetime(2026, 2, 1 + (i % 28), 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertIn("Night Owl", award_names(stats))

    def test_night_owl_not_earned_below_share(self):
        files = []
        for i in range(20):
            files.append((f"screenshot_n{i}.png", make_png(10, 10),
                          datetime(2026, 1, 1 + (i % 20), 23, 0)))
        for i in range(100):
            files.append((f"screenshot_d{i}.png", make_png(10, 10),
                          datetime(2026, 3, 1 + (i % 28), 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertNotIn("Night Owl", award_names(stats))

    def test_burst_mode(self):
        base = datetime(2026, 5, 1, 12, 0, 0)
        files = [
            (f"screenshot_b{i}.png", make_png(10, 10), base + timedelta(seconds=i * 5))
            for i in range(8)
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Burst Mode", award_names(stats))

    def test_archivist(self):
        old = datetime.now() - timedelta(days=1500)
        files = [
            ("screenshot_old.png", make_png(10, 10), old),
            ("screenshot_new.png", make_png(10, 10), datetime.now()),
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("The Archivist", award_names(stats))

    def test_receipt_keeper(self):
        files = []
        day = datetime(2026, 1, 5, 10, 0)  # a Monday
        made = 0
        while made < 50:
            if day.weekday() < 5:
                files.append((f"screenshot_w{made}.png", make_png(10, 10), day))
                made += 1
            day += timedelta(days=1)
        stats, _ = self.stats_for(files)
        self.assertIn("Receipt Keeper", award_names(stats))

    def test_repeat_customer(self):
        files = [
            (f"screenshot_dup{i}.png", make_png(100, 100), datetime(2026, 1, 1 + i, 12, 0))
            for i in range(10)
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Repeat Customer", award_names(stats))

    def test_time_traveler(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2021, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Time Traveler", award_names(stats))

    def test_screenshot_streak_award(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10), datetime(2026, 4, 1 + i, 12, 0))
            for i in range(7)
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Screenshot Streak", award_names(stats))

    def test_minimalist(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10), datetime(2026, 4, 1 + i, 12, 0))
            for i in range(5)
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Minimalist", award_names(stats))


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------

class CliTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3, when=None):
        when = when or datetime(2026, 6, 1, 12, 0)
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       when + timedelta(days=i))

    def test_terminal_report(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("THE VERDICT", out)
        self.assertIn("Innocent Bystander", out)
        self.assertIn("THE EVIDENCE", out)
        self.assertIn("THE SENTENCE", out)

    def test_json_shape(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        for key in ("verdict", "sentence", "count", "recordings", "total_bytes",
                    "first_date", "last_date", "span_days", "oldest", "per_year",
                    "busiest_day", "busiest_hour", "night_count", "night_share_pct",
                    "longest_streak_days", "burst_record", "duplicate_groups",
                    "duplicate_files", "device_eras", "awards"):
            self.assertIn(key, data)
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["verdict"], "Innocent Bystander")
        self.assertEqual(data["per_year"], {"2026": 3})
        self.assertEqual(data["device_eras"][0]["width"], 100)

    def test_card_contains_verdict_and_count(self):
        self.seed()
        card = self.root / "card.html"
        code, _, _ = self.run_main([str(self.root), "--card", str(card)])
        self.assertEqual(code, 0)
        text = card.read_text(encoding="utf-8")
        self.assertIn("Innocent Bystander", text)
        self.assertIn(">3<", text)
        self.assertIn("Snap Judgment", text)

    def test_markdown_out(self):
        self.seed()
        md = self.root / "report.md"
        code, _, _ = self.run_main([str(self.root), "--out", str(md)])
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## The Verdict", text)
        self.assertIn("Innocent Bystander", text)

    def test_since_filter(self):
        write_file(self.root, "screenshot_old.png", make_png(10, 10),
                   datetime(2024, 5, 1, 12, 0))
        write_file(self.root, "screenshot_new.png", make_png(10, 10),
                   datetime(2026, 5, 1, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--since", "2025", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 1)

    def test_empty_folder_exits_2(self):
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("No Evidence", out)

    def test_missing_folder_exits_2(self):
        code, _, err = self.run_main([str(self.root / "does-not-exist")])
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)


if __name__ == "__main__":
    unittest.main()
