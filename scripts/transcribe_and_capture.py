from __future__ import annotations

import argparse
import math
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from faster_whisper import WhisperModel


def format_ts(seconds: float) -> str:
    total_ms = int(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def probe_duration(ffmpeg_exe: str, video_path: Path) -> float:
    result = subprocess.run(
        [ffmpeg_exe, "-i", str(video_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    for line in result.stderr.splitlines():
        line = line.strip()
        if not line.startswith("Duration:"):
            continue
        raw = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
        hh, mm, rest = raw.split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(rest)
    raise RuntimeError("Could not determine video duration.")


def extract_audio(ffmpeg_exe: str, video_path: Path, audio_path: Path) -> None:
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )


def transcribe(audio_path: Path, model_name: str) -> tuple[str, list[dict[str, float | str]]]:
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language="ru",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=True,
    )
    rows: list[dict[str, float | str]] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        rows.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
            }
        )
    return info.language, rows


def build_clean_text(rows: list[dict[str, float | str]]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    previous_end: float | None = None

    for row in rows:
        start = float(row["start"])
        end = float(row["end"])
        text = str(row["text"])

        gap = 0.0 if previous_end is None else start - previous_end
        if current and gap > 1.5:
            paragraphs.append(" ".join(current).strip())
            current = []

        current.append(text)
        previous_end = end

    if current:
        paragraphs.append(" ".join(current).strip())

    return "\n\n".join(paragraphs).strip() + "\n"


def build_raw_text(language: str, rows: list[dict[str, float | str]]) -> str:
    lines = [f"language={language}"]
    for row in rows:
        lines.append(
            f"[{format_ts(float(row['start']))}-{format_ts(float(row['end']))}] {row['text']}"
        )
    return "\n".join(lines) + "\n"


def screenshot_times(duration: float, count: int) -> list[float]:
    if count <= 1:
        return [max(0.0, duration / 2)]

    start = min(5.0, max(0.0, duration * 0.05))
    end = max(start, duration - min(5.0, duration * 0.05))
    if math.isclose(start, end):
        return [duration / 2]

    step = (end - start) / (count - 1)
    return [round(start + index * step, 3) for index in range(count)]


def extract_screenshots(ffmpeg_exe: str, video_path: Path, output_dir: Path, times: list[float]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "screens_index.txt"
    index_lines: list[str] = []

    for idx, timestamp in enumerate(times, start=1):
        out_file = output_dir / f"screen_{idx:02d}_{format_ts(timestamp).replace(':', '-').replace('.', '_')}.jpg"
        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(out_file),
            ],
            check=True,
            capture_output=True,
        )
        index_lines.append(f"{out_file.name}\t{format_ts(timestamp)}")

    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return index_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--screens", type=int, default=8)
    args = parser.parse_args()

    video_path = args.video_path.resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    duration = probe_duration(ffmpeg_exe, video_path)
    base_name = video_path.stem
    parent = video_path.parent

    raw_path = parent / f"{base_name}_transcript_raw.txt"
    clean_path = parent / f"{base_name}_transcript_clean.txt"
    shots_dir = parent / f"{base_name}_screens"

    with tempfile.TemporaryDirectory() as temp_dir:
        audio_path = Path(temp_dir) / f"{base_name}.wav"
        extract_audio(ffmpeg_exe, video_path, audio_path)
        language, rows = transcribe(audio_path, args.model)

    raw_path.write_text(build_raw_text(language, rows), encoding="utf-8")
    clean_path.write_text(build_clean_text(rows), encoding="utf-8")
    index_path = extract_screenshots(ffmpeg_exe, video_path, shots_dir, screenshot_times(duration, args.screens))

    print(f"Saved raw transcript: {raw_path}")
    print(f"Saved clean transcript: {clean_path}")
    print(f"Saved screenshots: {shots_dir}")
    print(f"Saved screenshot index: {index_path}")


if __name__ == "__main__":
    main()
