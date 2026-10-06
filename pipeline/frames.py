# pipeline/frames.py
"""Extract still frames from an imported video so a note can show what was shown.

A screenshare meeting loses most of its content in the transcript alone. This
pulls a frame every N seconds with ffmpeg and drops the ones that barely differ
from the last kept frame, since a shared screen mostly holds still.

Pure functions that shell out to ffmpeg/ffprobe; no UI state. The user's source
file is only ever read.
"""
from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

from notes.frames import frame_filename
from pipeline.importer import FFMPEG_DEFAULT, FFPROBE_DEFAULT, AudioImportError

log = logging.getLogger(__name__)

# Never emit more than this many raw frames; a long recording at a short
# interval would otherwise write thousands of PNGs.
MAX_FRAMES = 600

# Mean absolute difference (0-255) between downscaled grayscale frames below
# which a frame counts as a duplicate of the last kept one. Measured on two real
# 10-second-interval captures (127 and 131 frames): consecutive-frame medians
# were ~0.15-0.2, and 0.5 kept 18 and 58 frames. 1.5 started dropping frames
# where a screenshare gained a few lines of text, so it is deliberately low.
DEDUPE_THRESHOLD = 0.5

_THUMB = (64, 64)


def has_video(source: Path, ffprobe: Path = FFPROBE_DEFAULT) -> bool:
    """True when *source* has a real video stream (not just embedded cover art)."""
    cmd = [
        str(ffprobe), "-v", "error",
        "-select_streams", "v",
        "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
        "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    # One "video,<attached_pic>" row per video stream.
    for row in result.stdout.splitlines():
        parts = [p.strip() for p in row.split(",")]
        if parts and parts[0] == "video" and (len(parts) < 2 or parts[1] != "1"):
            return True
    return False


def effective_interval(every_s: int, duration_s: int) -> int:
    """Raise the interval if *every_s* would exceed MAX_FRAMES for this duration."""
    if every_s < 1:
        raise ValueError("Frame interval must be at least 1 second.")
    if duration_s <= 0:
        return every_s
    return max(every_s, math.ceil(duration_s / MAX_FRAMES))


def _thumbnail(path: Path):
    from PIL import Image  # noqa: PLC0415

    with Image.open(path) as img:
        return list(img.convert("L").resize(_THUMB).tobytes())


def _mean_abs_diff(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def dedupe_frames(
    frames: list[tuple[int, Path]], threshold: float = DEDUPE_THRESHOLD
) -> list[tuple[int, Path]]:
    """Keep a frame only if it differs enough from the last *kept* frame.

    Comparing against the last kept frame, not the previous one, stops a slow
    fade from slipping through as many tiny steps. Without Pillow, nothing is
    dropped — duplicates are noisy but lose no content.
    """
    try:
        import PIL  # noqa: F401, PLC0415
    except ImportError:
        log.warning("Pillow not installed; frames will not be de-duplicated")
        return frames

    kept: list[tuple[int, Path]] = []
    last: list[int] | None = None
    for seconds, path in frames:
        try:
            thumb = _thumbnail(path)
        except Exception as exc:  # corrupt frame: skip it rather than abort
            log.warning("Could not read frame %s: %s", path.name, exc)
            continue
        if last is None or _mean_abs_diff(last, thumb) >= threshold:
            kept.append((seconds, path))
            last = thumb
    return kept


def extract_frames(
    source: Path,
    out_dir: Path,
    every_s: int,
    duration_s: int = 0,
    ffmpeg: Path = FFMPEG_DEFAULT,
    ffprobe: Path = FFPROBE_DEFAULT,
) -> list[tuple[int, str]]:
    """Write a de-duplicated frame every *every_s* seconds into *out_dir*.

    Returns (seconds, filename) pairs named by timestamp. Returns [] for a source
    with no video. Raises AudioImportError if ffmpeg itself fails.
    """
    if not has_video(source, ffprobe):
        log.info("No video stream in %s; skipping frame capture", source.name)
        return []

    step = effective_interval(every_s, duration_s)
    if step != every_s:
        log.info("Frame interval raised from %ds to %ds (frame cap)", every_s, step)

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_pattern = out_dir / "raw-%05d.png"
    cmd = [
        str(ffmpeg), "-v", "error", "-y",
        "-i", str(source),
        "-an",
        "-vf", f"fps=1/{step},scale='min(1280,iw)':-2",
        "-start_number", "0",
        str(raw_pattern),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioImportError(f"ffmpeg frame extraction failed: {exc}") from exc
    if result.returncode != 0:
        raise AudioImportError(
            f"ffmpeg returned {result.returncode} extracting frames: {result.stderr.strip()}"
        )

    raw = sorted(out_dir.glob("raw-*.png"))
    candidates = [(index * step, path) for index, path in enumerate(raw)]
    kept = dedupe_frames(candidates)

    out: list[tuple[int, str]] = []
    for seconds, path in kept:
        name = frame_filename(seconds)
        path.replace(out_dir / name)
        out.append((seconds, name))
    for _, path in candidates:
        if path.exists():
            path.unlink()
    log.info("Captured %d frame(s) (%d before de-duplication)", len(out), len(raw))
    return out
