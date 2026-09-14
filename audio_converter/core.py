from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


SUPPORTED_INPUTS = {
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac",
    ".opus", ".wma", ".aiff", ".aif", ".ac3", ".webm", ".mp4",
}

FORMATS = {
    "MP3": {"extension": ".mp3", "codec": "libmp3lame", "lossless": False},
    "WAV": {"extension": ".wav", "codec": "pcm_s16le", "lossless": True},
    "FLAC": {"extension": ".flac", "codec": "flac", "lossless": True},
    "OGG": {"extension": ".ogg", "codec": "libvorbis", "lossless": False},
    "M4A": {"extension": ".m4a", "codec": "aac", "lossless": False},
    "AAC": {"extension": ".aac", "codec": "aac", "lossless": False},
    "OPUS": {"extension": ".opus", "codec": "libopus", "lossless": False},
}

BITRATES = ("96k", "128k", "192k", "256k", "320k")


class ConversionError(RuntimeError):
    pass


class ConversionCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversionOptions:
    output_format: str = "MP3"
    bitrate: str = "192k"
    sample_rate: str | None = None
    channels: str | None = None
    normalize: bool = False
    preserve_metadata: bool = True
    audio_filter: str | None = None


def find_ffmpeg() -> str | None:
    """Return an FFmpeg executable from PATH or imageio-ffmpeg."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        return None


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_INPUTS


def unique_output_path(source: Path, output_dir: Path, output_format: str) -> Path:
    extension = FORMATS[output_format]["extension"]
    candidate = output_dir / f"{source.stem}{extension}"
    if candidate.resolve() == source.resolve() or candidate.exists():
        candidate = output_dir / f"{source.stem}_converted{extension}"
    number = 2
    while candidate.exists():
        candidate = output_dir / f"{source.stem}_converted_{number}{extension}"
        number += 1
    return candidate


def build_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    options: ConversionOptions,
) -> list[str]:
    if options.output_format not in FORMATS:
        raise ValueError(f"Unsupported output format: {options.output_format}")

    format_info = FORMATS[options.output_format]
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        str(format_info["codec"]),
    ]
    if not format_info["lossless"]:
        command.extend(["-b:a", options.bitrate])
    if options.sample_rate:
        command.extend(["-ar", options.sample_rate])
    if options.channels:
        command.extend(["-ac", options.channels])
    audio_filters: list[str] = []
    if options.audio_filter:
        audio_filters.append(options.audio_filter)
    if options.normalize:
        audio_filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
    if audio_filters:
        command.extend(["-af", ",".join(audio_filters)])
    if not options.preserve_metadata:
        command.extend(["-map_metadata", "-1"])
    command.extend(["-progress", "pipe:1", "-nostats", str(destination)])
    return command


def probe_duration(ffmpeg: str, source: Path) -> float | None:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(source)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def convert_file(
    ffmpeg: str,
    source: Path,
    destination: Path,
    options: ConversionOptions,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    duration = probe_duration(ffmpeg, source)
    command = build_command(ffmpeg, source, destination, options)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr_text = ""
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if should_cancel and should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                destination.unlink(missing_ok=True)
                raise ConversionCancelled("Conversion was cancelled")
            if duration and line.startswith("out_time_ms="):
                try:
                    # Despite its name, current FFmpeg reports this value in microseconds.
                    elapsed = int(line.split("=", 1)[1].strip()) / 1_000_000
                    if on_progress:
                        on_progress(min(1.0, elapsed / duration))
                except ValueError:
                    pass
        _, stderr_text = process.communicate()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    if process.returncode != 0:
        destination.unlink(missing_ok=True)
        useful_lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
        detail = useful_lines[-1] if useful_lines else "FFmpeg returned an unknown error"
        raise ConversionError(detail)
    if on_progress:
        on_progress(1.0)
