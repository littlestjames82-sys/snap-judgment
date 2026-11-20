"""Tests for snap_judgment. Stdlib unittest only.

All fixtures are synthetic: minimal header-only image files written to
temp folders, with exact mtimes set via os.utime.
"""

import csv
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
from unittest import mock

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

    def test_gnome_name(self):
        write_file(self.root, "Screenshot from 2026-01-01 12-00-00.png", make_png(10, 10))
        shots, _ = self.collect_names()
        self.assertEqual(len(shots), 1)

    def test_onedrive_candidate_follows_pictures_screenshots(self):
        candidates = sj.default_candidates()
        home = Path.home()
        self.assertEqual(candidates[0], home / "Pictures" / "Screenshots")
        self.assertEqual(
            candidates[1], home / "OneDrive" / "Pictures" / "Screenshots"
        )

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

    def test_duplicate_bytes_wasted(self):
        data_a = make_png(100, 100)
        data_b = make_png(200, 200, pad=50)
        files = [
            (f"screenshot_a{i}.png", data_a, datetime(2026, 1, 1 + i, 12, 0))
            for i in range(3)
        ]
        files += [
            (f"screenshot_b{i}.png", data_b, datetime(2026, 2, 1 + i, 12, 0))
            for i in range(2)
        ]
        files.append(("screenshot_unique.png", make_png(300, 300),
                      datetime(2026, 3, 1, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(
            stats["duplicate_bytes_wasted"], 2 * len(data_a) + len(data_b)
        )

    def test_longest_gap(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 1, 20, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["longest_gap_days"], 18)
        self.assertEqual(str(stats["longest_gap_start"]), "2026-01-02")
        self.assertEqual(str(stats["longest_gap_end"]), "2026-01-19")

    def test_longest_gap_at_span_edge(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 1, 2, 12, 0)),
            ("screenshot_c.png", make_png(10, 10), datetime(2026, 1, 31, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["longest_gap_days"], 28)
        self.assertEqual(str(stats["longest_gap_start"]), "2026-01-03")
        self.assertEqual(str(stats["longest_gap_end"]), "2026-01-30")

    def test_busiest_month(self):
        files = [
            ("screenshot_1.png", make_png(10, 10), datetime(2025, 8, 1, 12, 0)),
            ("screenshot_2.png", make_png(10, 10), datetime(2026, 8, 1, 12, 0)),
            ("screenshot_3.png", make_png(10, 10), datetime(2026, 8, 15, 12, 0)),
            ("screenshot_4.png", make_png(10, 10), datetime(2026, 8, 20, 12, 0)),
            ("screenshot_5.png", make_png(10, 10), datetime(2026, 9, 1, 12, 0)),
            ("screenshot_6.png", make_png(10, 10), datetime(2026, 9, 2, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(str(stats["busiest_month"]), "2026-08-01")
        self.assertEqual(stats["busiest_month_count"], 3)

    def test_per_day(self):
        files = [
            (f"screenshot_a{i}.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0))
            for i in range(5)
        ]
        files += [
            (f"screenshot_b{i}.png", make_png(10, 10), datetime(2026, 1, 5, 12, 0))
            for i in range(5)
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["per_day"], 2.0)

    def test_largest(self):
        big = make_png(10, 10, pad=500)
        files = [
            ("screenshot_small.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_big.png", big, datetime(2026, 1, 2, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["largest_name"], "screenshot_big.png")
        self.assertEqual(stats["largest_size"], len(big))
        self.assertEqual(stats["largest_human"], sj.human_bytes(len(big)))


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

    def test_clean_stretch(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 2, 15, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["longest_gap_days"], 44)
        self.assertIn("Clean Stretch", award_names(stats))

    def test_clean_stretch_not_earned_at_29_days(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 1, 31, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["longest_gap_days"], 29)
        self.assertNotIn("Clean Stretch", award_names(stats))

    def test_gigabyte_club(self):
        path = write_file(self.root, "screenshot_big.png", make_png(100, 100),
                          datetime(2026, 1, 1, 12, 0))
        with open(path, "r+b") as fh:
            fh.truncate(1024 ** 3 + 4096)  # sparse: big on paper, cheap on disk
        ts = datetime(2026, 1, 1, 12, 0).timestamp()
        os.utime(path, (ts, ts))
        shots, recordings = sj.collect([self.root])
        stats = sj.analyze(shots, recordings)
        self.assertGreaterEqual(stats["total_bytes"], 1024 ** 3)
        self.assertIn("Gigabyte Club", award_names(stats))


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

    def test_svg_card_written(self):
        self.seed()
        svg = self.root / "card.svg"
        code, _, _ = self.run_main([str(self.root), "--svg", str(svg)])
        self.assertEqual(code, 0)
        text = svg.read_text(encoding="utf-8")
        self.assertIn('viewBox="0 0 1200 630"', text)
        self.assertIn("SNAP JUDGMENT", text)
        self.assertIn("Innocent Bystander", text)
        self.assertIn(
            "Judged from file metadata only. No pixels were read.", text
        )

    def test_svg_escapes_interpolated_text(self):
        stats, _ = self.stats_for([
            ("screenshot_a.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
        ])
        svg = sj.render_svg(stats, "Guilty & <Charged>", "A sentence.", [])
        self.assertIn("Guilty &amp; &lt;Charged&gt;", svg)
        self.assertNotIn("<Charged>", svg)

    def test_json_new_fields(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        for key in ("duplicate_bytes_wasted", "longest_gap_days", "longest_gap",
                    "busiest_month", "per_day", "largest", "skipped", "year"):
            self.assertIn(key, data)
        self.assertEqual(data["skipped"], 0)
        self.assertIsNone(data["year"])
        self.assertEqual(data["busiest_month"], {"month": "Jun 2026", "count": 3})
        self.assertEqual(data["largest"]["name"], "screenshot_0.png")
        self.assertEqual(data["per_day"], 1.0)
        self.assertGreater(data["duplicate_bytes_wasted"], 0)
        self.assertIsNone(data["longest_gap"])

    def test_year_filter(self):
        write_file(self.root, "screenshot_old.png", make_png(10, 10),
                   datetime(2025, 5, 1, 12, 0))
        write_file(self.root, "screenshot_new1.png", make_png(10, 10),
                   datetime(2026, 5, 1, 12, 0))
        write_file(self.root, "screenshot_new2.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--year", "2026", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["year"], 2026)

    def test_year_scope_line(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--year", "2026"])
        self.assertEqual(code, 0)
        self.assertIn("Scope: year 2026", out)

    def test_broken_symlink_is_skipped_not_fatal(self):
        write_file(self.root, "screenshot_ok.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        os.symlink(self.root / "no-such-target.png",
                   self.root / "screenshot_broken.png")
        box = [0]
        shots, _ = sj.collect([self.root], skipped=box)
        self.assertEqual(len(shots), 1)
        self.assertEqual(box[0], 1)
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["skipped"], 1)

    def test_skipped_files_evidence_line(self):
        write_file(self.root, "screenshot_ok.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        os.symlink(self.root / "no-such-target.png",
                   self.root / "screenshot_broken.png")
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Files the court could not read", out)


# ---------------------------------------------------------------------------
# source census (v0.3.0)
# ---------------------------------------------------------------------------

class SourceCensusTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def source_names(self, files):
        stats, _ = self.stats_for(files)
        return [s["name"] for s in stats["sources"]]

    def test_whatsapp_package_source(self):
        names = self.source_names([
            ("Screenshot_20260101_120000_com.whatsapp.png", make_png(10, 10),
             datetime(2026, 1, 1, 12, 0)),
        ])
        self.assertEqual(names, ["WhatsApp"])

    def test_unknown_package_shown_raw(self):
        names = self.source_names([
            ("Screenshot_20260101_120000_com.example.app.png", make_png(10, 10),
             datetime(2026, 1, 1, 12, 0)),
        ])
        self.assertEqual(names, ["com.example.app"])

    def test_form_factor_sources(self):
        cases = [
            ((1170, 2532), "Phone (tall)"),
            ((1080, 1920), "Phone"),
            ((1600, 1600), "Tablet / square"),
            ((1920, 1080), "Desktop / landscape"),
        ]
        for (w, h), expected in cases:
            with self.subTest(dims=(w, h)):
                names = self.source_names([
                    ("screenshot_a.png", make_png(w, h),
                     datetime(2026, 1, 1, 12, 0)),
                ])
                self.assertEqual(names, [expected])

    def test_unknown_origin_for_heic(self):
        names = self.source_names([
            ("screenshot_a.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32,
             datetime(2026, 1, 1, 12, 0)),
        ])
        self.assertEqual(names, ["Unknown origin"])

    def test_version_numbers_are_not_packages(self):
        names = self.source_names([
            ("Screenshot 2026-01-01 at 10.00.00.png", make_png(1920, 1080),
             datetime(2026, 1, 1, 12, 0)),
        ])
        self.assertEqual(names, ["Desktop / landscape"])

    def test_charges_section_shown_at_ten(self):
        for i in range(12):
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("THE CHARGES", out)

    def test_charges_section_hidden_below_ten(self):
        for i in range(3):
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("THE CHARGES", out)


# ---------------------------------------------------------------------------
# retention and weekend statistics (v0.3.0)
# ---------------------------------------------------------------------------

class RetentionWeekendTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_weekend_share(self):
        day = datetime(2026, 1, 1, 12, 0)
        while day.weekday() != 5:  # walk to the first Saturday
            day += timedelta(days=1)
        files = [
            ("screenshot_sat.png", make_png(10, 10), day),
            ("screenshot_sun.png", make_png(10, 10), day + timedelta(days=1)),
            ("screenshot_mon.png", make_png(10, 10), day + timedelta(days=2)),
            ("screenshot_tue.png", make_png(10, 10), day + timedelta(days=3)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["weekend_count"], 2)
        self.assertEqual(stats["weekend_share"], 50.0)

    def test_retention_old_share(self):
        now = datetime.now()
        files = [
            ("screenshot_old.png", make_png(10, 10), now - timedelta(days=400)),
            ("screenshot_new.png", make_png(10, 10), now - timedelta(days=10)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["retention_old_count"], 1)
        self.assertEqual(stats["retention_old_share"], 50.0)

    def test_json_retention_weekend_sources_fields(self):
        write_file(self.root, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        for key in ("retention_old_count", "retention_old_share",
                    "weekend_count", "weekend_share", "sources"):
            self.assertIn(key, data)
        self.assertEqual(data["sources"][0]["name"], "Tablet / square")


# ---------------------------------------------------------------------------
# judge voices (v0.3.0)
# ---------------------------------------------------------------------------

class JudgeVoiceTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def test_strict_voice_presentation(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--judge", "strict"])
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Hanging Judge", out)
        self.assertIn("Mercy is not on today's docket.", out)
        self.assertIn(
            "Sentence: none. This time. The court will be watching the folder.",
            out,
        )
        self.assertIn(
            "The court enters the following into the record, without pleasure:",
            out,
        )

    def test_lenient_voice_presentation(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--judge", "lenient"])
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Sympathetic Judge", out)
        self.assertIn("Encouragement: case dismissed, with applause.", out)
        self.assertIn("The court would also like to celebrate the following:", out)

    def test_fair_voice_is_default_presentation(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("Presiding:", out)
        self.assertIn(
            "Sentence: case dismissed. Go take a screenshot of this verdict.",
            out,
        )

    def test_voice_changes_sentence_not_tier(self):
        fair = sj.verdict_for(300, voice="fair")
        strict = sj.verdict_for(300, voice="strict")
        self.assertEqual(fair[0], "Repeat Offender")
        self.assertEqual(strict[0], "Repeat Offender")
        self.assertNotEqual(fair[1], strict[1])

    def test_unknown_judge_exits_2(self):
        self.seed()
        with self.assertRaises(SystemExit) as ctx:
            self.run_main([str(self.root), "--judge", "bogus"])
        self.assertEqual(ctx.exception.code, 2)

    def test_json_includes_judge(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--json",
                                      "--judge", "strict"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["judge"], "strict")


# ---------------------------------------------------------------------------
# co-defendant mode (v0.3.0)
# ---------------------------------------------------------------------------

class VersusTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def fill(self, folder, n, data=None, start=None):
        data = data if data is not None else make_png(10, 10)
        start = start or datetime(2026, 6, 1, 12, 0)
        for i in range(n):
            write_file(folder, f"screenshot_{i}.png", data,
                       start + timedelta(days=i))

    def test_vs_terminal_comparison(self):
        main_dir, vs_dir = self.root / "main", self.root / "vs"
        self.fill(main_dir, 60)
        self.fill(vs_dir, 3)
        code, out, _ = self.run_main([str(main_dir), "--vs", str(vs_dir)])
        self.assertEqual(code, 0)
        self.assertIn("CO-DEFENDANT COMPARISON", out)
        self.assertIn("Petty Offender", out)
        self.assertIn("Innocent Bystander", out)
        self.assertIn("Bigger offender: the defendant", out)

    def test_vs_json_worse_offender(self):
        main_dir, vs_dir = self.root / "main", self.root / "vs"
        self.fill(main_dir, 60)
        self.fill(vs_dir, 3)
        code, out, _ = self.run_main([str(main_dir), "--vs", str(vs_dir),
                                      "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["versus"]["worse_offender"], "main")
        self.assertEqual(data["versus"]["main"]["count"], 60)
        self.assertEqual(data["versus"]["vs"]["count"], 3)

    def test_vs_tie_equally_guilty(self):
        main_dir, vs_dir = self.root / "main", self.root / "vs"
        self.fill(main_dir, 1)
        self.fill(vs_dir, 1)
        code, out, _ = self.run_main([str(main_dir), "--vs", str(vs_dir),
                                      "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["versus"]["worse_offender"], "tie")
        code, out, _ = self.run_main([str(main_dir), "--vs", str(vs_dir)])
        self.assertEqual(code, 0)
        self.assertIn("Equally guilty: the court cannot separate them.", out)

    def test_vs_size_tiebreak(self):
        main_dir, vs_dir = self.root / "main", self.root / "vs"
        self.fill(main_dir, 2, data=make_png(10, 10, pad=500))
        self.fill(vs_dir, 2)
        code, out, _ = self.run_main([str(main_dir), "--vs", str(vs_dir)])
        self.assertEqual(code, 0)
        self.assertIn("Bigger offender: the defendant, on total size", out)

    def test_vs_missing_folder_exits_2(self):
        main_dir = self.root / "main"
        self.fill(main_dir, 3)
        code, _, err = self.run_main(
            [str(main_dir), "--vs", str(self.root / "nope")]
        )
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)

    def test_vs_empty_folder_exits_2(self):
        main_dir, vs_dir = self.root / "main", self.root / "vs"
        self.fill(main_dir, 3)
        vs_dir.mkdir(parents=True)
        code, _, err = self.run_main([str(main_dir), "--vs", str(vs_dir)])
        self.assertEqual(code, 2)
        self.assertIn("co-defendant folder produced no screenshots", err)

    def test_vs_markdown_includes_comparison(self):
        main_dir, vs_dir = self.root / "main", self.root / "vs"
        self.fill(main_dir, 60)
        self.fill(vs_dir, 3)
        md = self.root / "report.md"
        code, _, _ = self.run_main([str(main_dir), "--vs", str(vs_dir),
                                    "--out", str(md)])
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## Co-Defendant Comparison", text)
        self.assertIn("Bigger offender: the defendant", text)


# ---------------------------------------------------------------------------
# CSV records (v0.3.0)
# ---------------------------------------------------------------------------

class CsvTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_csv_records(self):
        write_file(self.root, "screenshot_a.png", make_png(120, 240),
                   datetime(2026, 6, 1, 12, 0))
        write_file(self.root, "screenshot_b.jpg", make_jpeg(800, 600),
                   datetime(2026, 6, 2, 12, 0))
        write_file(self.root, "Screen Recording 2026-06-03.mov", b"\x00" * 64,
                   datetime(2026, 6, 3, 12, 0))
        csv_path = self.root / "records.csv"
        code, _, _ = self.run_main([str(self.root), "--csv", str(csv_path)])
        self.assertEqual(code, 0)
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(
            rows[0],
            ["path", "size_bytes", "modified", "width", "height", "is_recording"],
        )
        self.assertEqual(len(rows), 4)
        by_name = {Path(r[0]).name: r for r in rows[1:]}
        self.assertEqual(by_name["screenshot_a.png"][3], "120")
        self.assertEqual(by_name["screenshot_a.png"][4], "240")
        self.assertEqual(by_name["screenshot_a.png"][5], "false")
        self.assertEqual(
            by_name["Screen Recording 2026-06-03.mov"][5], "true"
        )
        self.assertEqual(by_name["Screen Recording 2026-06-03.mov"][3], "")


# ---------------------------------------------------------------------------
# court record history (v0.3.0)
# ---------------------------------------------------------------------------

class HistoryTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        self.shots_dir = self.root / "shots"
        self.hist_file = self.root / "court" / "history.jsonl"
        patcher = mock.patch.object(
            sj, "history_path", return_value=self.hist_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed(self, n=3, start=1):
        for i in range(n):
            write_file(self.shots_dir, f"screenshot_{start + i}.png",
                       make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=start + i))

    def records(self):
        return [
            json.loads(line)
            for line in self.hist_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_appended_on_terminal_run(self):
        self.seed()
        code, _, _ = self.run_main([str(self.shots_dir)])
        self.assertEqual(code, 0)
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["count"], 3)
        self.assertEqual(records[0]["verdict"], "Innocent Bystander")
        self.assertEqual(records[0]["judge"], "fair")
        self.assertEqual(records[0]["folders"], [str(self.shots_dir)])

    def test_not_appended_on_json_run(self):
        self.seed()
        code, _, _ = self.run_main([str(self.shots_dir), "--json"])
        self.assertEqual(code, 0)
        self.assertFalse(self.hist_file.exists())

    def test_court_record_line_up(self):
        self.seed(3)
        self.run_main([str(self.shots_dir)])
        self.seed(2, start=10)
        code, out, _ = self.run_main([str(self.shots_dir)])
        self.assertEqual(code, 0)
        self.assertIn(
            "Court record: 3 screenshots at your last hearing on", out
        )
        self.assertIn("(UP by 2 since)", out)

    def test_court_record_line_down(self):
        self.seed(5)
        self.run_main([str(self.shots_dir)])
        os.remove(self.shots_dir / "screenshot_4.png")
        os.remove(self.shots_dir / "screenshot_5.png")
        code, out, _ = self.run_main([str(self.shots_dir)])
        self.assertEqual(code, 0)
        self.assertIn("(DOWN by 2 since)", out)

    def test_history_flag_lists_hearings(self):
        self.seed()
        self.run_main([str(self.shots_dir)])
        code, out, _ = self.run_main(["--history"])
        self.assertEqual(code, 0)
        self.assertIn("COURT RECORD", out)
        self.assertIn(str(self.shots_dir), out)
        self.assertIn("3 screenshots", out)
        self.assertIn("Innocent Bystander", out)

    def test_history_flag_without_record_exits_2(self):
        code, _, err = self.run_main(["--history"])
        self.assertEqual(code, 2)
        self.assertIn("no court record", err)


# ---------------------------------------------------------------------------
# exhibits (v0.4.0)
# ---------------------------------------------------------------------------

class ExhibitsTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_days(self, n, start=datetime(2026, 6, 1, 12, 0)):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       start + timedelta(days=i))

    def test_exhibits_section_lists_five_oldest(self):
        self.seed_days(7)
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("EXHIBITS", out)
        self.assertIn("Exhibit A - screenshot_0.png (Jun 1, 2026)", out)
        self.assertIn("Exhibit E - screenshot_4.png (Jun 5, 2026)", out)
        self.assertNotIn("Exhibit F", out)

    def test_exhibits_hidden_below_five(self):
        self.seed_days(4)
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("EXHIBITS", out)

    def test_exhibits_ties_broken_by_filename(self):
        when = datetime(2026, 6, 1, 12, 0)
        write_file(self.root, "screenshot_z.png", make_png(10, 10), when)
        write_file(self.root, "screenshot_a.png", make_png(10, 10), when)
        write_file(self.root, "screenshot_m.png", make_png(10, 10),
                   when + timedelta(days=1))
        shots, recordings = sj.collect([self.root])
        stats = sj.analyze(shots, recordings)
        self.assertEqual(
            [e["name"] for e in stats["exhibits"]],
            ["screenshot_a.png", "screenshot_z.png", "screenshot_m.png"],
        )

    def test_exhibits_in_json(self):
        self.seed_days(6)
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["exhibits"]), 5)
        self.assertEqual(data["exhibits"][0]["name"], "screenshot_0.png")
        self.assertEqual(data["exhibits"][0]["date"], "2026-06-01")

    def test_exhibits_in_markdown(self):
        self.seed_days(5)
        md = self.root / "report.md"
        code, _, _ = self.run_main([str(self.root), "--out", str(md)])
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## Exhibits", text)
        self.assertIn("**Exhibit A** - screenshot_0.png (Jun 1, 2026)", text)


# ---------------------------------------------------------------------------
# formats and season evidence (v0.4.0)
# ---------------------------------------------------------------------------

class FormatsSeasonTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_formats_counts(self):
        files = [
            (f"screenshot_p{i}.png", make_png(10, 10),
             datetime(2026, 6, 1 + i, 12, 0)) for i in range(3)
        ]
        files.append(("screenshot_j.jpg", make_jpeg(10, 10),
                      datetime(2026, 6, 10, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["formats"], {"jpg": 1, "png": 3})

    def test_formats_evidence_line(self):
        for i in range(3):
            write_file(self.root, f"screenshot_p{i}.png", make_png(10, 10),
                       datetime(2026, 6, 1 + i, 12, 0))
        write_file(self.root, "screenshot_j.jpg", make_jpeg(10, 10),
                   datetime(2026, 6, 10, 12, 0))
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Formats", out)
        self.assertIn("PNG 75%", out)
        self.assertIn("JPG 25%", out)

    def test_season_picks_busiest(self):
        files = [
            (f"screenshot_o{i}.png", make_png(10, 10),
             datetime(2026, 10, 1 + i, 12, 0)) for i in range(4)
        ]
        files.append(("screenshot_j.png", make_png(10, 10),
                      datetime(2026, 1, 15, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["season"]["name"], "Autumn (Sep-Nov)")
        self.assertEqual(stats["season"]["share"], 80.0)

    def test_formats_and_season_in_json(self):
        write_file(self.root, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 10, 1, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["formats"], {"png": 1})
        self.assertEqual(data["season"]["name"], "Autumn (Sep-Nov)")
        self.assertEqual(data["season"]["share"], 100.0)


# ---------------------------------------------------------------------------
# per-folder breakdown (v0.4.0)
# ---------------------------------------------------------------------------

class FolderBreakdownTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def fill(self, folder, n):
        for i in range(n):
            write_file(folder, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def test_folders_block_in_terminal(self):
        dir_a, dir_b = self.root / "a", self.root / "b"
        self.fill(dir_a, 3)
        self.fill(dir_b, 2)
        code, out, _ = self.run_main([str(dir_a), str(dir_b)])
        self.assertEqual(code, 0)
        self.assertIn("FOLDERS JUDGED", out)
        self.assertIn(f"{dir_a} - 3 screenshots", out)
        self.assertIn(f"{dir_b} - 2 screenshots", out)

    def test_folders_block_hidden_for_single_folder(self):
        self.fill(self.root, 3)
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("FOLDERS JUDGED", out)

    def test_folders_in_json(self):
        dir_a, dir_b = self.root / "a", self.root / "b"
        self.fill(dir_a, 3)
        self.fill(dir_b, 2)
        code, out, _ = self.run_main([str(dir_a), str(dir_b), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(
            data["folders"],
            [{"path": str(dir_a), "count": 3},
             {"path": str(dir_b), "count": 2}],
        )

    def test_vs_folder_not_in_main_folders(self):
        dir_a, dir_b = self.root / "a", self.root / "b"
        self.fill(dir_a, 3)
        self.fill(dir_b, 2)
        code, out, _ = self.run_main([str(dir_a), "--vs", str(dir_b),
                                      "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["folders"],
                         [{"path": str(dir_a), "count": 3}])
        self.assertEqual(data["versus"]["vs"]["folders"],
                         [{"path": str(dir_b), "count": 2}])


# ---------------------------------------------------------------------------
# year comparison (v0.4.0)
# ---------------------------------------------------------------------------

class VsYearTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def fill_year(self, year, n, start_day=0):
        base = datetime(year, 1, 1, 12, 0)
        for i in range(n):
            write_file(self.root, f"screenshot_{year}_{i}.png",
                       make_png(10, 10), base + timedelta(days=start_day + i))

    def test_vs_year_improvement(self):
        self.fill_year(2025, 100)
        self.fill_year(2026, 50)
        code, out, _ = self.run_main([str(self.root), "--vs-year", "2026"])
        self.assertEqual(code, 0)
        self.assertIn("YEAR COMPARISON", out)
        self.assertIn(
            "The court notes improvement: 2026 is down 50% on 2025.", out
        )

    def test_vs_year_relapse(self):
        self.fill_year(2025, 50)
        self.fill_year(2026, 100)
        code, out, _ = self.run_main([str(self.root), "--vs-year", "2026"])
        self.assertEqual(code, 0)
        self.assertIn(
            "The court notes a relapse: 2026 is up 100% on 2025.", out
        )

    def test_vs_year_target_may_be_the_older_year(self):
        self.fill_year(2025, 100)
        self.fill_year(2026, 50)
        code, out, _ = self.run_main([str(self.root), "--vs-year", "2025"])
        self.assertEqual(code, 0)
        self.assertIn("YEAR COMPARISON", out)
        self.assertIn(
            "The court notes improvement: 2026 is down 50% on 2025.", out
        )

    def test_vs_year_no_change(self):
        self.fill_year(2025, 40)
        self.fill_year(2026, 40)
        code, out, _ = self.run_main([str(self.root), "--vs-year", "2026"])
        self.assertEqual(code, 0)
        self.assertIn("The court notes no change in behavior", out)

    def test_vs_year_json(self):
        self.fill_year(2025, 100)
        self.fill_year(2026, 50)
        code, out, _ = self.run_main([str(self.root), "--vs-year", "2026",
                                      "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        vs_year = data["vs_year"]
        self.assertEqual(vs_year["target_year"], 2026)
        self.assertEqual(vs_year["other_year"], 2025)
        self.assertEqual(vs_year["target"]["count"], 50)
        self.assertEqual(vs_year["other"]["count"], 100)
        self.assertEqual(vs_year["target"]["verdict"], "Petty Offender")
        self.assertEqual(vs_year["change_pct"], -50.0)

    def test_vs_year_single_year_exits_2(self):
        self.fill_year(2026, 10)
        code, _, err = self.run_main([str(self.root), "--vs-year", "2026"])
        self.assertEqual(code, 2)
        self.assertIn("at least two different years", err)

    def test_vs_year_absent_year_exits_2(self):
        self.fill_year(2025, 10)
        self.fill_year(2026, 10)
        code, _, err = self.run_main([str(self.root), "--vs-year", "2024"])
        self.assertEqual(code, 2)
        self.assertIn("no screenshots from 2024", err)


# ---------------------------------------------------------------------------
# wrapped kit (v0.4.0)
# ---------------------------------------------------------------------------

class WrappedKitTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_year(self, year, n=12):
        base = datetime(year, 3, 1, 23, 0)
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       base + timedelta(days=i))

    def short_post(self, text):
        lines = text.splitlines()
        start = lines.index("SHORT POST") + 1
        return lines[start]

    def test_kit_writes_svg_and_post(self):
        year = datetime.now().year
        self.seed_year(year)
        kit = self.root / "kit"
        code, out, _ = self.run_main([str(self.root), "--wrapped-kit",
                                      str(kit)])
        self.assertEqual(code, 0)
        svg = kit / f"wrapped-{year}.svg"
        post = kit / "post.txt"
        self.assertTrue(svg.is_file())
        self.assertTrue(post.is_file())
        self.assertIn(str(svg), out)
        self.assertIn(str(post), out)
        self.assertIn("SNAP JUDGMENT", svg.read_text(encoding="utf-8"))

    def test_kit_post_contents(self):
        year = datetime.now().year
        self.seed_year(year)
        kit = self.root / "kit"
        code, _, _ = self.run_main([str(self.root), "--wrapped-kit",
                                    str(kit)])
        self.assertEqual(code, 0)
        text = (kit / "post.txt").read_text(encoding="utf-8")
        self.assertIn("STANDARD POST", text)
        short = self.short_post(text)
        self.assertLessEqual(len(short), 280)
        self.assertIn("12 screenshots", short)
        self.assertIn("Innocent Bystander", short)
        self.assertIn("12 screenshots", text)
        self.assertIn("Innocent Bystander", text)
        self.assertIn(
            "https://github.com/littlestjames82-sys/snap-judgment", text
        )

    def test_kit_with_explicit_year(self):
        self.seed_year(2025)
        kit = self.root / "kit"
        code, _, _ = self.run_main([str(self.root), "--year", "2025",
                                    "--wrapped-kit", str(kit)])
        self.assertEqual(code, 0)
        self.assertTrue((kit / "wrapped-2025.svg").is_file())
        text = (kit / "post.txt").read_text(encoding="utf-8")
        self.assertIn("My 2025 in screenshots", text)

    def test_kit_empty_year_exits_2_and_writes_nothing(self):
        self.seed_year(2024)
        kit = self.root / "kit"
        code, _, err = self.run_main([str(self.root), "--year", "2026",
                                      "--wrapped-kit", str(kit)])
        self.assertEqual(code, 2)
        self.assertIn("no screenshots from 2026", err)
        self.assertFalse(kit.exists())


# ---------------------------------------------------------------------------
# fail-above gate (v0.4.0)
# ---------------------------------------------------------------------------

class FailAboveTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=60):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 1, 1, 12, 0) + timedelta(days=i % 300))

    def test_fail_above_lower_tier_exits_1(self):
        self.seed()
        code, out, _ = self.run_main(
            [str(self.root), "--fail-above", "Innocent Bystander"]
        )
        self.assertEqual(code, 1)
        self.assertIn("Petty Offender", out)  # the report still prints

    def test_fail_above_same_tier_exits_0(self):
        self.seed()
        code, _, _ = self.run_main(
            [str(self.root), "--fail-above", "Petty Offender"]
        )
        self.assertEqual(code, 0)

    def test_fail_above_higher_tier_exits_0(self):
        self.seed()
        code, _, _ = self.run_main(
            [str(self.root), "--fail-above", "Repeat Offender"]
        )
        self.assertEqual(code, 0)

    def test_fail_above_name_is_flexible(self):
        self.seed()
        for spelling in ("petty-offender", "PETTY  OFFENDER", " petty offender "):
            with self.subTest(spelling=spelling):
                code, _, _ = self.run_main(
                    [str(self.root), "--fail-above", spelling]
                )
                self.assertEqual(code, 0)
        code, _, _ = self.run_main(
            [str(self.root), "--fail-above", "innocent-bystander"]
        )
        self.assertEqual(code, 1)

    def test_fail_above_unknown_tier_exits_2(self):
        self.seed()
        code, _, err = self.run_main(
            [str(self.root), "--fail-above", "Banana"]
        )
        self.assertEqual(code, 2)
        self.assertIn("Innocent Bystander", err)
        self.assertIn("Unrepentant Screenshot Felon", err)

    def test_fail_above_applies_in_json_mode(self):
        self.seed()
        code, out, _ = self.run_main(
            [str(self.root), "--json", "--fail-above", "Innocent Bystander"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["verdict"], "Petty Offender")

    def test_fail_above_no_evidence_stays_2(self):
        code, _, _ = self.run_main(
            [str(self.root), "--fail-above", "Innocent Bystander"]
        )
        self.assertEqual(code, 2)


# ---------------------------------------------------------------------------
# new awards and markdown bars (v0.4.0)
# ---------------------------------------------------------------------------

class NewAward040Tests(TempDirCase):
    def test_three_am_club(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10),
             datetime(2026, 6, 1 + (i % 20), 2 + (i % 2), 30))
            for i in range(10)
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["three_am_count"], 10)
        self.assertIn("The 3 AM Club", award_names(stats))

    def test_three_am_club_not_earned_at_nine(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10),
             datetime(2026, 6, 1 + (i % 20), 3, 30))
            for i in range(9)
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["three_am_count"], 9)
        self.assertNotIn("The 3 AM Club", award_names(stats))

    def test_lifer(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2018, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 6, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertGreaterEqual(stats["span_days"], 8 * 365)
        names = award_names(stats)
        self.assertIn("Lifer", names)
        lifer = [a for a in sj.compute_awards(stats)
                 if a["name"] == "Lifer"][0]
        self.assertIn("years", lifer["evidence"])

    def test_lifer_not_earned_below_eight_years(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2022, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Time Traveler", award_names(stats))
        self.assertNotIn("Lifer", award_names(stats))

    def test_markdown_year_bars(self):
        files = [
            ("screenshot_a1.png", make_png(10, 10), datetime(2024, 3, 1, 12, 0)),
            ("screenshot_a2.png", make_png(10, 10), datetime(2024, 4, 1, 12, 0)),
            ("screenshot_b1.png", make_png(10, 10), datetime(2026, 3, 1, 12, 0)),
            ("screenshot_b2.png", make_png(10, 10), datetime(2026, 4, 1, 12, 0)),
            ("screenshot_b3.png", make_png(10, 10), datetime(2026, 5, 1, 12, 0)),
            ("screenshot_b4.png", make_png(10, 10), datetime(2026, 6, 1, 12, 0)),
        ]
        for name, data, when in files:
            write_file(self.root, name, data, when)
        md = self.root / "report.md"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main([str(self.root), "--out", str(md)])
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn(f"2026: 4 {'█' * 20}", text)
        self.assertIn(f"2024: 2 {'█' * 10}", text)


# ---------------------------------------------------------------------------
# PNG share card (v0.5.0)
# ---------------------------------------------------------------------------

class PngCardTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def test_png_signature_and_dimensions(self):
        self.seed()
        png = self.root / "card.png"
        code, _, _ = self.run_main([str(self.root), "--png", str(png)])
        self.assertEqual(code, 0)
        data = png.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(int.from_bytes(data[16:20], "big"), 1200)
        self.assertEqual(int.from_bytes(data[20:24], "big"), 630)

    def test_png_render_is_deterministic(self):
        stats, _ = self.stats_for([
            (f"screenshot_{i}.png", make_png(100, 100),
             datetime(2026, 6, 1 + i, 12, 0)) for i in range(3)
        ])
        tier, sentence = sj.verdict_for(stats["count"])
        awards = sj.compute_awards(stats)
        first = sj.render_png(stats, tier, sentence, awards)
        second = sj.render_png(stats, tier, sentence, awards)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 1000)

    def test_png_unusual_characters_do_not_crash(self):
        stats, _ = self.stats_for([
            ("screenshot_a.png", make_png(10, 10),
             datetime(2026, 6, 1, 12, 0)),
        ])
        data = sj.render_png(stats, "Guilty ☃ & <weird>!",
                             "A sentence with a snowman ☃.", [])
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

    def test_font_covers_required_characters(self):
        required = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
            "0123456789.,:;!?'%/-+()& "
        )
        for ch in required:
            with self.subTest(ch=ch):
                self.assertIn(ch, sj._FONT_5X7)
                glyph = sj._FONT_5X7[ch]
                self.assertEqual(len(glyph), 7)
                self.assertTrue(all(len(row) == 5 for row in glyph))


# ---------------------------------------------------------------------------
# judge packs (v0.5.0)
# ---------------------------------------------------------------------------

class JudgePackTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def write_pack(self, pack, name="pack.json"):
        path = self.root / name
        path.write_text(json.dumps(pack), encoding="utf-8")
        return path

    def poet_pack(self):
        return {
            "name": "The Poet",
            "intro": "The Poet presides over {count} screenshots, "
                     "verdict to come: {tier}.",
            "sentences": {
                "Innocent Bystander": "A short poem of a folder.",
            },
            "award_lead": "The court pins these medals:",
        }

    def test_pack_voice_applied(self):
        self.seed()
        pack = self.write_pack(self.poet_pack())
        code, out, _ = self.run_main(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Poet", out)
        self.assertIn(
            "The Poet presides over 3 screenshots, verdict to come: "
            "Innocent Bystander.", out,
        )
        self.assertIn("A short poem of a folder.", out)
        self.assertIn("The court pins these medals:", out)

    def test_pack_name_in_json(self):
        self.seed()
        pack = self.write_pack(self.poet_pack())
        code, out, _ = self.run_main(
            [str(self.root), "--json", "--judge-file", str(pack)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["judge"], "The Poet")

    def test_pack_missing_tier_falls_back_to_fair(self):
        for i in range(60):  # Petty Offender; the pack has no such sentence
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 1, 1, 12, 0) + timedelta(days=i % 300))
        pack = self.write_pack(self.poet_pack())
        code, out, _ = self.run_main(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Petty Offender", out)
        self.assertIn(
            "Sentence: community service. Delete one duplicate. "
            "Just one. Baby steps.", out,
        )

    def test_pack_invalid_json_exits_2(self):
        path = self.root / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        code, _, err = self.run_main(
            [str(self.root), "--judge-file", str(path)]
        )
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_pack_wrong_shape_exits_2(self):
        for bad in (["not", "an", "object"],
                    {"name": 5},
                    {"name": "X", "sentences": {"A": 3}},
                    {"name": "X", "intro": 4}):
            with self.subTest(bad=bad):
                pack = self.write_pack(bad, name="bad.json")
                code, _, err = self.run_main(
                    [str(self.root), "--judge-file", str(pack)]
                )
                self.assertEqual(code, 2)
                self.assertIn("judge file", err)

    def test_pack_missing_file_exits_2(self):
        code, _, err = self.run_main(
            [str(self.root), "--judge-file", str(self.root / "nope.json")]
        )
        self.assertEqual(code, 2)
        self.assertIn("judge file not found", err)


# ---------------------------------------------------------------------------
# HTML report (v0.5.0)
# ---------------------------------------------------------------------------

class HtmlReportTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_html_report_contents(self):
        for i in range(6):
            write_file(self.root, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        page = self.root / "report.html"
        code, _, _ = self.run_main([str(self.root), "--html", str(page)])
        self.assertEqual(code, 0)
        text = page.read_text(encoding="utf-8")
        self.assertIn("<title>Snap Judgment - Innocent Bystander</title>",
                      text)
        self.assertIn("The Evidence", text)
        self.assertIn("Total screenshots", text)
        self.assertIn('class="bar"', text)
        self.assertIn("width:100.0%", text)
        self.assertIn("Exhibits", text)
        self.assertIn("Exhibit A", text)
        self.assertIn("Awards", text)
        self.assertIn("case dismissed", text)

    def test_html_report_escapes_filenames(self):
        write_file(self.root, "screenshot_<b>.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        page = self.root / "report.html"
        code, _, _ = self.run_main([str(self.root), "--html", str(page)])
        self.assertEqual(code, 0)
        text = page.read_text(encoding="utf-8")
        self.assertIn("screenshot_&lt;b&gt;.png", text)
        self.assertNotIn("screenshot_<b>.png", text)


# ---------------------------------------------------------------------------
# silent months, median, anniversary (v0.5.0)
# ---------------------------------------------------------------------------

class NewStats050Tests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_silent_months(self):
        files = [
            ("screenshot_jan.png", make_png(10, 10),
             datetime(2026, 1, 15, 12, 0)),
            ("screenshot_apr.png", make_png(10, 10),
             datetime(2026, 4, 15, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["silent_months"], 2)  # February and March

    def test_median_per_active_day(self):
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
            ("screenshot_b1.png", make_png(10, 10), datetime(2026, 1, 2, 12, 0)),
            ("screenshot_b2.png", make_png(10, 10), datetime(2026, 1, 2, 13, 0)),
            ("screenshot_c1.png", make_png(10, 10), datetime(2026, 1, 3, 12, 0)),
            ("screenshot_c2.png", make_png(10, 10), datetime(2026, 1, 3, 13, 0)),
            ("screenshot_c3.png", make_png(10, 10), datetime(2026, 1, 3, 14, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["median_per_active_day"], 2.0)

    def test_anniversary(self):
        files = []
        for year, n in ((2024, 2), (2025, 2), (2026, 1)):
            for i in range(n):
                files.append((f"screenshot_{year}_{i}.png", make_png(10, 10),
                              datetime(year, 3, 14, 10 + i, 0)))
        files.append(("screenshot_other.png", make_png(10, 10),
                      datetime(2026, 7, 4, 12, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(
            stats["anniversary"],
            {"month_day": "Mar 14", "count": 5, "years": 3},
        )

    def test_anniversary_below_threshold_is_none(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10),
             datetime(2026, 3, 14, 10 + i, 0)) for i in range(4)
        ]
        stats, _ = self.stats_for(files)
        self.assertIsNone(stats["anniversary"])

    def test_new_stats_in_json_and_evidence(self):
        write_file(self.root, "screenshot_jan.png", make_png(10, 10),
                   datetime(2026, 1, 15, 12, 0))
        write_file(self.root, "screenshot_apr.png", make_png(10, 10),
                   datetime(2026, 4, 15, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["silent_months"], 2)
        self.assertEqual(data["median_per_active_day"], 1.0)
        self.assertIsNone(data["anniversary"])
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Silent months", out)
        self.assertIn("Median per active day", out)


# ---------------------------------------------------------------------------
# anniversary and creature-of-habit awards (v0.5.0)
# ---------------------------------------------------------------------------

class NewAward050Tests(TempDirCase):
    def test_anniversary_award(self):
        files = []
        for year in (2024, 2025, 2026):
            for i in range(5):
                files.append((f"screenshot_{year}_{i}.png", make_png(10, 10),
                              datetime(year, 3, 14, 9 + i, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["anniversary"]["count"], 15)
        names = award_names(stats)
        self.assertIn("Anniversary", names)
        award = [a for a in sj.compute_awards(stats)
                 if a["name"] == "Anniversary"][0]
        self.assertIn("Mar 14", award["evidence"])
        self.assertIn("15", award["evidence"])

    def test_creature_of_habit(self):
        files = []
        for i in range(7):
            files.append((f"screenshot_d{i}.png", make_png(10, 10),
                          datetime(2026, 6, 1 + i, 9 + i, 0)))
        for i in range(3):
            files.append((f"screenshot_h{i}.png", make_png(10, 10),
                          datetime(2026, 6, 10 + i, 14, 0)))
        stats, _ = self.stats_for(files)
        self.assertEqual(stats["busiest_hour"], 14)
        self.assertIn("Creature of Habit", award_names(stats))

    def test_creature_of_habit_not_earned_when_spread_out(self):
        files = [
            (f"screenshot_{i}.png", make_png(10, 10),
             datetime(2026, 6, 1 + (i % 5), 8 + i, 0))
            for i in range(10)
        ]
        stats, _ = self.stats_for(files)
        self.assertNotIn("Creature of Habit", award_names(stats))


# ---------------------------------------------------------------------------
# one-line mode (v0.5.0)
# ---------------------------------------------------------------------------

class OnelineTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def test_oneline_exactly_one_line(self):
        self.seed()
        code, out, _ = self.run_main([str(self.root), "--oneline"])
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(
            lines[0].startswith("Innocent Bystander · 3 screenshots · ")
        )
        self.assertTrue(lines[0].endswith("Jun 2026 - Jun 2026"))

    def test_oneline_format_matches_summary_helper(self):
        stats, _ = self.stats_for([
            (f"screenshot_{i}.png", make_png(100, 100),
             datetime(2026, 6, 1 + i, 12, 0)) for i in range(3)
        ])
        line = sj.oneline_summary(stats, "Innocent Bystander")
        size = sj.human_bytes(3 * len(make_png(100, 100)))
        self.assertEqual(
            line,
            f"Innocent Bystander · 3 screenshots · {size} · "
            "Jun 2026 - Jun 2026",
        )

    def test_oneline_still_writes_card(self):
        self.seed()
        card = self.root / "card.html"
        code, out, _ = self.run_main(
            [str(self.root), "--oneline", "--card", str(card)]
        )
        self.assertEqual(code, 0)
        self.assertTrue(card.is_file())
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_oneline_json_wins(self):
        self.seed()
        code, out, _ = self.run_main(
            [str(self.root), "--oneline", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "Innocent Bystander")

    def test_oneline_no_evidence(self):
        code, out, _ = self.run_main([str(self.root), "--oneline"])
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), "No Evidence · 0 screenshots")


if __name__ == "__main__":
    unittest.main()
