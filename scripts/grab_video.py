"""Grab one or more videos (Instagram reels, YouTube, ...) into their own
numbered "темаN" folders under docs/инста трасткбрикция/ and transcribe +
screenshot each there.

One folder per clip, self-contained, matching the existing layout:
    docs/инста трасткбрикция/темаN/
        N.mp4
        N_transcript_clean.txt
        N_transcript_raw.txt
        N_screens/
        meta.txt

Pass several URLs at once — they run sequentially, a failed one (e.g. an
image-only carousel) is skipped and its empty folder is removed.

Usage:
    python scripts/grab_video.py <url> [<url> ...] [--screens N] [--model small]
"""
from __future__ import annotations

import argparse
import re
import shutil
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
        raise RuntimeError("no video downloaded (image-only post? — screenshot it instead)")
    return videos[0]


def transcribe(video: Path, screens: int, model: str) -> None:
    subprocess.run(
        [sys.executable, str(TRANSCRIBE), str(video), "--screens", str(screens), "--model", model],
        check=True,
    )


def grab_one(url: str, screens: int, model: str) -> bool:
    folder, base = next_slot()
    (folder / "meta.txt").write_text(
        f"url: {url}\ndownloaded: {date.today().isoformat()}\n", encoding="utf-8"
    )
    try:
        video = download(url, folder, base)
        transcribe(video, screens, model)
        print(f"OK   {folder.name} <- {url}")
        return True
    except Exception as exc:  # skip + clean up, keep the batch going
        print(f"FAIL {url}: {exc}")
        shutil.rmtree(folder, ignore_errors=True)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Grab video(s) into тема-folders and transcribe.")
    parser.add_argument("urls", nargs="*", help="video URLs (or use --from-file)")
    parser.add_argument("--from-file", type=Path, help="text file with one URL per line")
    parser.add_argument("--screens", type=int, default=12)
    parser.add_argument("--model", default="small")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.from_file:
        urls += [ln.strip() for ln in args.from_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    urls = list(dict.fromkeys(urls))  # dedupe, keep order
    if not urls:
        parser.error("no URLs (pass URLs or --from-file)")

    ok = sum(grab_one(url, args.screens, args.model) for url in urls)
    print(f"Done: {ok}/{len(urls)} ok")


if __name__ == "__main__":
    main()
