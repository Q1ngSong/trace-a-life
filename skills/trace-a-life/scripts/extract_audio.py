#!/usr/bin/env python3
"""Extract and normalize the first audio track from a video with FFmpeg."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_executable(value: str | None, default: str) -> str:
    candidate = value or shutil.which(default)
    if not candidate:
        raise RuntimeError(
            f"{default} was not found. Install FFmpeg (for example: brew install ffmpeg) "
            f"or pass --{default} /absolute/path/to/{default}."
        )
    path = Path(candidate).expanduser()
    if path.parent != Path(".") and not path.is_file():
        raise RuntimeError(f"{default} executable does not exist: {path}")
    return str(path)


def build_ffmpeg_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]


def probe_duration(ffprobe: str | None, audio_path: Path) -> float | None:
    if not ffprobe:
        return None
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


def find_ffprobe(ffmpeg: str, explicit: str | None) -> str | None:
    if explicit:
        return resolve_executable(explicit, "ffprobe")
    sibling = Path(ffmpeg).with_name("ffprobe")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")


def extract_audio(
    source: Path,
    output: Path,
    *,
    ffmpeg: str,
    ffprobe: str | None = None,
    sample_rate: int = 16_000,
    channels: int = 1,
    overwrite: bool = False,
) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"input video does not exist: {source}")
    if source == output:
        raise ValueError("input and output must be different files")
    if output.suffix.lower() != ".wav":
        raise ValueError("output must use the .wav extension")
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {output}; pass --overwrite to replace it")
    if not 8_000 <= sample_rate <= 192_000:
        raise ValueError("sample rate must be between 8000 and 192000 Hz")
    if not 1 <= channels <= 8:
        raise ValueError("channels must be between 1 and 8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}.", suffix=".wav", dir=output.parent, delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
        command = build_ffmpeg_command(
            ffmpeg,
            source,
            temp_path,
            sample_rate=sample_rate,
            channels=channels,
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown FFmpeg error"
            raise RuntimeError(f"audio extraction failed: {detail}")
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise RuntimeError("audio extraction failed: FFmpeg produced an empty file")
        duration = probe_duration(ffprobe, temp_path)
        os.replace(temp_path, output)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return {
        "schema_version": "trace-a-life/audio-extraction/1",
        "source": source.name,
        "source_bytes": source.stat().st_size,
        "source_sha256": sha256_file(source),
        "output": output.name,
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256_file(output),
        "duration_seconds": duration,
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "codec": "pcm_s16le",
    }


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Extract the first audio track as an ASR-ready PCM WAV file."
    )
    argument_parser.add_argument("input", type=Path, help="source video file")
    argument_parser.add_argument("--output", "-o", type=Path, help="output .wav path")
    argument_parser.add_argument("--sample-rate", type=int, default=16_000)
    argument_parser.add_argument("--channels", type=int, default=1)
    argument_parser.add_argument("--ffmpeg", help="explicit FFmpeg executable")
    argument_parser.add_argument("--ffprobe", help="explicit ffprobe executable")
    argument_parser.add_argument("--manifest", type=Path, help="optional JSON receipt path")
    argument_parser.add_argument("--overwrite", action="store_true")
    argument_parser.add_argument(
        "--dry-run", action="store_true", help="print the FFmpeg command without executing it"
    )
    return argument_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    source = args.input.expanduser()
    output = args.output or source.with_suffix(".asr.wav")
    try:
        ffmpeg = resolve_executable(args.ffmpeg, "ffmpeg")
        if args.dry_run:
            payload = {
                "executed": False,
                "command": build_ffmpeg_command(
                    ffmpeg,
                    source,
                    output,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                ),
            }
        else:
            ffprobe = find_ffprobe(ffmpeg, args.ffprobe)
            payload = extract_audio(
                source,
                output,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                sample_rate=args.sample_rate,
                channels=args.channels,
                overwrite=args.overwrite,
            )
        if args.manifest:
            atomic_write_json(args.manifest, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
