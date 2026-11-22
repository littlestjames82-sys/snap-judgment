"""Tests for snap_judgment. Stdlib unittest only.

All fixtures are synthetic: minimal header-only image files written to
temp folders, with exact mtimes set via os.utime.
"""

import csv
import io
import json
import os
import re
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
        # Renamed in v0.6.0: the span award is now The Long Haul, and
        # "Time Traveler" belongs to screenshots dated in the future.
        files = [
            ("screenshot_a.png", make_png(10, 10), datetime(2021, 1, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10), datetime(2026, 1, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("The Long Haul", award_names(stats))
        self.assertNotIn("Time Traveler", award_names(stats))

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
        self.assertIn("The Long Haul", award_names(stats))
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
        # "<" and ">" cannot exist in a Windows filename, so on Windows the
        # fixture uses "&" (legal there, still HTML-escaped) instead.
        if os.name == "nt":
            raw, escaped = "screenshot_&_co.png", "screenshot_&amp;_co.png"
        else:
            raw, escaped = "screenshot_<b>.png", "screenshot_&lt;b&gt;.png"
        write_file(self.root, raw, make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        page = self.root / "report.html"
        code, _, _ = self.run_main([str(self.root), "--html", str(page)])
        self.assertEqual(code, 0)
        text = page.read_text(encoding="utf-8")
        self.assertIn(escaped, text)
        self.assertNotIn(raw, text)


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


# ---------------------------------------------------------------------------
# plea bargain (v0.6.0)
# ---------------------------------------------------------------------------

class PleaBargainTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_plea_folder(self):
        """6 screenshots: 1 newest, a duplicate trio, 2 old (2023)."""
        png = make_png(100, 100)
        write_file(self.root, "screenshot_new.png", make_png(200, 200),
                   datetime(2026, 6, 1, 12, 0))
        for i in range(3):
            write_file(self.root, f"screenshot_dup{i}.png", png,
                       datetime(2026, 5, 1, 12, 0) + timedelta(days=i))
        write_file(self.root, "screenshot_old1.png", make_png(50, 50, pad=10),
                   datetime(2023, 1, 1, 12, 0))
        write_file(self.root, "screenshot_old2.png", make_png(60, 60, pad=20),
                   datetime(2023, 6, 1, 12, 0))
        return png

    def test_exact_numbers(self):
        png = self.seed_plea_folder()
        shots, _recordings = sj.collect([self.root])
        plea = sj.compute_plea_bargain(shots, 2)
        self.assertEqual(plea["old_removed"], 2)
        self.assertEqual(plea["duplicate_removed"], 2)
        self.assertEqual(plea["removed_count"], 4)
        self.assertEqual(plea["remaining_count"], 2)
        expected_bytes = (
            2 * len(png)
            + len(make_png(50, 50, pad=10))
            + len(make_png(60, 60, pad=20))
        )
        self.assertEqual(plea["removed_bytes"], expected_bytes)
        self.assertEqual(plea["projected_tier"], "Innocent Bystander")
        self.assertEqual(plea["cutoff"], datetime(2024, 6, 1).date())

    def test_plea_years_zero(self):
        self.seed_plea_folder()
        shots, _recordings = sj.collect([self.root])
        plea = sj.compute_plea_bargain(shots, 0)
        # cutoff is the newest date itself: everything older goes
        self.assertEqual(plea["removed_count"], 5)
        self.assertEqual(plea["remaining_count"], 1)

    def test_section_absent_without_flag(self):
        self.seed_plea_folder()
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("PLEA BARGAIN", out)
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("plea_bargain", json.loads(out))

    def test_section_present_and_unchanged_line(self):
        self.seed_plea_folder()
        code, out, _ = self.run_main([str(self.root), "--plea-bargain"])
        self.assertEqual(code, 0)
        self.assertIn("THE PLEA BARGAIN", out)
        self.assertIn("Projected verdict", out)
        self.assertIn("Innocent Bystander", out)
        self.assertIn(
            "The court notes this plea changes nothing. "
            "The verdict stands.",
            out,
        )

    def test_accept_line_when_tier_improves(self):
        for i in range(50):
            write_file(self.root, f"screenshot_old{i}.png",
                       make_png(10 + i, 10),
                       datetime(2020, 1, 1, 12, 0) + timedelta(days=i))
        for i in range(5):
            write_file(self.root, f"screenshot_new{i}.png",
                       make_png(500 + i, 10),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        code, out, _ = self.run_main([str(self.root), "--plea-bargain"])
        self.assertEqual(code, 0)
        self.assertIn("Petty Offender", out)
        self.assertIn("The court would accept this plea.", out)

    def test_json_plea_bargain(self):
        png = self.seed_plea_folder()
        code, out, _ = self.run_main(
            [str(self.root), "--plea-bargain", "--json"]
        )
        self.assertEqual(code, 0)
        plea = json.loads(out)["plea_bargain"]
        self.assertEqual(plea["years"], 2)
        self.assertEqual(plea["old_removed"], 2)
        self.assertEqual(plea["duplicate_removed"], 2)
        self.assertEqual(plea["removed_count"], 4)
        self.assertEqual(plea["remaining_count"], 2)
        self.assertEqual(plea["projected_tier"], "Innocent Bystander")
        self.assertEqual(
            plea["removed_bytes"],
            2 * len(png)
            + len(make_png(50, 50, pad=10))
            + len(make_png(60, 60, pad=20)),
        )

    def test_negative_plea_years_exits_2(self):
        self.seed_plea_folder()
        code, _, err = self.run_main(
            [str(self.root), "--plea-bargain", "--plea-years=-1"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--plea-years", err)

    def test_markdown_includes_plea(self):
        self.seed_plea_folder()
        md = self.root / "report.md"
        code, _, _ = self.run_main(
            [str(self.root), "--plea-bargain", "--out", str(md)]
        )
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## The Plea Bargain", text)
        self.assertIn("hypothetical", text)


# ---------------------------------------------------------------------------
# rematch (v0.6.0)
# ---------------------------------------------------------------------------

class RematchTests(TempDirCase):
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

    def seed_record(self, count, verdict, when="2026-06-03T12:00:00",
                    folders=None):
        self.hist_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": when,
            "folders": (
                folders if folders is not None else [str(self.shots_dir)]
            ),
            "count": count,
            "total_bytes": 0,
            "verdict": verdict,
            "judge": "fair",
        }
        with open(self.hist_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def seed_shots(self, n, base=datetime(2026, 1, 1, 12, 0), start=0):
        for i in range(n):
            write_file(
                self.shots_dir, f"screenshot_{start + i}.png",
                make_png(100 + start + i, 100),
                base + timedelta(days=start + i),
            )

    def test_rematch_unchanged(self):
        self.seed_record(3, "Innocent Bystander")
        self.seed_shots(5)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("REMATCH", out)
        self.assertIn(
            "Since your last hearing (Jun 3, 2026): +2 screenshots.", out
        )
        self.assertIn("The verdict is unchanged", out)

    def test_rematch_worsened(self):
        self.seed_record(3, "Innocent Bystander")
        self.seed_shots(60)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("+57 screenshots", out)
        self.assertIn(
            "The verdict has worsened from Innocent Bystander "
            "to Petty Offender.",
            out,
        )

    def test_rematch_improved(self):
        self.seed_record(60, "Petty Offender")
        self.seed_shots(5)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("-55 screenshots", out)
        self.assertIn(
            "The verdict has improved from Petty Offender "
            "to Innocent Bystander.",
            out,
        )

    def test_rematch_no_record(self):
        self.seed_shots(3)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("No prior hearing on record for these folders.", out)

    def test_rematch_other_folder_does_not_match(self):
        self.seed_record(
            3, "Innocent Bystander", folders=[str(self.root / "elsewhere")]
        )
        self.seed_shots(3)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("No prior hearing on record for these folders.", out)

    def test_rematch_json(self):
        self.seed_record(3, "Innocent Bystander")
        self.seed_shots(5)
        code, out, _ = self.run_main(
            [str(self.shots_dir), "--rematch", "--json"]
        )
        self.assertEqual(code, 0)
        rematch = json.loads(out)["rematch"]
        self.assertEqual(rematch["previous_count"], 3)
        self.assertEqual(rematch["delta"], 2)
        self.assertEqual(rematch["previous_tier"], "Innocent Bystander")
        self.assertEqual(rematch["date"], "2026-06-03")

    def test_rematch_json_null_without_record(self):
        self.seed_shots(3)
        code, out, _ = self.run_main(
            [str(self.shots_dir), "--rematch", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["rematch"])

    def test_rematch_key_absent_without_flag(self):
        self.seed_record(3, "Innocent Bystander")
        self.seed_shots(3)
        code, out, _ = self.run_main([str(self.shots_dir), "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("rematch", json.loads(out))

    def test_rematch_reads_record_before_own_append(self):
        self.seed_shots(3)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("No prior hearing on record", out)
        self.seed_shots(2, start=3)
        code, out, _ = self.run_main([str(self.shots_dir), "--rematch"])
        self.assertEqual(code, 0)
        self.assertIn("+2 screenshots", out)
        self.assertNotIn("No prior hearing on record", out)

    def test_rematch_markdown(self):
        self.seed_record(3, "Innocent Bystander")
        self.seed_shots(5)
        md = self.root / "report.md"
        code, _, _ = self.run_main(
            [str(self.shots_dir), "--rematch", "--out", str(md)]
        )
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## Rematch", text)
        self.assertIn("+2 screenshots", text)


# ---------------------------------------------------------------------------
# heatmap (v0.6.0)
# ---------------------------------------------------------------------------

class HeatmapTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def a_monday(self):
        day = datetime(2026, 6, 1, 14, 0)
        while day.weekday() != 0:
            day += timedelta(days=1)
        return day

    def seed_heatmap(self):
        mon = self.a_monday()
        tue = mon + timedelta(days=1)
        files = [
            ("screenshot_a.png", make_png(10, 10), mon),
            ("screenshot_b.png", make_png(10, 10), mon + timedelta(minutes=5)),
            ("screenshot_c.png", make_png(10, 10), mon + timedelta(minutes=9)),
            ("screenshot_d.png", make_png(10, 10), tue.replace(hour=9)),
        ]
        for name, data, when in files:
            write_file(self.root, name, data, when)

    def test_heatmap_cells_and_sum(self):
        self.seed_heatmap()
        shots, recordings = sj.collect([self.root])
        stats = sj.analyze(shots, recordings)
        self.assertEqual(len(stats["heatmap"]), 7)
        self.assertTrue(all(len(row) == 24 for row in stats["heatmap"]))
        self.assertEqual(stats["heatmap"][0][14], 3)
        self.assertEqual(stats["heatmap"][1][9], 1)
        self.assertEqual(sum(sum(r) for r in stats["heatmap"]), 4)

    def test_heatmap_json_sums_to_count(self):
        self.seed_heatmap()
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(
            sum(sum(r) for r in data["heatmap"]), data["count"]
        )
        self.assertEqual(data["heatmap"][0][14], 3)

    def test_heatmap_in_markdown(self):
        self.seed_heatmap()
        md = self.root / "report.md"
        code, _, _ = self.run_main([str(self.root), "--out", str(md)])
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## Screenshot Heatmap", text)
        self.assertIn("Mon", text)
        self.assertIn("█", text)

    def test_heatmap_in_html_report(self):
        self.seed_heatmap()
        page = self.root / "report.html"
        code, _, _ = self.run_main([str(self.root), "--html", str(page)])
        self.assertEqual(code, 0)
        text = page.read_text(encoding="utf-8")
        self.assertIn("Screenshot Heatmap", text)
        self.assertIn('class="heat"', text)
        self.assertIn("rgba(176,141,60", text)


# ---------------------------------------------------------------------------
# date filters (v0.6.0)
# ---------------------------------------------------------------------------

class DateFilterTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_range(self):
        write_file(self.root, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        write_file(self.root, "screenshot_b.png", make_png(11, 10),
                   datetime(2026, 6, 15, 12, 0))
        write_file(self.root, "screenshot_c.png", make_png(12, 10),
                   datetime(2026, 6, 30, 12, 0))
        write_file(self.root, "screenshot_d.png", make_png(13, 10),
                   datetime(2026, 7, 1, 12, 0))

    def test_since_until_boundaries_inclusive(self):
        self.seed_range()
        code, out, _ = self.run_main([
            str(self.root), "--since", "2026-06-15",
            "--until", "2026-06-30", "--json",
        ])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 2)
        self.assertEqual(
            data["date_filter"],
            {"since": "2026-06-15", "until": "2026-06-30"},
        )

    def test_date_filter_evidence_line(self):
        self.seed_range()
        code, out, _ = self.run_main(
            [str(self.root), "--since", "2026-06-15"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Date filter", out)
        self.assertIn("from Jun 15, 2026", out)

    def test_date_filter_can_yield_no_evidence(self):
        self.seed_range()
        code, out, _ = self.run_main(
            [str(self.root), "--since", "2027-01-01"]
        )
        self.assertEqual(code, 2)
        self.assertIn("No Evidence", out)

    def test_invalid_since_exits_2(self):
        self.seed_range()
        code, _, err = self.run_main(
            [str(self.root), "--since", "June 2026"]
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid date for --since", err)

    def test_invalid_until_exits_2(self):
        self.seed_range()
        code, _, err = self.run_main(
            [str(self.root), "--until", "2026-13-40"]
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid date for --until", err)

    def test_bare_year_still_works(self):
        write_file(self.root, "screenshot_old.png", make_png(10, 10),
                   datetime(2024, 5, 1, 12, 0))
        write_file(self.root, "screenshot_new.png", make_png(10, 10),
                   datetime(2026, 5, 1, 12, 0))
        code, out, _ = self.run_main(
            [str(self.root), "--since", "2025", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["date_filter"]["since"], "2025-01-01")

    def test_composes_with_year(self):
        write_file(self.root, "screenshot_a.png", make_png(10, 10),
                   datetime(2025, 7, 1, 12, 0))
        write_file(self.root, "screenshot_b.png", make_png(11, 10),
                   datetime(2026, 3, 1, 12, 0))
        write_file(self.root, "screenshot_c.png", make_png(12, 10),
                   datetime(2026, 7, 1, 12, 0))
        code, out, _ = self.run_main([
            str(self.root), "--year", "2026",
            "--since", "2026-06-01", "--json",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 1)

    def test_no_filter_json_nulls(self):
        self.seed_range()
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["date_filter"], {"since": None, "until": None}
        )


# ---------------------------------------------------------------------------
# new awards (v0.6.0)
# ---------------------------------------------------------------------------

class NewAward060Tests(TempDirCase):
    def test_time_traveler_future(self):
        files = [
            ("screenshot_now.png", make_png(10, 10),
             datetime(2026, 6, 1, 12, 0)),
            ("screenshot_future.png", make_png(10, 10),
             datetime(2100, 1, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertIsNotNone(stats["future_shot"])
        self.assertEqual(stats["future_shot"]["name"], "screenshot_future.png")
        self.assertIn("Time Traveler", award_names(stats))
        award = [
            a for a in sj.compute_awards(stats)
            if a["name"] == "Time Traveler"
        ][0]
        self.assertIn("screenshot_future.png", award["evidence"])
        self.assertIn("Jan 1, 2100", award["evidence"])

    def test_time_traveler_not_earned_without_future(self):
        files = [
            ("screenshot_a.png", make_png(10, 10),
             datetime(2026, 6, 1, 12, 0)),
            ("screenshot_b.png", make_png(10, 10),
             datetime(2026, 6, 2, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertIsNone(stats["future_shot"])
        self.assertNotIn("Time Traveler", award_names(stats))

    def test_ancient_relic(self):
        files = [
            ("screenshot_old.png", make_png(10, 10),
             datetime(2015, 1, 1, 12, 0)),
            ("screenshot_new.png", make_png(10, 10),
             datetime(2026, 6, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertIn("Ancient Relic", award_names(stats))
        award = [
            a for a in sj.compute_awards(stats)
            if a["name"] == "Ancient Relic"
        ][0]
        self.assertIn("screenshot_old.png", award["evidence"])
        self.assertIn("Jan 1, 2015", award["evidence"])

    def test_ancient_relic_not_earned_at_nine_years(self):
        files = [
            ("screenshot_old.png", make_png(10, 10),
             datetime(2017, 6, 1, 12, 0)),
            ("screenshot_new.png", make_png(10, 10),
             datetime(2026, 6, 1, 12, 0)),
        ]
        stats, _ = self.stats_for(files)
        self.assertNotIn("Ancient Relic", award_names(stats))


# ---------------------------------------------------------------------------
# filename locales (v0.6.0)
# ---------------------------------------------------------------------------

class LocaleDetectionTests(TempDirCase):
    def test_french(self):
        self.assertTrue(sj.name_looks_like_screenshot(
            "Capture d'écran du 2026-06-01.png"
        ))
        self.assertTrue(sj.name_looks_like_screenshot(
            "Capture d'ecran 2026.png"
        ))

    def test_spanish(self):
        self.assertTrue(sj.name_looks_like_screenshot(
            "Captura de pantalla 2026-06-01.png"
        ))

    def test_japanese(self):
        self.assertTrue(sj.name_looks_like_screenshot(
            "スクリーンショット 2026-06-01.png"
        ))

    def test_polish(self):
        self.assertTrue(sj.name_looks_like_screenshot(
            "Zrzut ekranu 2026-06-01.png"
        ))

    def test_locales_collected_without_all(self):
        names = [
            "Capture d'écran 2026-06-01.png",
            "Captura de pantalla 2026-06-01.png",
            "スクリーンショット 2026-06-01.png",
            "Zrzut ekranu 2026-06-01.png",
        ]
        for name in names:
            write_file(self.root, name, make_png(10, 10),
                       datetime(2026, 6, 1, 12, 0))
        shots, _recordings = sj.collect([self.root])
        self.assertEqual(len(shots), 4)


# ---------------------------------------------------------------------------
# badge (v0.6.0)
# ---------------------------------------------------------------------------

class BadgeTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def test_badge_written_and_path_printed(self):
        self.seed()
        badge = self.root / "badge.svg"
        code, out, _ = self.run_main([str(self.root), "--badge", str(badge)])
        self.assertEqual(code, 0)
        self.assertTrue(badge.is_file())
        text = badge.read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertIn("snap judgment", text)
        self.assertIn("Innocent Bystander", text)
        self.assertIn(str(badge), out)

    def test_badge_color_by_tier(self):
        self.assertIn("#4c1", sj.render_badge("Innocent Bystander"))
        self.assertIn(
            "#b60205", sj.render_badge("Unrepentant Screenshot Felon")
        )
        self.assertIn(
            "Unrepentant Screenshot Felon",
            sj.render_badge("Unrepentant Screenshot Felon"),
        )


# ---------------------------------------------------------------------------
# busiest hour window (v0.6.0)
# ---------------------------------------------------------------------------

class BusiestWindowTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_window(self):
        base = datetime(2026, 8, 14, 21, 3)
        for i, minutes in enumerate((0, 7, 17, 42, 56)):
            write_file(self.root, f"screenshot_w{i}.png", make_png(10, 10),
                       base + timedelta(minutes=minutes))
        write_file(self.root, "screenshot_far.png", make_png(10, 10),
                   datetime(2026, 1, 1, 9, 0))
        return base

    def test_busiest_window_stats(self):
        base = self.seed_window()
        shots, recordings = sj.collect([self.root])
        stats = sj.analyze(shots, recordings)
        self.assertEqual(stats["busiest_window"]["count"], 5)
        self.assertEqual(stats["busiest_window"]["start"], base)

    def test_busiest_window_evidence_line(self):
        self.seed_window()
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Busiest hour window", out)
        self.assertIn(
            "5 screenshots in the hour from Aug 14, 2026 21:03", out
        )

    def test_busiest_window_json(self):
        self.seed_window()
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        window = json.loads(out)["busiest_window"]
        self.assertEqual(window["count"], 5)
        self.assertTrue(window["start"].startswith("2026-08-14T21:03"))

    def test_busiest_window_line_hidden_below_three(self):
        write_file(self.root, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 8, 14, 21, 3))
        write_file(self.root, "screenshot_b.png", make_png(10, 10),
                   datetime(2026, 8, 14, 21, 30))
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("Busiest hour window", out)
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(json.loads(out)["busiest_window"]["count"], 2)


# ---------------------------------------------------------------------------
# install line on the share cards (v0.6.0)
# ---------------------------------------------------------------------------

class CardInstallLineTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, n=3):
        for i in range(n):
            write_file(self.root, f"screenshot_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))

    def test_svg_card_has_install_line(self):
        self.seed()
        svg = self.root / "card.svg"
        code, _, _ = self.run_main([str(self.root), "--svg", str(svg)])
        self.assertEqual(code, 0)
        text = svg.read_text(encoding="utf-8")
        self.assertIn("pip install snap-judgment", text)
        self.assertIn(
            "Judged from file metadata only. No pixels were read.", text
        )

    def test_html_card_has_install_line(self):
        self.seed()
        card = self.root / "card.html"
        code, _, _ = self.run_main([str(self.root), "--card", str(card)])
        self.assertEqual(code, 0)
        text = card.read_text(encoding="utf-8")
        self.assertIn("pip install snap-judgment", text)
        self.assertIn(
            "Judged from file metadata only. Pixels were never read.", text
        )

    def test_png_card_renders_and_is_deterministic(self):
        stats, _ = self.stats_for([
            (f"screenshot_{i}.png", make_png(100, 100),
             datetime(2026, 6, 1 + i, 12, 0)) for i in range(3)
        ])
        tier, sentence = sj.verdict_for(stats["count"])
        awards = sj.compute_awards(stats)
        first = sj.render_png(stats, tier, sentence, awards)
        second = sj.render_png(stats, tier, sentence, awards)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(b"\x89PNG\r\n\x1a\n"))


# ---------------------------------------------------------------------------
# config file (v0.7.0)
# ---------------------------------------------------------------------------

class ConfigTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self._old_cwd = os.getcwd()
        self.work = self.root / "work"  # stands in for the current directory
        self.work.mkdir()
        self.shots = self.root / "shots"
        self.shots.mkdir()

    def tearDown(self):
        os.chdir(self._old_cwd)
        super().tearDown()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, folder, n=3, when=None):
        when = when or datetime(2026, 6, 1, 12, 0)
        for i in range(n):
            write_file(folder, f"screenshot_{i}.png", make_png(100, 100),
                       when + timedelta(days=i))

    def write_config(self, folder, text):
        (Path(folder) / ".snapjudgment.toml").write_text(
            text, encoding="utf-8"
        )

    def toml_path(self, path):
        # TOML basic strings treat backslashes as escapes, so a Windows
        # path interpolated raw makes tomllib reject the whole config.
        return str(path).replace("\\", "\\\\")

    def test_config_supplies_folders_and_judge(self):
        self.seed(self.shots)
        self.write_config(
            self.work,
            f'folders = ["{self.toml_path(self.shots)}"]\njudge = "strict"\n',
        )
        os.chdir(self.work)
        code, out, _ = self.run_main(["--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["judge"], "strict")

    def test_cli_overrides_config(self):
        self.seed(self.shots, n=3)
        other = self.root / "other"
        other.mkdir()
        self.seed(other, n=5)
        self.write_config(
            self.work,
            f'folders = ["{self.toml_path(self.shots)}"]\njudge = "strict"\n',
        )
        os.chdir(self.work)
        code, out, _ = self.run_main(
            [str(other), "--judge", "lenient", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 5)  # the CLI folder, not the config's
        self.assertEqual(data["judge"], "lenient")  # the CLI judge wins

    def test_malformed_config_warns_and_run_succeeds(self):
        self.seed(self.shots)
        self.write_config(self.work, "this is not [ toml\n")
        os.chdir(self.work)
        code, out, err = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertIn(".snapjudgment.toml", err)
        self.assertEqual(json.loads(out)["count"], 3)

    def test_unknown_judge_in_config_warns_and_run_succeeds(self):
        self.seed(self.shots)
        self.write_config(self.work, 'judge = "grumpy"\n')
        os.chdir(self.work)
        code, out, err = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertEqual(json.loads(out)["judge"], "fair")

    def test_config_in_judged_folder_is_found(self):
        self.seed(self.shots)
        self.write_config(self.shots, 'judge = "lenient"\n')
        os.chdir(self.work)  # the working directory has no config of its own
        code, out, _ = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["judge"], "lenient")

    def test_cwd_config_wins_over_folder_config(self):
        self.seed(self.shots)
        self.write_config(self.work, 'judge = "strict"\n')
        self.write_config(self.shots, 'judge = "lenient"\n')
        os.chdir(self.work)
        code, out, _ = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["judge"], "strict")

    def test_folder_config_ignored_with_two_folders(self):
        self.seed(self.shots)
        other = self.root / "other"
        other.mkdir()
        self.seed(other, n=2)
        self.write_config(self.shots, 'judge = "lenient"\n')
        os.chdir(self.work)
        code, out, _ = self.run_main(
            [str(self.shots), str(other), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["judge"], "fair")

    def test_config_judge_file(self):
        self.seed(self.shots)
        pack = self.work / "pack.json"
        pack.write_text(
            json.dumps({"name": "The Test Judge", "sentences": {}}),
            encoding="utf-8",
        )
        self.write_config(self.work, f'judge_file = "{self.toml_path(pack)}"\n')
        os.chdir(self.work)
        code, out, _ = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["judge"], "The Test Judge")

    def test_config_missing_judge_file_warns_and_run_succeeds(self):
        self.seed(self.shots)
        self.write_config(
            self.work, f'judge_file = "{self.toml_path(self.work)}/no-such-pack.json"\n'
        )
        os.chdir(self.work)
        code, out, err = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertEqual(json.loads(out)["judge"], "fair")

    def test_config_plea_years(self):
        self.seed(self.shots)
        self.write_config(self.work, "plea_years = 5\n")
        os.chdir(self.work)
        code, out, _ = self.run_main(
            [str(self.shots), "--plea-bargain", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["plea_bargain"]["years"], 5)

    def test_config_fail_above(self):
        self.seed(self.shots, n=60)  # Petty Offender, above Innocent Bystander
        self.write_config(self.work, 'fail_above = "Innocent Bystander"\n')
        os.chdir(self.work)
        code, _, _ = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 1)

    def test_config_all(self):
        # files that are images but not recognizably screenshots
        for i in range(3):
            write_file(self.shots, f"photo_{i}.png", make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        os.chdir(self.work)
        code, _, _ = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 2)  # nothing the court recognizes
        self.write_config(self.work, "all = true\n")
        code, out, _ = self.run_main([str(self.shots), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 3)


class FlatTomlTests(unittest.TestCase):
    """The fallback parser used on Python 3.9/3.10 (no tomllib)."""

    def test_flat_subset(self):
        data = sj.parse_flat_toml(
            "# a comment\n"
            'judge = "strict" # trailing comment\n'
            "plea_years = 5\n"
            "all = true\n"
            "folders = [\"/a/b\", '/c']\n"
            'fail_above = "Repeat Offender"\n'
        )
        self.assertEqual(data["judge"], "strict")
        self.assertEqual(data["plea_years"], 5)
        self.assertIs(data["all"], True)
        self.assertEqual(data["folders"], ["/a/b", "/c"])
        self.assertEqual(data["fail_above"], "Repeat Offender")

    def test_flat_parser_rejects_garbage(self):
        with self.assertRaises(ValueError):
            sj.parse_flat_toml("this is not toml\n")

    def test_flat_parser_ignores_tables(self):
        data = sj.parse_flat_toml('[other]\njudge = "strict"\n')
        self.assertEqual(data, {})


def write_sized(folder, name, size, when=None):
    """A file with an exact size (sparse, via truncate) and mtime."""
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)
    if when is not None:
        ts = when.timestamp()
        os.utime(path, (ts, ts))
    return path


# ---------------------------------------------------------------------------
# the downloads docket (v0.9.0)
# ---------------------------------------------------------------------------

class DownloadsCourtTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self._old_cwd = os.getcwd()

    def tearDown(self):
        # Restore the cwd BEFORE the temp tree is removed: on Windows a
        # directory that is the process cwd cannot be deleted (WinError 32),
        # and addCleanup callbacks run after tearDown, too late.
        os.chdir(self._old_cwd)
        super().tearDown()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, specs):
        """specs: list of (name, size, datetime)."""
        for name, size, when in specs:
            write_sized(self.root, name, size, when)

    def filler(self, n, size=1000, start=datetime(2026, 6, 1, 12, 0)):
        return [
            (f"file_{i:04d}.dat", size, start + timedelta(days=i % 28))
            for i in range(n)
        ]

    def dl_stats(self):
        files = sj.collect_downloads([self.root])
        return sj.analyze_downloads(files)

    # -- tiers ---------------------------------------------------------

    def test_tier_boundaries(self):
        cases = {
            1: "Tidy Desk", 24: "Tidy Desk",
            25: "Mild Clutter", 99: "Mild Clutter",
            100: "Digital Junk Drawer", 249: "Digital Junk Drawer",
            250: "Landfill Adjacent", 499: "Landfill Adjacent",
            500: "Hoarding Situation", 999: "Hoarding Situation",
            1000: "Superfund Site", 5000: "Superfund Site",
        }
        for count, tier in cases.items():
            with self.subTest(count=count):
                self.assertEqual(sj.downloads_verdict_for(count)[0], tier)
        self.assertEqual(sj.downloads_verdict_for(0)[0], "No Evidence")

    def test_no_evidence_exit_2(self):
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads"]
        )
        self.assertEqual(code, 2)
        self.assertIn("THE DOWNLOADS DOCKET", out)
        self.assertIn("No Evidence", out)

    def test_terminal_report_shape(self):
        self.seed(self.filler(30))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads"]
        )
        self.assertEqual(code, 0)
        self.assertIn("SNAP JUDGMENT - THE DOWNLOADS DOCKET", out)
        self.assertIn("Mild Clutter", out)
        self.assertIn("30 files", out)
        self.assertIn(
            "Judged from file names, sizes, and dates only.", out
        )

    def test_nonrecursive(self):
        self.seed(self.filler(3))
        sub = self.root / "subfolder"
        for i in range(7):
            write_sized(sub, f"inner_{i}.dat", 10,
                        datetime(2026, 6, 1, 12, 0))
        self.assertEqual(self.dl_stats()["count"], 3)

    # -- awards ----------------------------------------------------------

    def award_keys(self, stats):
        return {a["key"] for a in sj.compute_downloads_awards(stats)}

    def test_award_installer_archaeologist(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([(f"tool{i}.exe", 100, base) for i in range(10)])
        self.assertIn(
            "installer_archaeologist", self.award_keys(self.dl_stats())
        )
        other = self.root / "nine"
        for i in range(9):
            write_sized(other, f"tool{i}.exe", 100, base)
        stats = sj.analyze_downloads(sj.collect_downloads([other]))
        self.assertNotIn(
            "installer_archaeologist", self.award_keys(stats)
        )

    def test_award_zip_hoarder(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([(f"pack{i}.zip", 100, base) for i in range(15)])
        self.assertIn("zip_hoarder", self.award_keys(self.dl_stats()))

    def test_award_ancient_relic_and_time_capsule(self):
        old = datetime(2022, 1, 1, 12, 0)
        new = datetime(2026, 6, 1, 12, 0)
        specs = [(f"old{i}.txt", 100, old) for i in range(6)]
        specs += [(f"new{i}.txt", 100, new) for i in range(4)]
        self.seed(specs)
        keys = self.award_keys(self.dl_stats())
        self.assertIn("ancient_relic", keys)      # oldest is 4+ years old
        self.assertIn("time_capsule", keys)       # 60% over a year old

    def test_award_gigabyte_landfill(self):
        self.seed([("huge.iso", 5 * 1024 ** 3 + 1,
                    datetime(2026, 6, 1, 12, 0))])
        self.assertIn(
            "gigabyte_landfill", self.award_keys(self.dl_stats())
        )

    def test_award_still_seeding_and_receipt_keeper(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([
            ("linux.torrent", 100, base),
            ("invoice-march.pdf", 100, base),
            ("plain.txt", 100, base),
        ])
        keys = self.award_keys(self.dl_stats())
        self.assertIn("still_seeding", keys)
        self.assertIn("receipt_keeper_dx", keys)

    def test_no_awards_on_a_clean_desk(self):
        self.seed(self.filler(5))
        self.assertEqual(sj.compute_downloads_awards(self.dl_stats()), [])

    # -- duplicate downloads --------------------------------------------

    def test_duplicate_grouping_chains(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([
            ("setup.exe", 500, base),
            ("setup (1).exe", 500, base),
            ("setup (2).exe", 500, base),
            ("report.pdf", 300, base),
            ("report copy.pdf", 300, base),
            ("unique.txt", 100, base),
        ])
        stats = self.dl_stats()
        self.assertEqual(stats["duplicate_groups"], 2)
        self.assertEqual(stats["duplicate_extra"], 3)
        self.assertEqual(stats["duplicate_extra_bytes"], 500 * 2 + 300)
        keys = self.award_keys(stats)
        self.assertNotIn("double_downloader", keys)  # needs 5 extras

    def test_double_downloader_award(self):
        base = datetime(2026, 6, 1, 12, 0)
        specs = []
        for stem in ("a", "b", "c"):
            specs.append((f"{stem}.exe", 100, base))
            specs.append((f"{stem} (1).exe", 100, base))
            specs.append((f"{stem} (2).exe", 100, base))
        self.seed(specs)
        stats = self.dl_stats()
        self.assertEqual(stats["duplicate_extra"], 6)
        self.assertIn("double_downloader", self.award_keys(stats))

    # -- outputs ---------------------------------------------------------

    def test_json_shape(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([(f"tool{i}.exe", 100, base) for i in range(12)])
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["court"], "downloads")
        self.assertEqual(data["verdict"], "Tidy Desk")
        self.assertEqual(data["count"], 12)
        self.assertEqual(data["installers"]["count"], 12)
        self.assertEqual(data["extensions"][0]["ext"], ".exe")
        self.assertEqual(
            data["awards"][0]["key"], "installer_archaeologist"
        )

    def test_screenshots_json_gains_court(self):
        write_file(self.root, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["court"], "screenshots")

    def test_markdown_out(self):
        self.seed(self.filler(30))
        md = self.root / "docket.md"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--out", str(md)]
        )
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("The Downloads Docket", text)
        self.assertIn("Mild Clutter", text)
        self.assertIn("Judged from file names, sizes, and dates only.",
                      text)

    def test_oneline(self):
        self.seed(self.filler(30))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--oneline"]
        )
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("Mild Clutter", lines[0])
        self.assertIn("30 files", lines[0])

    # -- fail-above, judges, intensity -----------------------------------

    def test_fail_above_downloads(self):
        self.seed(self.filler(100))  # Digital Junk Drawer
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--fail-above", "Mild Clutter"]
        )
        self.assertEqual(code, 1)
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--fail-above", "Digital Junk Drawer"]
        )
        self.assertEqual(code, 0)
        code, _, err = self.run_main(
            [str(self.root), "--court", "downloads",
             "--fail-above", "Repeat Offender"]  # a screenshots tier
        )
        self.assertEqual(code, 2)
        self.assertIn("Superfund Site", err)

    def test_unsupported_flags_error(self):
        self.seed(self.filler(3))
        for flag, value in (("--vs-year", "2025"),
                            ("--plea-bargain", None),
                            ("--judge-file", "pack.json")):
            argv = [str(self.root), "--court", "downloads", flag]
            if value is not None:
                argv.append(value)
            with self.subTest(flag=flag):
                code, _, err = self.run_main(argv)
                self.assertEqual(code, 2)
                self.assertIn(flag, err)
                self.assertIn("downloads court", err)

    def test_judge_strict_applies(self):
        self.seed(self.filler(30))  # Mild Clutter
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--judge", "strict"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Hanging Judge", out)
        self.assertIn(
            "Sentence: the installers are deleted today. "
            "Not tomorrow. Today.", out,
        )

    def test_intensity_commentary_downloads(self):
        base = datetime(2026, 6, 1, 12, 0)
        specs = []
        for stem in ("a", "b", "c"):
            specs.append((f"{stem}.exe", 100, base))
            specs.append((f"{stem} (1).exe", 100, base))
            specs.append((f"{stem} (2).exe", 100, base))
        self.seed(specs)
        code, gentle, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--intensity", "gentle"]
        )
        self.assertEqual(code, 0)
        code, roast, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertIn(
            "COMMENTARY: The court found 6 files that were downloaded "
            "more than once", gentle,
        )
        self.assertIn(
            "COMMENTARY: 6 duplicate downloads. The court has now "
            "reviewed the same installer multiple times", roast,
        )
        self.assertNotEqual(gentle, roast)
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--intensity", "roast", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertIn("duplicate downloads",
                      json.loads(out)["commentary"])

    def test_all_flag_accepted_and_ignored(self):
        self.seed(self.filler(3))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--all"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Tidy Desk", out)

    # -- default folder, history, config ---------------------------------

    def test_default_folder_is_home_downloads(self):
        downloads = self.root / "Downloads"
        for i in range(3):
            write_sized(downloads, f"f{i}.dat", 10,
                        datetime(2026, 6, 1, 12, 0))
        with mock.patch.object(sj.Path, "home", return_value=self.root):
            code, out, _ = self.run_main(["--court", "downloads"])
        self.assertEqual(code, 0)
        self.assertIn("Tidy Desk", out)

    def test_default_folder_missing_errors(self):
        with mock.patch.object(sj.Path, "home", return_value=self.root):
            code, _, err = self.run_main(["--court", "downloads"])
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)

    def test_history_records_downloads_hearing(self):
        hist_file = self.root / "court" / "history.jsonl"
        patcher = mock.patch.object(
            sj, "history_path", return_value=hist_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.seed(self.filler(3))
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads"]
        )
        self.assertEqual(code, 0)
        records = [
            json.loads(line)
            for line in hist_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["court"], "downloads")
        self.assertEqual(records[0]["verdict"], "Tidy Desk")
        self.assertEqual(records[0]["count"], 3)
        code, out, _ = self.run_main(["--history"])
        self.assertEqual(code, 0)
        self.assertIn("3 files", out)

    def test_config_court_key(self):
        work = self.root / "work"
        work.mkdir()
        shots = self.root / "shots"
        shots.mkdir()
        write_file(shots, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        old_cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(os.chdir, old_cwd)
        (work / ".snapjudgment.toml").write_text(
            'court = "downloads"\n', encoding="utf-8"
        )
        code, out, _ = self.run_main([str(shots)])
        self.assertEqual(code, 0)
        self.assertIn("THE DOWNLOADS DOCKET", out)

    def test_config_court_invalid_warns_and_ignores(self):
        work = self.root / "work"
        work.mkdir()
        shots = self.root / "shots"
        shots.mkdir()
        write_file(shots, "screenshot_a.png", make_png(10, 10),
                   datetime(2026, 6, 1, 12, 0))
        old_cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(os.chdir, old_cwd)
        (work / ".snapjudgment.toml").write_text(
            'court = "banana"\n', encoding="utf-8"
        )
        code, out, err = self.run_main([str(shots)])
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertIn("The Court of Screenshot Affairs", out)


# ---------------------------------------------------------------------------
# downloads docket citizenship (v0.10.0): cards, badge, HTML report,
# rematch, year + wrapped kit, CSV, date filters
# ---------------------------------------------------------------------------

class DownloadsCitizenshipTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self._old_cwd = os.getcwd()
        self.hist_file = self.root / "court" / "history.jsonl"
        patcher = mock.patch.object(
            sj, "history_path", return_value=self.hist_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self._old_cwd)
        super().tearDown()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, specs):
        """specs: list of (name, size, datetime)."""
        for name, size, when in specs:
            write_sized(self.root, name, size, when)

    def filler(self, n, size=1000, start=datetime(2026, 6, 1, 12, 0)):
        return [
            (f"file_{i:04d}.dat", size, start + timedelta(days=i % 28))
            for i in range(n)
        ]

    def dl_stats(self):
        files = sj.collect_downloads([self.root])
        return sj.analyze_downloads(files)

    def seed_record(self, count, total_bytes, verdict,
                    when="2026-06-03T12:00:00", court="downloads",
                    folders=None):
        self.hist_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": when,
            "folders": (
                folders if folders is not None else [str(self.root)]
            ),
            "count": count,
            "total_bytes": total_bytes,
            "verdict": verdict,
            "judge": "fair",
        }
        if court is not None:
            record["court"] = court
        with open(self.hist_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    # -- cards -----------------------------------------------------------

    def test_png_card_written_and_valid(self):
        self.seed(self.filler(30))
        png = self.root / "card.png"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--png", str(png)]
        )
        self.assertEqual(code, 0)
        data = png.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(int.from_bytes(data[16:20], "big"), 1200)
        self.assertEqual(int.from_bytes(data[20:24], "big"), 630)

    def test_png_render_is_deterministic(self):
        self.seed(self.filler(30))
        stats = self.dl_stats()
        tier, sentence = sj.downloads_verdict_for(stats["count"])
        awards = sj.compute_downloads_awards(stats)
        first = sj.render_downloads_png(stats, tier, sentence, awards)
        second = sj.render_downloads_png(stats, tier, sentence, awards)
        self.assertEqual(first, second)
        self.assertEqual(first[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(first), 1000)

    def test_svg_card_shows_docket_header_and_tier(self):
        self.seed(self.filler(30))
        svg = self.root / "card.svg"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--svg", str(svg)]
        )
        self.assertEqual(code, 0)
        text = svg.read_text(encoding="utf-8")
        self.assertIn("THE DOWNLOADS DOCKET", text)
        self.assertIn("Mild Clutter", text)
        self.assertIn("FILES", text)
        self.assertIn("pip install snap-judgment", text)

    def test_svg_card_headline_is_duplicate_waste(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([("setup.exe", 5000, base), ("setup (1).exe", 5000, base)])
        svg = sj.render_downloads_svg(
            self.dl_stats(), "Tidy Desk", "Sentence: x.", []
        )
        self.assertIn("DUPLICATE WASTE", svg)
        self.assertIn("4.9 KB", svg)

    def test_svg_card_headline_is_installers_without_waste(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([(f"tool{i}.exe", 2000, base) for i in range(12)])
        svg = sj.render_downloads_svg(
            self.dl_stats(), "Tidy Desk", "Sentence: x.", []
        )
        self.assertIn("INSTALLERS", svg)
        self.assertNotIn("DUPLICATE WASTE", svg)

    def test_html_card_shows_tier_and_files(self):
        self.seed(self.filler(30))
        card = self.root / "card.html"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--card", str(card)]
        )
        self.assertEqual(code, 0)
        text = card.read_text(encoding="utf-8")
        self.assertIn("The Downloads Docket", text)
        self.assertIn("Mild Clutter", text)
        self.assertIn(">Files<", text)
        self.assertIn("pip install snap-judgment", text)

    # -- badge -------------------------------------------------------------

    def test_badge_written_shows_tier_and_path_printed(self):
        self.seed(self.filler(30))
        badge = self.root / "badge.svg"
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--badge", str(badge)]
        )
        self.assertEqual(code, 0)
        text = badge.read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertIn("snap judgment", text)
        self.assertIn("Mild Clutter", text)
        self.assertIn(str(badge), out)

    def test_badge_colors_follow_the_docket_ladder(self):
        self.assertIn("#4c1", sj.render_downloads_badge("Tidy Desk"))
        self.assertIn(
            "#b60205", sj.render_downloads_badge("Superfund Site")
        )
        self.assertIn(
            "Superfund Site", sj.render_downloads_badge("Superfund Site")
        )

    # -- HTML report -------------------------------------------------------

    def test_html_report_shows_evidence_values(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([(f"tool{i}.exe", 2000, base) for i in range(12)])
        report = self.root / "report.html"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--html", str(report)]
        )
        self.assertEqual(code, 0)
        text = report.read_text(encoding="utf-8")
        self.assertIn("The Downloads Docket", text)
        self.assertIn("Tidy Desk", text)
        self.assertIn("Installers", text)
        self.assertIn("12 (23.4 KB)", text)
        self.assertIn("tool0.exe", text)
        self.assertIn("No file was opened.", text)

    # -- rematch -----------------------------------------------------------

    def test_rematch_worsened_with_size_delta(self):
        self.seed_record(3, 300, "Tidy Desk")
        self.seed(self.filler(30, size=1000))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("REMATCH", out)
        self.assertIn(
            "Since your last hearing (Jun 3, 2026): +27 files "
            "(+29.0 KB).", out,
        )
        self.assertIn(
            "The verdict has worsened from Tidy Desk to Mild Clutter.",
            out,
        )

    def test_rematch_improved(self):
        self.seed_record(100, 100_000, "Digital Junk Drawer")
        self.seed(self.filler(3, size=1000))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("-97 files (-94.7 KB)", out)
        self.assertIn(
            "The verdict has improved from Digital Junk Drawer "
            "to Tidy Desk.", out,
        )

    def test_rematch_no_prior_hearing(self):
        self.seed(self.filler(3))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("No prior hearing on record for these folders.", out)

    def test_rematch_ignores_screenshots_court_records(self):
        # a screenshots-court record for the same folders (no court
        # field, the pre-0.9.0 shape) is not a downloads hearing
        self.seed_record(3, 300, "Innocent Bystander", court=None)
        self.seed(self.filler(3))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("No prior hearing on record for these folders.", out)

    def test_rematch_json(self):
        self.seed_record(3, 300, "Tidy Desk")
        self.seed(self.filler(5, size=1000))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--rematch", "--json"]
        )
        self.assertEqual(code, 0)
        rematch = json.loads(out)["rematch"]
        self.assertEqual(rematch["previous_count"], 3)
        self.assertEqual(rematch["delta"], 2)
        self.assertEqual(rematch["previous_bytes"], 300)
        self.assertEqual(rematch["bytes_delta"], 4700)
        self.assertEqual(rematch["previous_tier"], "Tidy Desk")
        self.assertEqual(rematch["date"], "2026-06-03")

    def test_rematch_json_null_without_record(self):
        self.seed(self.filler(3))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--rematch", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["rematch"])

    def test_rematch_reads_record_before_own_append(self):
        self.seed(self.filler(3))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("No prior hearing on record", out)
        write_sized(self.root, "extra_a.dat", 1000,
                    datetime(2026, 7, 2, 12, 0))
        write_sized(self.root, "extra_b.dat", 1000,
                    datetime(2026, 7, 3, 12, 0))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("No prior hearing on record", out)
        self.assertIn("The verdict is unchanged", out)

    def test_rematch_markdown_section(self):
        self.seed_record(3, 300, "Tidy Desk")
        self.seed(self.filler(5, size=1000))
        md = self.root / "docket.md"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--rematch", "--out", str(md)]
        )
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("## Rematch", text)
        self.assertIn("+2 files (+4.6 KB)", text)

    # -- year filter ---------------------------------------------------------

    def test_year_filter_scopes_report(self):
        specs = [(f"y25_{i}.dat", 100, datetime(2025, 6, 1, 12, 0))
                 for i in range(5)]
        specs += [(f"y26_{i}.dat", 100, datetime(2026, 6, 1, 12, 0))
                  for i in range(3)]
        self.seed(specs)
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--year", "2025"]
        )
        self.assertEqual(code, 0)
        self.assertIn("5 files", out)
        self.assertIn("Scope: year 2025", out)

    def test_year_json_key_only_when_filtered(self):
        self.seed(self.filler(3))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("year", json.loads(out))
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--year", "2026", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["count"], 3)

    def test_year_invalid_errors(self):
        self.seed(self.filler(3))
        code, _, err = self.run_main(
            [str(self.root), "--court", "downloads", "--year", "26"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--year must be a four-digit year", err)

    # -- wrapped kit ---------------------------------------------------------

    def test_wrapped_kit_writes_svg_and_post(self):
        year = datetime.now().year
        base = datetime(year, 6, 1, 12, 0)
        self.seed([(f"file_{i:04d}.dat", 1000,
                    base + timedelta(days=i % 28)) for i in range(30)])
        kit = self.root / "kit"
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--wrapped-kit", str(kit)]
        )
        self.assertEqual(code, 0)
        svg = kit / f"wrapped-{year}.svg"
        post = kit / "post.txt"
        self.assertTrue(svg.is_file())
        self.assertTrue(post.is_file())
        self.assertIn(str(svg), out)
        self.assertIn(str(post), out)
        self.assertIn(
            "THE DOWNLOADS DOCKET", svg.read_text(encoding="utf-8")
        )
        text = post.read_text(encoding="utf-8")
        self.assertIn("STANDARD POST", text)
        self.assertIn(f"My {year} in downloads", text)
        lines = text.splitlines()
        short = lines[lines.index("SHORT POST") + 1]
        self.assertLessEqual(len(short), 280)
        self.assertIn("30 files", short)
        self.assertIn("Mild Clutter", short)

    def test_wrapped_kit_with_explicit_year(self):
        base = datetime(2025, 6, 1, 12, 0)
        self.seed([(f"file_{i:04d}.dat", 1000,
                    base + timedelta(days=i % 28)) for i in range(12)])
        kit = self.root / "kit"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--year", "2025", "--wrapped-kit", str(kit)]
        )
        self.assertEqual(code, 0)
        self.assertTrue((kit / "wrapped-2025.svg").is_file())
        text = (kit / "post.txt").read_text(encoding="utf-8")
        self.assertIn("My 2025 in downloads", text)

    def test_wrapped_kit_empty_year_errors(self):
        self.seed(self.filler(12))
        kit = self.root / "kit"
        code, _, err = self.run_main(
            [str(self.root), "--court", "downloads",
             "--year", "2024", "--wrapped-kit", str(kit)]
        )
        self.assertEqual(code, 2)
        self.assertIn("no files from 2024 to wrap", err)
        self.assertFalse(kit.exists())

    # -- CSV ---------------------------------------------------------------

    def test_csv_header_and_categories(self):
        base = datetime(2026, 6, 1, 12, 0)
        self.seed([
            ("setup.exe", 500, base),
            ("pack.zip", 300, base + timedelta(days=1)),
            ("notes.txt", 100, base + timedelta(days=2)),
            ("driver.msi", 700, base + timedelta(days=3)),
        ])
        target = self.root / "files.csv"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--csv", str(target)]
        )
        self.assertEqual(code, 0)
        with open(target, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(
            rows[0],
            ["name", "size_bytes", "modified", "extension", "category"],
        )
        by_name = {row[0]: row for row in rows[1:]}
        self.assertEqual(by_name["setup.exe"][1], "500")
        self.assertEqual(by_name["setup.exe"][3], ".exe")
        self.assertEqual(by_name["setup.exe"][4], "installer")
        self.assertEqual(by_name["driver.msi"][4], "installer")
        self.assertEqual(by_name["pack.zip"][4], "archive")
        self.assertEqual(by_name["notes.txt"][4], "other")

    # -- date filters --------------------------------------------------------

    def test_since_until_filters(self):
        self.seed([
            ("a.dat", 100, datetime(2025, 12, 31, 12, 0)),
            ("b.dat", 100, datetime(2026, 6, 1, 12, 0)),
            ("c.dat", 100, datetime(2026, 6, 10, 12, 0)),
            ("d.dat", 100, datetime(2026, 6, 20, 12, 0)),
        ])
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--since", "2026-06-10", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 2)
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--until", "2026-06-10", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 3)
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--since", "2026", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 3)

    def test_since_invalid_errors(self):
        self.seed(self.filler(3))
        code, _, err = self.run_main(
            [str(self.root), "--court", "downloads", "--since", "banana"]
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid date for --since", err)

    # -- default output is unchanged -----------------------------------------

    def test_default_terminal_output_matches_demo_example(self):
        import importlib.util

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 19, 12, 0, 0)

        repo = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "make_downloads_demo", repo / "tools" / "make_downloads_demo.py"
        )
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        folder = self.root / "Downloads"
        folder.mkdir()
        demo.build_fixture(folder)
        with mock.patch.object(sj, "datetime", FrozenDateTime):
            code, out, _ = self.run_main(
                [str(folder), "--court", "downloads"]
            )
        self.assertEqual(code, 0)
        expected = (
            repo / "examples" / "downloads-demo-output.txt"
        ).read_text(encoding="utf-8")
        self.assertEqual(out, expected)


# ---------------------------------------------------------------------------
# judge pack award overrides (v0.9.0)
# ---------------------------------------------------------------------------

class AwardOverrideTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_night(self):
        """30 night screenshots: 8 in one minute (a burst), then one a
        day for 22 more days (a streak). Night Owl, Burst Mode, and
        Screenshot Streak all fire."""
        base = datetime(2026, 6, 1, 23, 0, 0)
        for i in range(8):
            write_file(self.root, f"screenshot_burst_{i}.png",
                       make_png(100, 100), base + timedelta(seconds=5 * i))
        for i in range(22):
            write_file(self.root, f"screenshot_night_{i}.png",
                       make_png(100, 100),
                       base + timedelta(days=i + 1, minutes=30))

    def write_pack(self, pack, name="pack.json"):
        path = self.root / name
        path.write_text(json.dumps(pack), encoding="utf-8")
        return path

    def awards_by_key(self, argv):
        code, out, err = self.run_main(argv + ["--json"])
        self.assertEqual(code, 0, err)
        return {a["key"]: a for a in json.loads(out)["awards"]}

    def test_name_only_override_keeps_builtin_evidence(self):
        self.seed_night()
        pack = self.write_pack({
            "name": "The Renamer",
            "awards": {"night_owl": {"name": "Hoot Hoot"}},
        })
        awards = self.awards_by_key(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(awards["night_owl"]["name"], "Hoot Hoot")
        self.assertIn(
            "30 screenshots between 10 PM and 5 AM",
            awards["night_owl"]["evidence"],
        )

    def test_evidence_only_override_keeps_builtin_name(self):
        self.seed_night()
        pack = self.write_pack({
            "name": "The Rewriter",
            "awards": {
                "night_owl": {
                    "evidence": "{night_count} hoots at "
                                "{night_share:.1f}% after dark.",
                },
            },
        })
        awards = self.awards_by_key(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(awards["night_owl"]["name"], "Night Owl")
        self.assertEqual(
            awards["night_owl"]["evidence"],
            "30 hoots at 100.0% after dark.",
        )

    def test_both_overridden(self):
        self.seed_night()
        pack = self.write_pack({
            "name": "The Full Rewrite",
            "awards": {
                "burst_mode": {
                    "name": "Rapid Fire",
                    "evidence": "{burst_record} shots in a minute. "
                                "A personal record, regrettably.",
                },
            },
        })
        awards = self.awards_by_key(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(awards["burst_mode"]["name"], "Rapid Fire")
        self.assertEqual(
            awards["burst_mode"]["evidence"],
            "8 shots in a minute. A personal record, regrettably.",
        )

    def test_unknown_award_key_warns_and_is_ignored(self):
        self.seed_night()
        pack = self.write_pack({
            "name": "The Optimist",
            "awards": {
                "best_hair": {"name": "Great Hair"},
                "night_owl": {"name": "Hoot Hoot"},
            },
        })
        code, out, err = self.run_main(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(code, 0)
        self.assertIn("unknown award", err)
        self.assertIn("best_hair", err)
        self.assertIn("Hoot Hoot", out)

    def test_malformed_awards_exit_2(self):
        self.seed_night()
        for bad in ({"name": "X", "awards": []},
                    {"name": "X", "awards": {"night_owl": {"name": 5}}},
                    {"name": "X", "awards": {"night_owl": {}}}):
            with self.subTest(bad=bad):
                pack = self.write_pack(bad, name="bad.json")
                code, _, err = self.run_main(
                    [str(self.root), "--judge-file", str(pack)]
                )
                self.assertEqual(code, 2)
                self.assertIn("awards", err)

    def test_pack_without_awards_unchanged(self):
        self.seed_night()
        pack = self.write_pack({"name": "The Plain Judge"})
        awards = self.awards_by_key(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(awards["night_owl"]["name"], "Night Owl")
        self.assertEqual(awards["burst_mode"]["name"], "Burst Mode")
        self.assertEqual(
            awards["screenshot_streak"]["name"], "Screenshot Streak"
        )

    def test_critic_pack_renames_in_a_real_run(self):
        self.seed_night()
        pack = (
            Path(__file__).resolve().parent.parent
            / "judges" / "judge-critic.json"
        )
        code, out, _ = self.run_main(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Critic", out)
        self.assertIn("Nocturne in a Minor Key", out)
        self.assertIn("Rapid Montage", out)
        self.assertIn("The Long Run", out)
        awards = self.awards_by_key(
            [str(self.root), "--judge-file", str(pack)]
        )
        self.assertEqual(
            awards["night_owl"]["name"], "Nocturne in a Minor Key"
        )
        self.assertIn("shot entirely at night",
                      awards["night_owl"]["evidence"])


# ---------------------------------------------------------------------------
# the cleanup order (v0.9.0)
# ---------------------------------------------------------------------------

class CleanupOrderTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_plea_folder(self):
        """6 screenshots: 1 newest, a duplicate trio, 2 old (2023)."""
        png = make_png(100, 100)
        write_file(self.root, "screenshot_new.png", make_png(200, 200),
                   datetime(2026, 6, 1, 12, 0))
        for i in range(3):
            write_file(self.root, f"screenshot_dup{i}.png", png,
                       datetime(2026, 5, 1, 12, 0) + timedelta(days=i))
        write_file(self.root, "screenshot_old1.png",
                   make_png(50, 50, pad=10), datetime(2023, 1, 1, 12, 0))
        write_file(self.root, "screenshot_old2.png",
                   make_png(60, 60, pad=20), datetime(2023, 6, 1, 12, 0))

    def expected_plea(self, years=2):
        shots, _recordings = sj.collect([self.root])
        return sj.compute_plea_bargain(shots, years)

    def list_lines(self, path):
        return path.read_text(encoding="utf-8").splitlines()

    def test_exact_contents_and_header(self):
        self.seed_plea_folder()
        plea = self.expected_plea()
        target = self.root / "cleanup.txt"
        code, _, _ = self.run_main(
            [str(self.root), "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        lines = self.list_lines(target)
        header = [line for line in lines if line.startswith("#")]
        paths = [line for line in lines if not line.startswith("#")]
        self.assertTrue(header)
        self.assertIn(
            f"{plea['removed_count']} files", header[1]
        )
        self.assertIn(f"{plea['removed_bytes']} bytes", header[1])
        self.assertEqual(paths, plea["removed_paths"])
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), 4)

    def test_terminal_line(self):
        self.seed_plea_folder()
        target = self.root / "cleanup.txt"
        code, out, _ = self.run_main(
            [str(self.root), "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        self.assertIn("CLEANUP ORDER: 4 files (", out)
        self.assertIn(f"listed in {target}", out)
        self.assertIn("The court does not swing the axe itself.", out)
        self.assertNotIn("THE PLEA BARGAIN", out)

    def test_json_cleanup_order(self):
        self.seed_plea_folder()
        plea = self.expected_plea()
        target = self.root / "cleanup.txt"
        code, out, _ = self.run_main(
            [str(self.root), "--cleanup-list", str(target), "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(
            data["cleanup_order"],
            {"file": str(target), "count": plea["removed_count"],
             "bytes": plea["removed_bytes"]},
        )
        self.assertNotIn("plea_bargain", data)

    def test_no_delete_invariant(self):
        self.seed_plea_folder()
        before = sorted(p.name for p in self.root.iterdir())
        target = self.root / "cleanup.txt"
        code, _, _ = self.run_main(
            [str(self.root), "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        for name in before:
            self.assertTrue((self.root / name).is_file(), name)

    def test_agrees_with_plea_bargain(self):
        self.seed_plea_folder()
        target = self.root / "cleanup.txt"
        code, out, _ = self.run_main(
            [str(self.root), "--plea-bargain",
             "--cleanup-list", str(target), "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(
            data["cleanup_order"]["count"],
            data["plea_bargain"]["removed_count"],
        )
        self.assertEqual(
            data["cleanup_order"]["bytes"],
            data["plea_bargain"]["removed_bytes"],
        )
        paths = [
            line for line in self.list_lines(target)
            if not line.startswith("#")
        ]
        self.assertEqual(len(paths), data["plea_bargain"]["removed_count"])

    def test_plea_years_respected(self):
        self.seed_plea_folder()
        target = self.root / "cleanup.txt"
        code, out, _ = self.run_main(
            [str(self.root), "--cleanup-list", str(target),
             "--plea-years", "0", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["cleanup_order"]["count"], 5)

    def test_cleanup_in_downloads_court_errors(self):
        target = self.root / "cleanup.txt"
        code, _, err = self.run_main(
            [str(self.root), "--court", "downloads",
             "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 2)
        self.assertIn("--cleanup-list", err)
        self.assertFalse(target.exists())


# ---------------------------------------------------------------------------
# roast intensity and court commentary (v0.8.0)
# ---------------------------------------------------------------------------

class IntensityTests(TempDirCase):
    GENTLE_NIGHT = (
        "The court sees 6 screenshots taken at night and simply notes "
        "the defendant keeps late hours."
    )
    ROAST_NIGHT = (
        "6 screenshots after 10 PM. The court has reviewed the "
        "defendant's sleep schedule and found it guilty too."
    )

    def setUp(self):
        super().setUp()
        self._old_cwd = os.getcwd()
        self.work = self.root / "work"  # stands in for the current directory
        self.work.mkdir()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self._old_cwd)
        super().tearDown()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_night(self, night=6, day=4):
        """A night-heavy folder: night share 60%, nothing else notable."""
        base = datetime(2026, 6, 1)
        for i in range(night):
            write_file(self.root, f"screenshot_night_{i}.png",
                       make_png(100, 100),
                       base.replace(hour=23) + timedelta(days=i))
        for i in range(day):
            write_file(self.root, f"screenshot_day_{i}.png",
                       make_png(100, 100),
                       base.replace(hour=12) + timedelta(days=i))

    def write_config(self, text):
        (self.work / ".snapjudgment.toml").write_text(
            text, encoding="utf-8"
        )

    # -- band parsing ------------------------------------------------

    def test_band_boundaries(self):
        cases = {0: 0, 19: 0, 20: 1, 39: 1, 40: 2, 59: 2,
                 60: 3, 79: 3, 80: 4, 100: 4}
        for value, level in cases.items():
            self.assertEqual(sj.parse_intensity(value), level)
            self.assertEqual(sj.parse_intensity(str(value)), level)

    def test_band_names_case_insensitive(self):
        self.assertEqual(sj.parse_intensity("gentle"), 0)
        self.assertEqual(sj.parse_intensity("Kind"), 1)
        self.assertEqual(sj.parse_intensity("HONEST"), 2)
        self.assertEqual(sj.parse_intensity(" spicy "), 3)
        self.assertEqual(sj.parse_intensity("roast"), 4)
        self.assertEqual(sj.parse_intensity("full roast"), 4)
        self.assertEqual(sj.parse_intensity("Full-Roast"), 4)

    def test_parse_intensity_invalid(self):
        for raw in ("banana", "mild", "", "50.5", "101", "-1", 101, -1,
                    True):
            with self.assertRaises(sj.SnapError):
                sj.parse_intensity(raw)

    def test_intensity_flag_invalid_exits_2(self):
        self.seed_night()
        code, out, err = self.run_main(
            [str(self.root), "--intensity", "banana"]
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid intensity", err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    # -- opt-in only ---------------------------------------------------

    def test_commentary_absent_by_default_terminal(self):
        self.seed_night()
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("COMMENTARY", out)

    def test_commentary_absent_by_default_json(self):
        self.seed_night()
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("commentary", json.loads(out))

    def test_cards_and_oneline_unchanged_by_intensity(self):
        self.seed_night()
        plain_card = self.root / "plain.html"
        roast_card = self.root / "roast.html"
        code, _, _ = self.run_main([str(self.root), "--card", str(plain_card)])
        self.assertEqual(code, 0)
        code, _, _ = self.run_main(
            [str(self.root), "--card", str(roast_card),
             "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(plain_card.read_bytes(), roast_card.read_bytes())
        _, plain_line, _ = self.run_main([str(self.root), "--oneline"])
        _, roast_line, _ = self.run_main(
            [str(self.root), "--oneline", "--intensity", "roast"]
        )
        self.assertEqual(plain_line, roast_line)

    # -- presence and placement ----------------------------------------

    def test_commentary_terminal_after_sentence(self):
        self.seed_night()
        code, out, _ = self.run_main(
            [str(self.root), "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertIn(f"COMMENTARY: {self.ROAST_NIGHT}", out)
        self.assertLess(out.index("THE SENTENCE"),
                        out.index("COMMENTARY:"))

    def test_commentary_json(self):
        self.seed_night()
        code, out, _ = self.run_main(
            [str(self.root), "--json", "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["commentary"], self.ROAST_NIGHT)

    def test_commentary_markdown(self):
        self.seed_night()
        report = self.root / "report.md"
        code, _, _ = self.run_main(
            [str(self.root), "--out", str(report), "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        text = report.read_text(encoding="utf-8")
        self.assertIn("## Court Commentary", text)
        self.assertIn(self.ROAST_NIGHT, text)
        self.assertLess(text.index("## The Sentence"),
                        text.index("## Court Commentary"))

    def test_commentary_html_report(self):
        self.seed_night()
        page = self.root / "report.html"
        code, _, _ = self.run_main(
            [str(self.root), "--html", str(page), "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        text = page.read_text(encoding="utf-8")
        self.assertIn("Court Commentary", text)
        self.assertIn(sj.html.escape(self.ROAST_NIGHT), text)

    # -- content ---------------------------------------------------------

    def test_gentle_and_roast_differ_on_same_folder(self):
        self.seed_night()
        _, gentle_out, _ = self.run_main(
            [str(self.root), "--json", "--intensity", "gentle"]
        )
        _, roast_out, _ = self.run_main(
            [str(self.root), "--json", "--intensity", "roast"]
        )
        gentle = json.loads(gentle_out)["commentary"]
        roast = json.loads(roast_out)["commentary"]
        self.assertEqual(gentle, self.GENTLE_NIGHT)  # real night count: 6
        self.assertEqual(roast, self.ROAST_NIGHT)
        self.assertNotEqual(gentle, roast)

    def test_priority_night_beats_duplicates(self):
        # 12 identical files (11 duplicate extras) but 7 of 12 at night:
        # the night case wins the commentary.
        base = datetime(2026, 6, 1)
        for i in range(12):
            hour = 23 if i < 7 else 12
            write_file(self.root, f"screenshot_{i:02d}.png",
                       make_png(100, 100),
                       base.replace(hour=hour) + timedelta(days=i))
        code, out, _ = self.run_main(
            [str(self.root), "--json", "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["commentary"],
            "7 screenshots after 10 PM. The court has reviewed the "
            "defendant's sleep schedule and found it guilty too.",
        )

    def test_priority_hoard_beats_duplicates(self):
        stats = {
            "count": 1500, "night_count": 10, "night_share": 10.0,
            "duplicate_files": 30, "duplicate_groups": 5,
            "longest_streak": 3, "oldest_dt": datetime(2026, 6, 1),
        }
        line = sj.court_commentary(stats, 4, now=datetime(2026, 6, 2))
        self.assertEqual(
            line,
            "1,500 screenshots. The court would ask the defendant to "
            "produce one specific screenshot, but nobody involved has "
            "that kind of time. That is rather the point.",
        )

    def test_oldest_case_uses_archivist_age_basis(self):
        now = datetime(2026, 1, 1)
        stats = {
            "count": 100, "night_count": 0, "night_share": 0.0,
            "duplicate_files": 0, "duplicate_groups": 0,
            "longest_streak": 3,
            "oldest_dt": now - timedelta(days=6 * 365),
        }
        line = sj.court_commentary(stats, 0, now=now)
        self.assertEqual(
            line,
            "The oldest screenshot here is 6 years old, and the court "
            "finds something sweet in that. Some things are simply "
            "worth keeping.",
        )

    def test_no_evidence_commentary_line(self):
        self.assertEqual(sj.court_commentary({}, 4), sj.NO_COMMENTARY)
        self.assertEqual(
            sj.court_commentary({"count": 0}, 0),
            "No evidence, no commentary. The court does not roast an "
            "empty folder.",
        )

    def test_no_evidence_cli_with_intensity(self):
        empty = self.root / "empty"
        empty.mkdir()
        code, out, _ = self.run_main(
            [str(empty), "--json", "--intensity", "roast"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["commentary"], sj.NO_COMMENTARY)
        code, out, _ = self.run_main([str(empty), "--intensity", "roast"])
        self.assertEqual(code, 2)
        self.assertIn(f"COMMENTARY: {sj.NO_COMMENTARY}", out)

    # -- config file -----------------------------------------------------

    def test_config_intensity_applies(self):
        self.seed_night()
        self.write_config('intensity = "roast"\n')
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["commentary"], self.ROAST_NIGHT)

    def test_config_intensity_cli_overrides(self):
        self.seed_night()
        self.write_config("intensity = 90\n")  # roast, as an integer
        code, out, _ = self.run_main(
            [str(self.root), "--json", "--intensity", "gentle"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["commentary"], self.GENTLE_NIGHT)

    def test_config_intensity_malformed_warns_and_is_ignored(self):
        self.seed_night()
        self.write_config('intensity = "lava"\n')
        code, out, err = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertNotIn("commentary", json.loads(out))

    # -- composition -----------------------------------------------------

    def test_intensity_with_strict_judge(self):
        self.seed_night()
        code, out, _ = self.run_main(
            [str(self.root), "--judge", "strict", "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Hanging Judge", out)
        self.assertIn(
            "Sentence: none. This time. The court will be watching "
            "the folder.", out,
        )
        self.assertIn(f"COMMENTARY: {self.ROAST_NIGHT}", out)

    def test_intensity_with_judge_pack(self):
        self.seed_night()
        pack = self.root / "pack.json"
        pack.write_text(
            json.dumps({"name": "The Test Judge", "sentences": {}}),
            encoding="utf-8",
        )
        code, out, _ = self.run_main(
            [str(self.root), "--json", "--judge-file", str(pack),
             "--intensity", "gentle"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["judge"], "The Test Judge")
        self.assertEqual(data["commentary"], self.GENTLE_NIGHT)

    def test_fail_above_unchanged_with_intensity(self):
        for i in range(60):  # Petty Offender
            write_file(self.root, f"screenshot_{i:02d}.png",
                       make_png(100, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        code, _, _ = self.run_main(
            [str(self.root), "--intensity", "roast",
             "--fail-above", "Innocent Bystander"]
        )
        self.assertEqual(code, 1)
        code, _, _ = self.run_main(
            [str(self.root), "--intensity", "roast",
             "--fail-above", "Habitual Hoarder"]
        )
        self.assertEqual(code, 0)


# ---------------------------------------------------------------------------
# the grand jury (v0.11.0): both courts, one hearing
# ---------------------------------------------------------------------------

class GrandJuryTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.hist_file = self.root / "court" / "history.jsonl"
        patcher = mock.patch.object(
            sj, "history_path", return_value=self.hist_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.shots_dir = self.root / "shots"
        self.shots_dir.mkdir()
        self.dl_dir = self.root / "downloads"
        self.dl_dir.mkdir()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_shots(self, n, start=datetime(2026, 6, 1, 12, 0)):
        for i in range(n):
            write_file(self.shots_dir, f"screenshot_{i:03d}.png",
                       make_png(100, 100),
                       start + timedelta(days=i % 28))

    def seed_dl(self, n, size=1000, start=datetime(2026, 6, 1, 12, 0)):
        for i in range(n):
            write_sized(self.dl_dir, f"file_{i:04d}.dat", size,
                        start + timedelta(days=i % 28))

    def grand_jury(self, *extra):
        return self.run_main(
            ["--grand-jury", str(self.shots_dir), str(self.dl_dir),
             *extra]
        )

    # -- the combined hearing -------------------------------------------

    def test_happy_path_low_low(self):
        self.seed_shots(3)
        self.seed_dl(3)
        code, out, _ = self.grand_jury()
        self.assertEqual(code, 0)
        self.assertIn("SNAP JUDGMENT - THE GRAND JURY", out)
        self.assertIn("THE SCREENSHOTS COURT", out)
        self.assertIn("Innocent Bystander", out)
        self.assertIn("THE DOWNLOADS DOCKET", out)
        self.assertIn("Tidy Desk", out)
        self.assertIn("THE FULL DOCKET", out)
        self.assertIn("Combined files", out)
        # both courts in their bottom two tiers: the acquittal line
        self.assertIn("little to prosecute", out)

    def test_mixed_pair_names_the_worse_folder(self):
        self.seed_shots(3)          # Innocent Bystander, rank 0
        self.seed_dl(120)           # Digital Junk Drawer, rank 2
        code, out, _ = self.grand_jury()
        self.assertEqual(code, 0)
        self.assertIn("Digital Junk Drawer", out)
        self.assertIn("the downloads folder", out)
        self.assertIn("verdict is split", out)

    def test_standing_lines_for_every_voice_and_class(self):
        pairs = {
            "low": ("Innocent Bystander", "Mild Clutter"),
            "mixed": ("Repeat Offender", "Mild Clutter"),
            "high": ("Felony-Level Archivist", "Hoarding Situation"),
        }
        for voice in ("strict", "fair", "lenient"):
            for pair_class, (shots_tier, dl_tier) in pairs.items():
                with self.subTest(voice=voice, pair_class=pair_class):
                    line = sj.grand_jury_standing(
                        voice, shots_tier, dl_tier
                    )
                    self.assertTrue(line)
                    self.assertNotIn("—", line)  # no em dashes
                    self.assertNotIn("{worse}", line)
        # the mixed line names the worse folder (screenshots here)
        line = sj.grand_jury_standing(
            "fair", "Repeat Offender", "Mild Clutter"
        )
        self.assertIn("the screenshots folder", line)
        # both courts in their top two tiers: the harshest class
        line = sj.grand_jury_standing(
            "fair", "Felony-Level Archivist", "Hoarding Situation"
        )
        self.assertIn("two crime scenes", line)

    def test_strict_judge_presides(self):
        self.seed_shots(3)
        self.seed_dl(3)
        code, out, _ = self.grand_jury("--judge", "strict")
        self.assertEqual(code, 0)
        self.assertIn("Presiding: The Hanging Judge", out)
        self.assertIn("remain under observation", out)

    def test_intensity_commentary_in_both_blocks(self):
        base = datetime(2026, 6, 1, 23, 30)
        for i in range(6):
            write_file(self.shots_dir, f"screenshot_{i}.png",
                       make_png(100, 100), base + timedelta(days=i))
        for i in range(12):
            write_sized(self.dl_dir, f"tool{i}.exe", 100,
                        datetime(2026, 6, 1, 12, 0))
        code, out, _ = self.grand_jury("--intensity", "roast")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("COMMENTARY:"), 2)
        self.assertIn("sleep schedule", out)       # screenshots court
        self.assertIn("every key to every door", out)  # downloads docket

    # -- folder resolution ------------------------------------------------

    def test_auto_detect_with_no_positionals(self):
        shots = self.root / "Pictures" / "Screenshots"
        for i in range(3):
            write_file(shots, f"screenshot_{i}.png", make_png(10, 10),
                       datetime(2026, 6, 1, 12, 0))
        downloads = self.root / "Downloads"
        for i in range(3):
            write_sized(downloads, f"f{i}.dat", 10,
                        datetime(2026, 6, 1, 12, 0))
        with mock.patch.object(sj.Path, "home", return_value=self.root):
            code, out, _ = self.run_main(["--grand-jury"])
        self.assertEqual(code, 0)
        self.assertIn("Innocent Bystander", out)
        self.assertIn("Tidy Desk", out)

    def test_one_positional_is_an_error(self):
        code, _, err = self.run_main(
            ["--grand-jury", str(self.shots_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("--grand-jury", err)
        self.assertIn("both folders or none", err)

    def test_neither_court_with_evidence_exits_2(self):
        code, out, _ = self.grand_jury()
        self.assertEqual(code, 2)
        self.assertIn("THE FULL DOCKET", out)
        self.assertIn("No screenshots were produced as evidence.", out)
        self.assertIn("No files were produced as evidence.", out)
        # each court's verdict line plus its FULL DOCKET row
        self.assertEqual(out.count("No Evidence"), 4)

    def test_one_court_with_evidence_exits_0(self):
        self.seed_dl(30)  # Mild Clutter; the shots folder stays empty
        code, out, _ = self.grand_jury()
        self.assertEqual(code, 0)
        self.assertIn("No Evidence", out)
        self.assertIn("Mild Clutter", out)

    def test_missing_folder_reports_no_evidence(self):
        self.seed_dl(3)
        missing = self.root / "no-such-folder"
        code, out, _ = self.run_main(
            ["--grand-jury", str(missing), str(self.dl_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("No Evidence", out)
        self.assertIn("Tidy Desk", out)

    # -- outputs -----------------------------------------------------------

    def test_json_shape_and_combined_totals(self):
        self.seed_shots(3)
        self.seed_dl(30, size=2000)
        code, out, _ = self.grand_jury("--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIs(data["grand_jury"], True)
        self.assertEqual(data["screenshots"]["court"], "screenshots")
        self.assertEqual(data["screenshots"]["verdict"],
                         "Innocent Bystander")
        self.assertEqual(data["downloads"]["court"], "downloads")
        self.assertEqual(data["downloads"]["verdict"], "Mild Clutter")
        combined = data["combined"]
        self.assertEqual(
            set(combined),
            {"files", "bytes", "screenshots_tier", "downloads_tier"},
        )
        self.assertEqual(combined["files"], 33)
        shots_bytes = data["screenshots"]["total_bytes"]
        self.assertEqual(
            combined["bytes"],
            shots_bytes + data["downloads"]["total_bytes"],
        )
        self.assertEqual(combined["screenshots_tier"],
                         "Innocent Bystander")
        self.assertEqual(combined["downloads_tier"], "Mild Clutter")

    def test_png_card_is_valid_and_deterministic(self):
        self.seed_shots(3)
        self.seed_dl(3)
        first = self.root / "docket1.png"
        second = self.root / "docket2.png"
        code, _, _ = self.grand_jury("--png", str(first))
        self.assertEqual(code, 0)
        code, _, _ = self.grand_jury("--png", str(second))
        self.assertEqual(code, 0)
        blob = first.read_bytes()
        self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(blob, second.read_bytes())

    def test_svg_and_html_cards_name_the_full_docket(self):
        self.seed_shots(3)
        self.seed_dl(3)
        svg = self.root / "docket.svg"
        card = self.root / "docket.html"
        code, _, _ = self.grand_jury(
            "--svg", str(svg), "--card", str(card)
        )
        self.assertEqual(code, 0)
        svg_text = svg.read_text(encoding="utf-8")
        self.assertIn("SNAP JUDGMENT - THE FULL DOCKET", svg_text)
        self.assertIn("Innocent Bystander", svg_text)
        self.assertIn("Tidy Desk", svg_text)
        self.assertIn("The Full Docket",
                      card.read_text(encoding="utf-8"))

    def test_markdown_mirrors_the_terminal_structure(self):
        self.seed_shots(3)
        self.seed_dl(3)
        md = self.root / "docket.md"
        code, _, _ = self.grand_jury("--out", str(md))
        self.assertEqual(code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("# Snap Judgment - The Grand Jury", text)
        self.assertIn("## The Screenshots Court", text)
        self.assertIn("## The Downloads Docket", text)
        self.assertIn("## The Full Docket", text)
        self.assertIn("little to prosecute", text)

    # -- fail-above and refusals ------------------------------------------

    def test_fail_above_trips_on_the_worse_court(self):
        self.seed_shots(3)          # Innocent Bystander, rank 0
        self.seed_dl(120)           # Digital Junk Drawer, rank 2
        code, _, _ = self.grand_jury("--fail-above", "Mild Clutter")
        self.assertEqual(code, 1)   # the docket is above Mild Clutter
        code, _, _ = self.grand_jury(
            "--fail-above", "Digital Junk Drawer"
        )
        self.assertEqual(code, 0)
        code, _, _ = self.grand_jury("--fail-above", "Petty Offender")
        self.assertEqual(code, 0)   # screenshots rank 0 is not above 1
        code, _, err = self.grand_jury("--fail-above", "Banana Court")
        self.assertEqual(code, 2)   # a tier in neither ladder
        self.assertIn("--fail-above", err)

    def test_single_court_options_are_refused(self):
        self.seed_shots(3)
        self.seed_dl(3)
        for flag, value in (("--vs", str(self.dl_dir)),
                            ("--csv", "x.csv"),
                            ("--history", None),
                            ("--rematch", None),
                            ("--judge-file", "pack.json")):
            argv = ["--grand-jury", str(self.shots_dir),
                    str(self.dl_dir), flag]
            if value is not None:
                argv.append(value)
            with self.subTest(flag=flag):
                code, _, err = self.run_main(argv)
                self.assertEqual(code, 2)
                self.assertIn(flag, err)
                self.assertIn("--grand-jury", err)

    # -- the court record ---------------------------------------------------

    def test_history_gains_both_entries(self):
        self.seed_shots(3)
        self.seed_dl(30)
        code, _, _ = self.grand_jury()
        self.assertEqual(code, 0)
        records = [
            json.loads(line)
            for line in self.hist_file.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 2)
        by_court = {r.get("court", "screenshots"): r for r in records}
        self.assertEqual(
            by_court["screenshots"]["folders"], [str(self.shots_dir)]
        )
        self.assertEqual(
            by_court["screenshots"]["verdict"], "Innocent Bystander"
        )
        self.assertEqual(
            by_court["downloads"]["folders"], [str(self.dl_dir)]
        )
        self.assertEqual(by_court["downloads"]["verdict"], "Mild Clutter")
        # and each court's rematch still finds its own last hearing
        code, out, _ = self.run_main(
            [str(self.dl_dir), "--court", "downloads", "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Since your last hearing", out)
        code, out, _ = self.run_main(
            [str(self.shots_dir), "--rematch"]
        )
        self.assertEqual(code, 0)
        self.assertIn("REMATCH", out)


# ---------------------------------------------------------------------------
# the downloads cleanup order and recursive docket (v0.11.0)
# ---------------------------------------------------------------------------

class DownloadsCleanupRecursiveTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_chain(self):
        base = datetime(2026, 6, 1, 12, 0)
        specs = [
            ("setup.exe", 500),
            ("setup (1).exe", 500),
            ("setup (2).exe", 700),
            ("report.pdf", 300),
            ("report copy.pdf", 300),
            ("unique.txt", 100),
        ]
        for name, size in specs:
            write_sized(self.root, name, size, base)

    # -- the downloads cleanup order --------------------------------------

    def test_cleanup_lists_only_the_extra_copies(self):
        self.seed_chain()
        target = self.root / "cleanup.txt"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        lines = target.read_text(encoding="utf-8").splitlines()
        header = [line for line in lines if line.startswith("#")]
        paths = [line for line in lines if not line.startswith("#")]
        self.assertTrue(header)
        self.assertIn("3 files", header[1])
        self.assertIn("1500 bytes", header[1])
        expected = sorted(
            str(self.root / name)
            for name in ("setup (1).exe", "setup (2).exe",
                         "report copy.pdf")
        )
        self.assertEqual(paths, expected)  # sorted, keepers excluded
        self.assertNotIn(str(self.root / "setup.exe"), paths)
        self.assertNotIn(str(self.root / "report.pdf"), paths)

    def test_cleanup_terminal_line_and_json(self):
        self.seed_chain()
        target = self.root / "cleanup.txt"
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        self.assertIn("CLEANUP ORDER: 3 files (", out)
        self.assertIn(f"listed in {target}", out)
        self.assertIn("The court does not swing the axe itself.", out)
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--cleanup-list", str(target), "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["cleanup_order"],
            {"file": str(target), "count": 3, "bytes": 1500},
        )

    def test_cleanup_deletes_nothing(self):
        self.seed_chain()
        before = sorted(p.name for p in self.root.iterdir())
        target = self.root / "cleanup.txt"
        code, _, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        for name in before:
            self.assertTrue((self.root / name).is_file(), name)

    def test_cleanup_with_no_duplicates_orders_nothing(self):
        base = datetime(2026, 6, 1, 12, 0)
        for i in range(3):
            write_sized(self.root, f"plain{i}.txt", 100, base)
        target = self.root / "cleanup.txt"
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads",
             "--cleanup-list", str(target)]
        )
        self.assertEqual(code, 0)
        self.assertIn("CLEANUP ORDER: 0 files", out)
        paths = [
            line for line in target.read_text(
                encoding="utf-8"
            ).splitlines()
            if not line.startswith("#")
        ]
        self.assertEqual(paths, [])

    # -- the recursive docket ----------------------------------------------

    def seed_tree(self):
        base = datetime(2026, 6, 1, 12, 0)
        for i in range(3):
            write_sized(self.root, f"top{i}.dat", 100, base)
        sub = self.root / "sub"
        for i in range(7):
            write_sized(sub, f"inner{i}.dat", 100, base)
        return sub

    def test_recursive_collects_the_whole_tree(self):
        sub = self.seed_tree()
        files = sj.collect_downloads([self.root], recursive=True)
        self.assertEqual(len(files), 10)
        stats = sj.analyze_downloads(files, recursive=True)
        self.assertEqual(stats["count"], 10)
        self.assertEqual(
            stats["folders"],
            [{"path": str(sub), "count": 7},
             {"path": str(self.root), "count": 3}],
        )
        # the default stays on the surface, with no breakdown
        self.assertEqual(len(sj.collect_downloads([self.root])), 3)
        plain = sj.analyze_downloads(sj.collect_downloads([self.root]))
        self.assertNotIn("folders", plain)

    def test_recursive_breakdown_reaches_terminal_and_json(self):
        sub = self.seed_tree()
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--recursive"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Folders", out)
        self.assertIn(str(sub), out)
        code, out, _ = self.run_main(
            [str(self.root), "--court", "downloads", "--recursive",
             "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 10)
        self.assertEqual(
            data["folders"],
            [{"path": str(sub), "count": 7},
             {"path": str(self.root), "count": 3}],
        )

    def test_recursive_grouping_stays_within_each_folder(self):
        base = datetime(2026, 6, 1, 12, 0)
        write_sized(self.root, "setup.exe", 500, base)
        sub = self.root / "sub"
        write_sized(sub, "setup.exe", 500, base)
        write_sized(sub, "setup (1).exe", 500, base)
        files = sj.collect_downloads([self.root], recursive=True)
        stats = sj.analyze_downloads(files, recursive=True)
        # the subfolder chain is one group with one extra; the lone
        # setup.exe on the surface is not part of any chain
        self.assertEqual(stats["duplicate_groups"], 1)
        self.assertEqual(stats["duplicate_extra"], 1)
        self.assertEqual(
            stats["duplicate_extra_paths"],
            [str(sub / "setup (1).exe")],
        )


# ---------------------------------------------------------------------------
# the trend (v0.12.0): the court record for one folder set, visualized
# ---------------------------------------------------------------------------

class TrendTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self._old_cwd = os.getcwd()
        self.shots_dir = self.root / "shots"
        self.dl_dir = self.root / "downloads"
        self.hist_file = self.root / "court" / "history.jsonl"
        patcher = mock.patch.object(
            sj, "history_path", return_value=self.hist_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self._old_cwd)
        super().tearDown()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_shots(self, n, start=1):
        for i in range(n):
            write_file(
                self.shots_dir, f"screenshot_{start + i}.png",
                make_png(100, 100),
                datetime(2026, 6, 1, 12, 0) + timedelta(days=start + i),
            )

    def seed_files(self, n, size=1000):
        base = datetime(2026, 6, 1, 12, 0)
        for i in range(n):
            write_sized(self.dl_dir, f"file_{i:04d}.dat", size,
                        base + timedelta(days=i % 28))

    def seed_history(self, records):
        self.hist_file.parent.mkdir(parents=True, exist_ok=True)
        self.hist_file.write_text(
            "".join(json.dumps(r) + "\n" for r in records),
            encoding="utf-8",
        )

    def record(self, folder, count, verdict, when, court=None):
        rec = {
            "timestamp": when,
            "folders": [str(folder)],
            "count": count,
            "total_bytes": count * 100,
            "verdict": verdict,
            "judge": "fair",
        }
        if court is not None:
            rec["court"] = court
        return rec

    def sparkline_of(self, out):
        blocks = set("▁▂▃▄▅▆▇█")
        for line in out.splitlines():
            text = line.strip()
            if text and set(text) <= blocks:
                return text
        return None

    # -- unit level ------------------------------------------------------

    def test_sparkline_scaling(self):
        self.assertEqual(sj.sparkline([]), "")
        self.assertEqual(sj.sparkline([7]), "█")
        self.assertEqual(sj.sparkline([3, 3]), "██")
        self.assertEqual(sj.sparkline([10, 30, 60]), "▁▄█")
        self.assertEqual(sj.sparkline([0, 10]), "▁█")

    def test_build_trend_unit(self):
        records = [
            self.record(self.shots_dir, 0, "No Evidence",
                        "2026-09-01T10:00:00"),
            self.record(self.shots_dir, 8, "Innocent Bystander",
                        "2026-10-01T10:00:00"),
            # a different folder set: not this trend
            self.record(self.root / "elsewhere", 500,
                        "Habitual Hoarder", "2026-10-02T10:00:00"),
        ]
        trend = sj.build_trend(
            records, [str(self.shots_dir)], "screenshots"
        )
        self.assertEqual(trend["hearings"], 2)
        self.assertEqual(trend["counts"], [0, 8])
        self.assertEqual(trend["delta"], 8)
        # a first count of zero has no meaningful percent
        self.assertEqual(trend["delta_pct"], 0.0)
        self.assertEqual(
            trend["tiers"], ["No Evidence", "Innocent Bystander"]
        )

    # -- screenshots court -------------------------------------------------

    def test_trend_seeded_history(self):
        self.seed_history([
            self.record(self.shots_dir, 10, "Innocent Bystander",
                        "2026-09-01T10:00:00"),
            self.record(self.shots_dir, 30, "Innocent Bystander",
                        "2026-10-01T10:00:00"),
            # the downloads court's record for the same folder must
            # not leak into the screenshots trend
            self.record(self.shots_dir, 999, "Superfund Site",
                        "2026-10-02T10:00:00", court="downloads"),
        ])
        self.seed_shots(60)
        code, out, _ = self.run_main([str(self.shots_dir), "--trend"])
        self.assertEqual(code, 0)
        self.assertIn("TREND", out)
        self.assertIn("Hearings on record: 3", out)
        self.assertIn("Count: 10 -> 60 screenshots (+50, +500.0%)", out)
        self.assertIn(
            "Verdict: Innocent Bystander -> Petty Offender", out
        )
        spark = self.sparkline_of(out)
        self.assertEqual(spark, "▁▄█")
        self.assertEqual(len(spark), 3)  # one bar per hearing

    def test_trend_first_hearing(self):
        self.seed_shots(3)
        code, out, _ = self.run_main([str(self.shots_dir), "--trend"])
        self.assertEqual(code, 0)
        self.assertIn("This is the first hearing on record.", out)
        self.assertIn(
            "Count: 3 screenshots, verdict Innocent Bystander.", out
        )
        self.assertEqual(self.sparkline_of(out), "█")
        self.assertIn("(one hearing on record)", out)

    def test_trend_includes_today_and_records_it(self):
        self.seed_history([
            self.record(self.shots_dir, 5, "Innocent Bystander",
                        "2026-09-01T10:00:00"),
        ])
        self.seed_shots(3)
        code, out, _ = self.run_main([str(self.shots_dir), "--trend"])
        self.assertEqual(code, 0)
        self.assertIn("Hearings on record: 2", out)
        self.assertIn("Count: 5 -> 3 screenshots (-2, -40.0%)", out)
        # today's hearing entered the record, so a second trend run
        # sees three hearings
        code, out, _ = self.run_main([str(self.shots_dir), "--trend"])
        self.assertEqual(code, 0)
        self.assertIn("Hearings on record: 3", out)
        self.assertEqual(self.sparkline_of(out), "█▁▁")

    def test_trend_json_shape(self):
        self.seed_history([
            self.record(self.shots_dir, 10, "Innocent Bystander",
                        "2026-09-01T10:00:00"),
            self.record(self.shots_dir, 30, "Innocent Bystander",
                        "2026-10-01T10:00:00"),
        ])
        self.seed_shots(60)
        code, out, _ = self.run_main(
            [str(self.shots_dir), "--trend", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "Petty Offender")
        trend = data["trend"]
        self.assertEqual(
            set(trend),
            {"hearings", "counts", "tiers", "first_count",
             "latest_count", "delta", "delta_pct"},
        )
        self.assertEqual(trend["hearings"], 3)
        self.assertEqual(trend["counts"], [10, 30, 60])
        self.assertEqual(
            trend["tiers"],
            ["Innocent Bystander", "Innocent Bystander",
             "Petty Offender"],
        )
        self.assertEqual(trend["first_count"], 10)
        self.assertEqual(trend["latest_count"], 60)
        self.assertEqual(trend["delta"], 50)
        self.assertEqual(trend["delta_pct"], 500.0)

    def test_trend_markdown_section(self):
        self.seed_history([
            self.record(self.shots_dir, 10, "Innocent Bystander",
                        "2026-09-01T10:00:00"),
            self.record(self.shots_dir, 30, "Innocent Bystander",
                        "2026-10-01T10:00:00"),
        ])
        self.seed_shots(60)
        md_path = self.root / "report.md"
        code, _, _ = self.run_main(
            [str(self.shots_dir), "--trend", "--out", str(md_path)]
        )
        self.assertEqual(code, 0)
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("## Trend", md)
        self.assertIn("Hearings on record: 3", md)
        self.assertIn("```\n▁▄█\n```", md)

    def test_trend_intensity_commentary(self):
        base = datetime(2026, 6, 1, 23, 30)  # every shot at night
        for i in range(60):
            write_file(self.shots_dir, f"screenshot_{i}.png",
                       make_png(100, 100), base + timedelta(days=i % 20))
        code, out, _ = self.run_main(
            [str(self.shots_dir), "--trend", "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertIn("COMMENTARY:", out)
        self.assertIn("sleep schedule", out)

    def test_trend_refusals(self):
        self.seed_shots(3)
        combos = [
            ["--vs", str(self.root)],
            ["--vs-year", "2025"],
            ["--rematch"],
            ["--plea-bargain"],
            ["--wrapped-kit", str(self.root / "kit")],
            ["--grand-jury"],
        ]
        for extra in combos:
            with self.subTest(extra=extra):
                code, _, err = self.run_main(
                    [str(self.shots_dir), "--trend"] + extra
                )
                self.assertEqual(code, 2)
                self.assertIn("not supported", err)
                self.assertIn("--trend", err)

    # -- downloads court ---------------------------------------------------

    def test_trend_downloads_court_isolated(self):
        self.seed_history([
            # a screenshots-court record for the same folder (no
            # court field) must not leak into the docket's trend
            self.record(self.dl_dir, 999, "Habitual Hoarder",
                        "2026-09-01T10:00:00"),
            self.record(self.dl_dir, 4, "Tidy Desk",
                        "2026-10-01T10:00:00", court="downloads"),
        ])
        self.seed_files(30)
        code, out, _ = self.run_main(
            [str(self.dl_dir), "--court", "downloads", "--trend"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Hearings on record: 2", out)
        self.assertIn("Count: 4 -> 30 files (+26, +650.0%)", out)
        self.assertIn("Verdict: Tidy Desk -> Mild Clutter", out)
        self.assertNotIn("999", out)


# ---------------------------------------------------------------------------
# co-defendants in the docket (v0.12.0): downloads --vs
# ---------------------------------------------------------------------------

class DownloadsVersusTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self._old_cwd = os.getcwd()
        self.main_dir = self.root / "main"
        self.vs_dir = self.root / "vs"

    def tearDown(self):
        os.chdir(self._old_cwd)
        super().tearDown()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed(self, folder, n, size=1000,
             start=datetime(2026, 6, 1, 12, 0)):
        for i in range(n):
            write_sized(folder, f"file_{i:04d}.dat", size,
                        start + timedelta(days=i % 28))

    def test_vs_terminal_comparison(self):
        self.seed(self.main_dir, 30)   # Mild Clutter
        self.seed(self.vs_dir, 5)      # Tidy Desk
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("CO-DEFENDANT COMPARISON", out)
        self.assertIn("Mild Clutter", out)
        self.assertIn("Tidy Desk", out)
        self.assertIn(
            "Delta (defendant less co-defendant): +25 files", out
        )
        self.assertIn("Bigger offender: the defendant", out)
        self.assertIn("outranks", out)

    def test_vs_co_defendant_worse(self):
        self.seed(self.main_dir, 5)
        self.seed(self.vs_dir, 30)
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Bigger offender: the co-defendant", out)
        self.assertIn(
            "Delta (defendant less co-defendant): -25 files", out
        )

    def test_vs_tiebreak_count_when_tiers_level(self):
        self.seed(self.main_dir, 30)   # Mild Clutter
        self.seed(self.vs_dir, 40)     # also Mild Clutter, more files
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Bigger offender: the co-defendant", out)
        self.assertIn(
            "on count with the verdicts level at Mild Clutter", out
        )

    def test_vs_tiebreak_size_when_counts_level(self):
        self.seed(self.main_dir, 30, size=1000)
        self.seed(self.vs_dir, 30, size=2000)
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Bigger offender: the co-defendant", out)
        self.assertIn("on total size with verdict and count level", out)

    def test_vs_dead_heat(self):
        self.seed(self.main_dir, 10)
        self.seed(self.vs_dir, 10)
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir)]
        )
        self.assertEqual(code, 0)
        self.assertIn("Dead heat", out)

    def test_vs_json_shape(self):
        self.seed(self.main_dir, 30)
        self.seed(self.vs_dir, 5)
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir), "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["versus"]["worse_offender"], "main")
        self.assertEqual(data["versus"]["main"]["count"], 30)
        self.assertEqual(data["versus"]["vs"]["count"], 5)
        self.assertEqual(
            data["versus"]["main"]["court"], "downloads"
        )
        self.assertEqual(
            data["versus"]["vs"]["verdict"], "Tidy Desk"
        )

    def test_vs_dead_heat_json(self):
        self.seed(self.main_dir, 10)
        self.seed(self.vs_dir, 10)
        code, out, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir), "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["versus"]["worse_offender"], "tie")

    def test_vs_markdown(self):
        self.seed(self.main_dir, 30)
        self.seed(self.vs_dir, 5)
        md_path = self.root / "report.md"
        code, _, _ = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir), "--out", str(md_path)]
        )
        self.assertEqual(code, 0)
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("## Co-Defendant Comparison", md)
        self.assertIn("| Verdict | Mild Clutter | Tidy Desk |", md)
        self.assertIn("Bigger offender: the defendant", md)

    def test_vs_missing_folder(self):
        self.seed(self.main_dir, 3)
        code, _, err = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.root / "nope")]
        )
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)

    def test_vs_empty_folder(self):
        self.seed(self.main_dir, 3)
        self.vs_dir.mkdir(parents=True)
        code, _, err = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs", str(self.vs_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("co-defendant folder produced no files", err)

    def test_vs_year_still_refused(self):
        self.seed(self.main_dir, 3)
        code, _, err = self.run_main(
            [str(self.main_dir), "--court", "downloads",
             "--vs-year", "2025"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--vs-year", err)
        self.assertIn("downloads court", err)


# ---------------------------------------------------------------------------
# the rehabilitation plan (v0.13.0): the sentence, served in stages
# ---------------------------------------------------------------------------

class RehabTests(TempDirCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main(argv)
        return code, out.getvalue(), err.getvalue()

    def seed_rehab_folder(self):
        """6 screenshots: 1 newest, a duplicate trio, 2 old (2023)."""
        png = make_png(100, 100)
        write_file(self.root, "screenshot_new.png", make_png(200, 200),
                   datetime(2026, 6, 1, 12, 0))
        for i in range(3):
            write_file(self.root, f"screenshot_dup{i}.png", png,
                       datetime(2026, 5, 1, 12, 0) + timedelta(days=i))
        write_file(self.root, "screenshot_old1.png",
                   make_png(50, 50, pad=10), datetime(2023, 1, 1, 12, 0))
        write_file(self.root, "screenshot_old2.png",
                   make_png(60, 60, pad=20), datetime(2023, 6, 1, 12, 0))
        return png

    def seed_heavy_folder(self, n=60):
        """n recent screenshots, distinct dims and growing sizes."""
        for i in range(n):
            write_file(self.root, f"screenshot_{i:03d}.png",
                       make_png(100 + i, 100, pad=i * 100),
                       datetime(2026, 5, 1, 12, 0) + timedelta(days=i % 28))

    # -- unit level ------------------------------------------------------

    def test_exact_numbers(self):
        png = self.seed_rehab_folder()
        shots, _recordings = sj.collect([self.root])
        rehab = sj.compute_rehabilitation(shots)
        self.assertEqual(
            [step["key"] for step in rehab["steps"]],
            ["duplicate_extras", "ancient_evidence", "heavy_evidence"],
        )
        self.assertEqual(
            [step["count"] for step in rehab["steps"]], [2, 2, 0]
        )
        self.assertEqual(rehab["removed_count"], 4)
        self.assertEqual(rehab["remaining_count"], 2)
        expected_bytes = (
            2 * len(png)
            + len(make_png(50, 50, pad=10))
            + len(make_png(60, 60, pad=20))
        )
        self.assertEqual(rehab["removed_bytes"], expected_bytes)
        self.assertEqual(rehab["steps"][0]["cumulative_count"], 2)
        self.assertEqual(rehab["steps"][1]["cumulative_count"], 4)
        self.assertEqual(rehab["steps"][1]["remaining_count"], 2)
        self.assertEqual(
            rehab["steps"][1]["projected_tier"], "Innocent Bystander"
        )
        self.assertEqual(rehab["projected_tier"], "Innocent Bystander")
        self.assertEqual(rehab["cutoff"], datetime(2024, 6, 1).date())
        self.assertEqual(rehab["years"], 2)
        total = sum(s.size for s in shots)
        self.assertEqual(
            rehab["reclaim_share_pct"],
            round(100.0 * expected_bytes / total, 1),
        )
        # 4 files at 5 seconds each rounds up to a single minute,
        # and fits in a single session of 50
        self.assertEqual(rehab["review_minutes"], 1)
        self.assertEqual(rehab["sessions"], 1)

    def test_partition_never_double_counts(self):
        # 30 old screenshots forming one duplicate group, plus 5
        # recent distinct ones: step 1 claims 29 of the old group,
        # step 2 claims only its keeper. The plea bargain reaches
        # the same total by the opposite attribution.
        png = make_png(100, 100)
        for i in range(30):
            write_file(self.root, f"screenshot_old{i}.png", png,
                       datetime(2023, 1, 1, 12, 0) + timedelta(days=i))
        for i in range(5):
            write_file(self.root, f"screenshot_new{i}.png",
                       make_png(200 + i, 100),
                       datetime(2026, 5, 1, 12, 0) + timedelta(days=i))
        shots, _recordings = sj.collect([self.root])
        rehab = sj.compute_rehabilitation(shots)
        self.assertEqual(
            [step["count"] for step in rehab["steps"]], [29, 1, 0]
        )
        self.assertEqual(
            sum(step["count"] for step in rehab["steps"]),
            rehab["removed_count"],
        )
        self.assertEqual(
            sum(step["bytes"] for step in rehab["steps"]),
            rehab["removed_bytes"],
        )
        plea = sj.compute_plea_bargain(shots, 2)
        self.assertEqual(rehab["removed_count"], plea["removed_count"])
        self.assertEqual(rehab["removed_bytes"], plea["removed_bytes"])

    def test_heavy_step_takes_ten_largest_remaining(self):
        self.seed_heavy_folder(60)
        shots, _recordings = sj.collect([self.root])
        rehab = sj.compute_rehabilitation(shots)
        self.assertEqual(
            [step["count"] for step in rehab["steps"]], [0, 0, 10]
        )
        heavy = rehab["steps"][2]
        expected = sum(
            len(make_png(100 + i, 100, pad=i * 100))
            for i in range(50, 60)
        )
        self.assertEqual(heavy["bytes"], expected)
        self.assertEqual(rehab["remaining_count"], 50)
        self.assertEqual(rehab["projected_tier"], "Petty Offender")
        # cumulative projected tiers never get worse down the plan
        ranks = [
            sj.tier_rank(step["projected_tier"])
            for step in rehab["steps"]
        ]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_heavy_step_needs_more_than_ten_standing(self):
        self.seed_heavy_folder(10)
        shots, _recordings = sj.collect([self.root])
        rehab = sj.compute_rehabilitation(shots)
        self.assertEqual(rehab["steps"][2]["count"], 0)
        self.assertEqual(rehab["removed_count"], 0)

    def test_sessions_and_review_time_scale(self):
        for i in range(120):
            write_file(self.root, f"screenshot_old{i}.png",
                       make_png(100 + i, 100),
                       datetime(2023, 1, 1, 12, 0) + timedelta(days=i))
        write_file(self.root, "screenshot_new.png", make_png(900, 900),
                   datetime(2026, 6, 1, 12, 0))
        shots, _recordings = sj.collect([self.root])
        rehab = sj.compute_rehabilitation(shots)
        self.assertEqual(rehab["removed_count"], 120)
        self.assertEqual(rehab["sessions"], 3)  # ceil(120 / 50)
        self.assertEqual(rehab["review_minutes"], 10)  # 120 * 5s = 600s

    def test_single_screenshot_needs_no_plan(self):
        write_file(self.root, "screenshot_one.png", make_png(100, 100),
                   datetime(2026, 6, 1, 12, 0))
        shots, _recordings = sj.collect([self.root])
        rehab = sj.compute_rehabilitation(shots)
        self.assertEqual(rehab["removed_count"], 0)
        self.assertEqual(rehab["removed_bytes"], 0)
        self.assertEqual(rehab["remaining_count"], 1)
        self.assertEqual(rehab["projected_tier"], "Innocent Bystander")
        self.assertEqual(rehab["review_minutes"], 0)
        self.assertEqual(rehab["sessions"], 0)
        self.assertIn(
            "No rehabilitation required",
            sj.rehab_closing(rehab, "Innocent Bystander"),
        )

    def test_voice_never_changes_the_math(self):
        self.seed_rehab_folder()
        shots, _recordings = sj.collect([self.root])
        fair = sj.compute_rehabilitation(shots, voice="fair")
        strict = sj.compute_rehabilitation(shots, voice="strict")
        self.assertEqual(fair["removed_count"], strict["removed_count"])
        self.assertEqual(fair["removed_bytes"], strict["removed_bytes"])
        self.assertEqual(
            fair["projected_tier"], strict["projected_tier"]
        )

    # -- screenshots court -------------------------------------------------

    def test_section_absent_without_flag(self):
        self.seed_rehab_folder()
        code, out, _ = self.run_main([str(self.root)])
        self.assertEqual(code, 0)
        self.assertNotIn("REHABILITATION PLAN", out)
        code, out, _ = self.run_main([str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("rehabilitation", json.loads(out))

    def test_section_present(self):
        self.seed_rehab_folder()
        code, out, _ = self.run_main([str(self.root), "--rehab"])
        self.assertEqual(code, 0)
        self.assertIn("THE REHABILITATION PLAN", out)
        self.assertIn("Step 1 - Clear the duplicate extras: 2 files", out)
        self.assertIn("Step 2 - Retire the ancient evidence: 2 files", out)
        self.assertNotIn("Step 3", out)  # an empty step is not assigned
        self.assertIn("After this step: 2 screenshots remain", out)
        self.assertIn("Estimated review time", out)
        self.assertIn(
            "The plan reclaims space but not standing", out
        )

    def test_rehabilitable_line_when_tier_improves(self):
        for i in range(55):
            write_file(self.root, f"screenshot_old{i}.png",
                       make_png(100 + i, 100),
                       datetime(2023, 1, 1, 12, 0) + timedelta(days=i))
        for i in range(5):
            write_file(self.root, f"screenshot_new{i}.png",
                       make_png(500 + i, 100),
                       datetime(2026, 6, 1, 12, 0) + timedelta(days=i))
        code, out, _ = self.run_main([str(self.root), "--rehab"])
        self.assertEqual(code, 0)
        self.assertIn("Petty Offender", out)  # the verdict itself stands
        self.assertIn(
            "The court finds the defendant rehabilitable", out
        )

    def test_single_screenshot_terminal(self):
        write_file(self.root, "screenshot_one.png", make_png(100, 100),
                   datetime(2026, 6, 1, 12, 0))
        code, out, _ = self.run_main([str(self.root), "--rehab"])
        self.assertEqual(code, 0)
        self.assertIn("No steps assigned", out)
        self.assertIn("No rehabilitation required", out)

    def test_empty_folder(self):
        empty = self.root / "empty"
        empty.mkdir()
        code, out, _ = self.run_main([str(empty), "--rehab"])
        self.assertEqual(code, 2)
        self.assertIn("No Evidence", out)
        code, out, _ = self.run_main([str(empty), "--rehab", "--json"])
        self.assertEqual(code, 2)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "No Evidence")
        self.assertNotIn("rehabilitation", data)

    def test_json_shape(self):
        png = self.seed_rehab_folder()
        code, out, _ = self.run_main(
            [str(self.root), "--rehab", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "Innocent Bystander")
        rehab = data["rehabilitation"]
        self.assertEqual(
            set(rehab),
            {"years", "steps", "removed_count", "removed_bytes",
             "removed_human", "reclaim_share_pct", "remaining_count",
             "projected_tier", "review_minutes", "sessions", "outlook"},
        )
        self.assertEqual(
            set(rehab["steps"][0]),
            {"key", "name", "count", "bytes", "human",
             "cumulative_count", "cumulative_bytes", "remaining_count",
             "projected_tier"},
        )
        self.assertEqual(
            [step["count"] for step in rehab["steps"]], [2, 2, 0]
        )
        self.assertEqual(rehab["removed_count"], 4)
        self.assertEqual(
            rehab["removed_bytes"],
            2 * len(png)
            + len(make_png(50, 50, pad=10))
            + len(make_png(60, 60, pad=20)),
        )
        self.assertEqual(rehab["remaining_count"], 2)
        self.assertEqual(rehab["projected_tier"], "Innocent Bystander")
        self.assertIn("reclaims space but not standing",
                      rehab["outlook"])

    def test_markdown_section(self):
        self.seed_rehab_folder()
        md_path = self.root / "report.md"
        code, _, _ = self.run_main(
            [str(self.root), "--rehab", "--out", str(md_path)]
        )
        self.assertEqual(code, 0)
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("## Rehabilitation Plan", md)
        self.assertIn("Step 1 - Clear the duplicate extras", md)
        self.assertIn("Step 2 - Retire the ancient evidence", md)
        self.assertIn("projected verdict of **Innocent Bystander**", md)

    def test_rehab_composes_with_judge_and_intensity(self):
        base = datetime(2026, 6, 1, 23, 30)  # every shot at night
        for i in range(60):
            write_file(self.root, f"screenshot_{i}.png",
                       make_png(100 + i, 100, pad=i * 10),
                       base + timedelta(days=i % 20))
        code, out, _ = self.run_main(
            [str(self.root), "--rehab", "--judge", "strict",
             "--intensity", "roast"]
        )
        self.assertEqual(code, 0)
        self.assertIn("THE REHABILITATION PLAN", out)
        self.assertIn("Step 1 - Move out the heavy evidence", out)
        self.assertIn("COMMENTARY:", out)

    def test_rehab_refusals(self):
        self.seed_rehab_folder()
        combos = [
            ["--vs", str(self.root)],
            ["--vs-year", "2025"],
            ["--rematch"],
            ["--plea-bargain"],
            ["--cleanup-list", str(self.root / "cleanup.txt")],
            ["--wrapped-kit", str(self.root / "kit")],
            ["--trend"],
            ["--grand-jury"],
        ]
        for extra in combos:
            with self.subTest(extra=extra):
                code, _, err = self.run_main(
                    [str(self.root), "--rehab"] + extra
                )
                self.assertEqual(code, 2)
                self.assertIn("not supported", err)
                self.assertIn("--rehab", err)

    # -- downloads court ---------------------------------------------------

    def test_rehab_refused_in_downloads_court(self):
        dl_dir = self.root / "downloads"
        write_sized(dl_dir, "file_0.dat", 1000,
                    datetime(2026, 6, 1, 12, 0))
        code, _, err = self.run_main(
            [str(dl_dir), "--court", "downloads", "--rehab"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--rehab", err)
        self.assertIn("downloads court", err)


# ---------------------------------------------------------------------------
# shell completions (v0.7.0)
# ---------------------------------------------------------------------------

class CompletionTests(unittest.TestCase):
    def test_bash_completion_covers_every_long_flag(self):
        repo = Path(__file__).resolve().parent.parent
        bash = (repo / "completions" / "snapjudgment.bash").read_text(
            encoding="utf-8"
        )
        flags = set(re.findall(r"--[a-z][a-z0-9-]*",
                               sj.build_parser().format_help()))
        self.assertGreater(len(flags), 15)
        for flag in sorted(flags):
            self.assertIn(flag, bash)


if __name__ == "__main__":
    unittest.main()
