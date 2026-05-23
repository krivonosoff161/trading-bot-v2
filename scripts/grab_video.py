"""Grab a video (Instagram reel, YouTube, ...) into the next numbered "темаN"
folder under docs/инста трасткбрикция/ and transcribe + screenshot it there.

One folder per clip, self-contained, matching the existing layout:
    docs/инста трасткбрикция/темаN/
        N.mp4
        N_transcript_clean.txt
        N_transcript_raw.txt
        N_screens/
        meta.txt

Usage:
    python scripts/grab_video.py <url> [--screens N] [--model small]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = ROOT / "docs" / "инста трасткбрикция"
TRANSCRIBE = ROOT / "scripts" / "transcribe_and_capture.py"
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov"}


def next_slot() -> tuple[Path, str]:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    used = [
        int(m.group(1))
        for p in BASE_DIR.iterdir()
        if p.is_dir() and (m := re.search(r"(\d+)", p.name))
    ]
    number = max(used, default=0) + 1
    folder = BASE_DIR / f"тема{number}"
    folder.mkdir()
    return folder, str(number)


def download(url: str, folder: Path, base: str) -> Path:
    subprocess.run(
        [sys.executable, "-m", "yt_dlp", "-o", str(folder / f"{base}.%(ext)s"), url],
        check=True,
    )
    videos = [p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTS]
    if not videos:
        raise RuntimeError("yt-dlp downloaded no video (image-only post? — screenshot it instead).")
    return videos[0]


def transcribe(video: Path, screens: int, model: str) -> None:
    subprocess.run(
        [sys.executable, str(TRANSCRIBE), str(video), "--screens", str(screens), "--model", model],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Grab a video into its own тема-folder and transcribe it.")
    parser.add_argument("url", help="video URL (Instagram reel, YouTube, etc.)")
    parser.add_argument("--screens", type=int, default=12)
    parser.add_argument("--model", default="small")
    args = parser.parse_args()

    folder, base = next_slot()
    (folder / "meta.txt").write_text(
        f"url: {args.url}\ndownloaded: {date.today().isoformat()}\n", encoding="utf-8"
    )
    print(f"Folder: {folder}")

    video = download(args.url, folder, base)
    transcribe(video, args.screens, args.model)
    print(f"Done -> {folder}")


if __name__ == "__main__":
    main()
