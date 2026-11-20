#!/usr/bin/env python3
"""Generate examples/downloads-demo-output.txt.

Builds a deterministic synthetic downloads folder (fixed names, sizes,
and modification times), judges it in the downloads court, and saves
the terminal report. Sizes are set with truncate(), so the fixture's
hundreds of megabytes are sparse and cost no real disk.

Run from the repo root:  python3 tools/make_downloads_demo.py
"""

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snap_judgment as sj

REPO = Path(__file__).resolve().parent.parent


def write_sized(folder, name, size, when):
    path = Path(folder) / name
    with open(path, "wb") as fh:
        fh.truncate(size)
    ts = when.timestamp()
    os.utime(path, (ts, ts))
    return path


def build_fixture(folder):
    """117 files: installers with copy chains, archives, duplicates,
    one torrent, receipts, one genuinely old file, and honest filler."""
    recent = datetime(2026, 10, 15, 14, 30)
    entries = []
    # installers (12), including a setup.exe copy chain
    installers = [
        ("setup.exe", 4_800_000), ("setup (1).exe", 4_800_000),
        ("setup (2).exe", 4_800_000), ("driver.msi", 12_400_000),
        ("update.pkg", 88_000_000), ("tool.dmg", 45_000_000),
        ("app.deb", 3_200_000), ("app.rpm", 3_100_000),
        ("PhotoApp.AppImage", 96_000_000), ("patch.exe", 1_150_000),
        ("installer.msi", 27_000_000), ("runtime.exe", 9_600_000),
    ]
    for i, (name, size) in enumerate(installers):
        entries.append((name, size, recent - timedelta(days=3 * i)))
    # archives (16), including a photos.zip copy chain
    archives = [
        "backup.zip", "photos.zip", "photos (1).zip", "data.tar",
        "logs.gz", "bundle.tgz", "archive.7z", "old.rar",
        "project.zip", "assets.zip", "fonts.zip", "videos.zip",
        "docs.tar", "misc.zip", "export.zip", "stuff.zip",
    ]
    for i, name in enumerate(archives):
        entries.append(
            (name, 2_000_000 + i * 731_000,
             recent - timedelta(days=20 + 5 * i))
        )
    # duplicate downloads: a report.pdf chain and a song.mp3 pair
    entries.append(("report.pdf", 640_000, recent - timedelta(days=2)))
    entries.append(("report (1).pdf", 640_000, recent - timedelta(days=1)))
    entries.append(("report copy.pdf", 640_000, recent))
    entries.append(("song.mp3", 5_100_000, recent - timedelta(days=9)))
    entries.append(("song (1).mp3", 5_100_000, recent - timedelta(days=8)))
    # one torrent, two receipts, one genuinely old file
    entries.append(("linux.iso.torrent", 42_000, recent - timedelta(days=30)))
    entries.append(("invoice-2026-041.pdf", 88_000, recent - timedelta(days=12)))
    entries.append(("receipt-store.png", 210_000, recent - timedelta(days=6)))
    entries.append(("readme-2019.txt", 4_100, datetime(2022, 3, 4, 9, 15)))
    # filler: documents, media, and images across 2025 and 2026
    filler_exts = ["pdf", "png", "mp3", "mp4", "docx", "txt", "jpg", "csv"]
    for i in range(80):
        ext = filler_exts[i % len(filler_exts)]
        when = datetime(2025, 1, 6, 10, 0) + timedelta(days=(i * 7) % 640)
        entries.append((f"download-{i + 1:03d}.{ext}",
                        10_000 + i * 137_913, when))
    for name, size, when in entries:
        write_sized(folder, name, size, when)
    return len(entries)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "Downloads"
        folder.mkdir()
        count = build_fixture(folder)
        history = Path(tmp) / "history.jsonl"
        original = sj.history_path
        sj.history_path = lambda: history
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = sj.main([str(folder), "--court", "downloads"])
        finally:
            sj.history_path = original
    if code != 0:
        print(f"demo run exited {code}", file=sys.stderr)
        return 1
    target = REPO / "examples" / "downloads-demo-output.txt"
    target.write_text(out.getvalue(), encoding="utf-8")
    print(f"wrote {target} from a fixture of {count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
