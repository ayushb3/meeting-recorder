# pipeline/importer.py
"""Helpers for importing a local audio or video file into the pipeline.

These are pure functions that shell out to ffmpeg/ffprobe; they do not touch
any UI state.  Both binaries are expected at /opt/homebrew/bin/ on macOS, but
the callers may pass explicit paths for testability.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG_DEFAULT = Path("/opt/homebrew/bin/ffmpeg")
FFPROBE_DEFAULT = Path("/opt/homebrew/bin/ffprobe")


class AudioImportError(Exception):
    """Raised when an imported file cannot be prepared for transcription."""


def get_duration_seconds(
    source: Path,
    ffprobe: Path = FFPROBE_DEFAULT,
) -> int:
    """Return the duration of *source* in whole seconds using ffprobe.

    Raises ImportError if ffprobe fails or returns an unparseable result.
    """
    cmd = [
        str(ffprobe),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, OSError) as exc:
        raise AudioImportError(f"ffprobe not found or failed to start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioImportError(f"ffprobe timed out on {source}") from exc

    if result.returncode != 0:
        raise AudioImportError(
            f"ffprobe returned {result.returncode} for {source.name}: {result.stderr.strip()}"
        )

    raw = result.stdout.strip()
    try:
        return int(float(raw))
    except (ValueError, TypeError) as exc:
        raise AudioImportError(
            f"ffprobe returned an unparseable duration {raw!r} for {source.name}"
        ) from exc


def _is_16k_mono_wav(source: Path) -> bool:
    """Return True if *source* is already a 16 kHz mono PCM WAV.

    Uses ffprobe to check stream metadata.  Returns False on any error so
    that callers fall through to conversion.
    """
    cmd = [
        str(FFPROBE_DEFAULT),
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,codec_name",
        "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        return False

    if result.returncode != 0:
        return False

    # output: codec_name,sample_rate,channels  e.g.  pcm_s16le,16000,1
    raw = result.stdout.strip()
    parts = raw.split(",")
    if len(parts) < 3:
        return False
    codec, rate, channels = parts[0].strip(), parts[1].strip(), parts[2].strip()
    return codec == "pcm_s16le" and rate == "16000" and channels == "1"


def prepare_audio(
    source: Path,
    dest_dir: Path,
    ffmpeg: Path = FFMPEG_DEFAULT,
    ffprobe: Path = FFPROBE_DEFAULT,
) -> Path:
    """Return a 16 kHz mono WAV path suitable for whisper.cpp.

    If *source* is already a 16 kHz mono PCM WAV, return *source* unchanged
    (no copy, no conversion).  Otherwise transcode into *dest_dir* using ffmpeg
    and return the new path.

    The converted file is always placed inside *dest_dir*, never beside the
    original.  *dest_dir* is created if it does not exist.
    """
    if not source.exists():
        raise AudioImportError(f"Source file not found: {source}")

    suffix = source.suffix.lower()
    is_wav = suffix == ".wav"

    if is_wav and _is_16k_mono_wav(source):
        log.info("Source is already 16 kHz mono WAV — skipping conversion: %s", source.name)
        return source

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / (source.stem + "-import.wav")

    log.info("Converting %s → %s (16 kHz mono WAV)", source.name, out_path.name)
    cmd = [
        str(ffmpeg),
        "-i", str(source),
        "-vn",             # drop video stream (no-op for audio-only files)
        "-ac", "1",        # mono
        "-ar", "16000",    # 16 kHz
        "-c:a", "pcm_s16le",
        "-y",              # overwrite if dest exists
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except (FileNotFoundError, OSError) as exc:
        raise AudioImportError(f"ffmpeg not found or failed to start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioImportError(f"ffmpeg timed out converting {source.name}") from exc

    if result.returncode != 0:
        raise AudioImportError(
            f"ffmpeg returned {result.returncode} converting {source.name}: "
            f"{result.stderr[-400:].strip()}"
        )

    if not out_path.exists():
        raise AudioImportError(f"ffmpeg produced no output file at {out_path}")

    return out_path
