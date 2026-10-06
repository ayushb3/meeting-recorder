"""Scrape a meeting transcript from a Stream recording page.

The transcript panel keeps only part of the transcript in the DOM, so copying a
long one by hand takes many separate operations. This automates that.

The panel scrolls itself to follow the video's playhead, and that sync — not
scrolling — is what mounts rows. So this seeks the (paused) video across the
timeline and harvests whatever the panel renders at each position, keyed by each
row's own ordinal. Setting scrollTop does nothing; that was measured, not guessed.

Usage:
    python scripts/stream_transcript.py "<recording URL>" --out transcript.txt
    python scripts/stream_transcript.py "<recording URL>" --note

    Quote the URL — Stream URLs contain '&'.

Requires playwright in the venv (not in requirements.txt — the app must not take
a browser dependency):

    .venv/bin/pip install playwright

You sign in by hand in the window that opens, and open the transcript panel
yourself. This script never sees, stores or types a password.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# scripts/ is not a package, so make the repo importable for the shared modules.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from notes.frames import frame_filename, interleave_frames, place_frames  # noqa: E402,F401

ROW ='[id^="sub-entry-"]'
PROFILE_DIR = Path.home() / ".cache" / "meeting-recorder-spike" / "chrome-profile"

# "Surname, Firstname 1 hours 2 minutes 3 seconds" — anchored at the end. The
# name may contain commas, so the timestamp suffix is the only reliable anchor.
# Every unit is optional because the panel omits zero-valued trailing units:
# a line at exactly 18:00 is labelled "Okafor, Dara 18 minutes", with no seconds.
_TIME_RE = re.compile(
    r"\s*(?:(\d+)\s+hours?)?\s*(?:(\d+)\s+minutes?)?\s*(?:(\d+)\s+seconds?)?\s*$"
)

HARVEST_JS = """els => els.map(e => {
    const g = e.closest('[role="group"]');
    return [e.getAttribute('aria-posinset'),
            g ? g.getAttribute('aria-label') : null,
            e.innerText];
})"""


class ScrapeError(Exception):
    """Raised when the transcript cannot be scraped completely."""


def _fail(code: str, message: str) -> int:
    print(f"\nERROR: {code} {message}", file=sys.stderr)
    return 1


def parse_label(label: str | None) -> tuple[str | None, int | None]:
    """'Surname, Firstname 0 minutes 3 seconds' -> ('Surname, Firstname', 3).

    Returns (None, None) for labels with no timestamp — notably the
    "X started transcription" system notice, which is not a transcript line.
    """
    if not label:
        return None, None
    match = _TIME_RE.search(label)
    # Every unit is optional, so the pattern also matches an empty suffix. A row
    # with no unit at all is the "X started transcription" notice, not a line.
    if not match or not any(match.groups()):
        return None, None
    speaker = label[: match.start()].strip()
    if not speaker:
        return None, None
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return speaker, hours * 3600 + minutes * 60 + seconds


def clean_text(raw: str, label: str | None) -> str:
    """Strip the duplicated accessibility label out of a row's visible text.

    The panel renders the speaker and timestamp both as screen-reader text and
    visibly, so the raw row repeats them before the utterance. Only strip that
    leading repetition — a blind replace would also delete a speaker's name from
    inside their own sentence.
    """
    text = " ".join(raw.split())
    if not label:
        return text

    speaker, _ = parse_label(label)
    duration_and_clock = (
        r"^(?:\d+\s+hours?\s*)?(?:\d+\s+minutes?\s*)?(?:\d+\s+seconds?\s*)?"
        r"(?:\d+:\d{2}\s*)?"
    )
    # Strip each repetition at most once, in the order the panel renders them:
    # speaker header, duration/clock, then the full a11y label. Stripping
    # greedily would also consume a speaker's name from their own sentence.
    for prefix in (" ".join(speaker.split()) if speaker else None,):
        if prefix and text.startswith(prefix):
            text = text[len(prefix) :].lstrip()
    text = re.sub(duration_and_clock, "", text).lstrip()
    collapsed = " ".join(label.split())
    if text.startswith(collapsed):
        text = text[len(collapsed) :].lstrip()
    return " ".join(text.split())


def format_lines(
    rows: dict[int, tuple[str | None, str]],
) -> tuple[list[str], list[int]]:
    """Rows -> ('[MM:SS] Speaker: text' lines, posinsets that produced no line).

    The only rows that *should* produce nothing are the "X started/stopped
    transcription" notices, whose label carries no timestamp. Anything else in
    the dropped list is a parsing failure the caller must not ignore — that is
    how a transcript silently loses lines while still looking complete.
    """
    lines: list[str] = []
    dropped: list[int] = []
    for posinset in sorted(rows):
        label, raw = rows[posinset]
        speaker, start = parse_label(label)
        text = clean_text(raw, label) if speaker else ""
        if speaker is None or start is None or not text:
            dropped.append(posinset)
            continue
        minutes, seconds = divmod(start, 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {speaker}: {text}")
    return lines, dropped


def is_system_notice(row: tuple[str | None, str]) -> bool:
    """A 'X started/stopped transcription' row — legitimately not a line."""
    label, raw = row
    if label and label.strip():
        return False
    return bool(re.search(r"(started|stopped) transcription", " ".join(raw.split())))


def find_gaps(collected: set[int], setsize: int) -> list[tuple[int, int]]:
    """Missing posinsets as (start, end) inclusive runs."""
    missing = sorted(set(range(1, setsize + 1)) - collected)
    if not missing:
        return []
    runs, start, prev = [], missing[0], missing[0]
    for value in missing[1:]:
        if value != prev + 1:
            runs.append((start, prev))
            start = value
        prev = value
    runs.append((start, prev))
    return runs


def _harvest(page) -> dict[int, tuple[str | None, str]]:
    """Every row currently mounted, keyed by aria-posinset."""
    out: dict[int, tuple[str | None, str]] = {}
    for posinset, label, text in page.locator(ROW).evaluate_all(HARVEST_JS):
        if posinset:
            out[int(posinset)] = (label, text or "")
    return out


def parse_timestamps(spec: str | None, duration: float | None = None) -> list[int]:
    """'7:46,19:40' or '1:02:15' -> [466, 1180] seconds, sorted and deduplicated.

    Raises ValueError on anything unparseable, or on a timestamp past *duration*
    — seeking past the end silently yields the last frame for every such entry,
    which looks like a working capture and is not.
    """
    if not spec:
        return []
    out: set[int] = set()
    for raw in spec.split(","):
        piece = raw.strip()
        if not piece:
            continue
        parts = piece.split(":")
        if not all(p.strip().isdigit() for p in parts) or not 1 <= len(parts) <= 3:
            raise ValueError(f"Not a timestamp: {piece!r}. Use MM:SS, H:MM:SS or seconds.")
        values = [int(p) for p in parts]
        seconds = 0
        for value in values:
            seconds = seconds * 60 + value
        if duration is not None and seconds > duration:
            raise ValueError(
                f"{piece} is past the end of the recording ({duration:.0f}s)."
            )
        out.add(seconds)
    return sorted(out)


def _seek(page, seconds: float) -> None:
    page.evaluate(
        "t => { const v = document.querySelector('video');"
        " if (v) { v.pause(); v.currentTime = t; } }",
        seconds,
    )


def _await_frame_ready(page, timeout_ms: int = 10_000) -> bool:
    """Wait for the player's own readiness rather than guessing a delay.

    After a seek the player may briefly show the previous frame; readyState >= 2
    with seeking finished is the signal that the new one has decoded.
    """
    waited = 0
    while waited < timeout_ms:
        state = page.evaluate(
            "() => { const v = document.querySelector('video');"
            " return v ? {ready: v.readyState, seeking: v.seeking} : null; }"
        )
        if state and state["ready"] >= 2 and not state["seeking"]:
            return True
        page.wait_for_timeout(200)
        waited += 200
    return False


def interval_timestamps(duration: float, every_s: int) -> list[int]:
    """Timestamps for --frames-every: 0, N, 2N ... strictly before the end.

    The interval is raised if it would exceed the frame cap, so a long recording
    with a small N cannot ask for thousands of seeks.
    """
    from pipeline.frames import effective_interval  # noqa: PLC0415

    if not duration or duration <= 0:
        raise ValueError(
            "Cannot tell how long the recording is, so --frames-every cannot "
            "pick timestamps. Use --frames-at instead."
        )
    step = effective_interval(every_s, int(duration))
    if step != every_s:
        print(f"  frame interval raised from {every_s}s to {step}s (frame cap)")
    return list(range(0, int(duration), step))


def dedupe_captured(
    frames: list[tuple[int, str]], frames_dir: Path
) -> list[tuple[int, str]]:
    """Drop near-duplicate captured frames and delete their files."""
    from pipeline.frames import dedupe_frames  # noqa: PLC0415

    kept = dedupe_frames([(s, frames_dir / n) for s, n in frames])
    kept_names = {path.name for _, path in kept}
    for _, name in frames:
        if name not in kept_names:
            (frames_dir / name).unlink(missing_ok=True)
    return [(s, path.name) for s, path in kept]


def capture_frames(page, seconds_list: list[int], out_dir: Path, extra_settle_ms: int) -> list[tuple[int, str]]:
    """Seek to each timestamp and screenshot the video element.

    Returns (seconds, filename) for each frame written. The element screenshot
    was verified to capture real content rather than a black rectangle; if that
    ever changes, the caller should notice empty files rather than silence.
    """
    if not seconds_list:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    video = page.locator("video").first
    captured: list[tuple[int, str]] = []

    for seconds in seconds_list:
        _seek(page, seconds)
        if not _await_frame_ready(page):
            print(f"    {seconds}s: player not ready, capturing anyway")
        page.wait_for_timeout(extra_settle_ms)
        name = frame_filename(seconds)
        path = out_dir / name
        try:
            video.screenshot(path=str(path))
        except Exception as exc:
            print(f"    {seconds}s: capture failed — {exc}")
            continue
        if path.exists() and path.stat().st_size > 0:
            captured.append((seconds, name))
            minutes, secs = divmod(seconds, 60)
            print(f"    [{minutes:02d}:{secs:02d}] {name}")
        else:
            print(f"    {seconds}s: wrote an empty file, skipping")

    return captured


def scrape(page, steps: int, settle_ms: int) -> tuple[dict[int, tuple[str | None, str]], int]:
    """Sweep the timeline, harvesting rows. Returns (rows, setsize)."""
    setsize_attr = page.locator(f"{ROW}[aria-setsize]").first.get_attribute("aria-setsize")
    if not setsize_attr:
        raise ScrapeError(
            "No aria-setsize on any row — cannot know how many lines to expect, so "
            "completeness cannot be verified. Refusing to emit a possibly-partial transcript."
        )
    setsize = int(setsize_attr)

    duration = page.evaluate(
        "() => { const v = document.querySelector('video'); return v ? v.duration : 0; }"
    )
    if not duration:
        raise ScrapeError("No <video> element — the transcript panel is driven by the player.")

    print(f"  {setsize} rows to collect, {duration:.0f}s of recording")
    collected = _harvest(page)
    step = duration / steps

    for i in range(1, steps + 2):
        if len(collected) >= setsize:
            break
        _seek(page, min(i * step, duration - 1))
        page.wait_for_timeout(settle_ms)
        collected.update(_harvest(page))
        if i % 10 == 0:
            print(f"    {len(collected)}/{setsize} after {i} steps")

    # Gap-fill: seek to the timestamps bracketing each missing run.
    for attempt in range(3):
        gaps = find_gaps(set(collected), setsize)
        if not gaps:
            break
        print(f"  filling {len(gaps)} gap(s), pass {attempt + 1}")
        for low, high in gaps:
            # Interpolate where the gap sits in the timeline from its neighbours.
            before = max((p for p in collected if p < low), default=None)
            after = min((p for p in collected if p > high), default=None)
            anchors = []
            for neighbour in (before, after):
                if neighbour is not None:
                    _, start = parse_label(collected[neighbour][0])
                    if start is not None:
                        anchors.append(start)
            if not anchors:
                continue
            for target in {min(anchors), sum(anchors) / len(anchors), max(anchors)}:
                _seek(page, max(0.0, min(target, duration - 1)))
                page.wait_for_timeout(settle_ms)
                collected.update(_harvest(page))

    return collected, setsize


def run(
    url: str,
    steps: int,
    settle_ms: int,
    login_timeout: int,
    frames_at: str | None = None,
    frames_dir: Path | None = None,
    capture_settle_ms: int = 400,
    frames_every: int | None = None,
) -> tuple[list[str], datetime | None, list[tuple[int, str]], int]:
    """Drive the browser and return (transcript_lines, recording_date, frames, duration_s)."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise ScrapeError(
            "playwright is not installed. Run:\n  .venv/bin/pip install playwright"
        ) from exc

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Profile: {PROFILE_DIR}")

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",  # branded channel — tenant policy is friendlier to it
                headless=False,  # Microsoft's login flow fails headless
                viewport=None,
            )
        except Exception as exc:
            raise ScrapeError(
                f"Could not launch Google Chrome via channel='chrome': {exc}\n"
                "  Verify /Applications/Google Chrome.app exists."
            ) from exc

        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                raise ScrapeError(f"Could not reach the URL: {exc}") from exc

            print("\n>>> Sign in by hand if prompted, then OPEN THE TRANSCRIPT PANEL")
            print(f">>> (player UI -> Transcript). Waiting up to {login_timeout}s...\n")

            waited = 0
            while waited < login_timeout and page.locator(ROW).count() == 0:
                page.wait_for_timeout(3000)
                waited += 3
                if waited % 30 == 0:
                    print(f"    ...still waiting ({waited}s)")

            if page.locator(ROW).count() == 0:
                counts = {
                    "aria-posinset": page.locator("[aria-posinset]").count(),
                    "role=group": page.locator('[role="group"]').count(),
                    "data-is-scrollable": page.locator('[data-is-scrollable="true"]').count(),
                }
                if any(counts.values()):
                    raise ScrapeError(
                        "Transcript-like markup is present but no rows matched "
                        f"'{ROW}'. Microsoft has changed the markup.\n"
                        f"  Present: {counts}\n"
                        "  Re-derive the row selector in DevTools."
                    )
                raise ScrapeError(
                    "No transcript markup found. The panel is probably not open — "
                    "open it in the Chrome window and re-run."
                )

            print("Transcript panel found. Sweeping...")
            collected, setsize = scrape(page, steps, settle_ms)

            gaps = find_gaps(set(collected), setsize)
            if gaps:
                raise ScrapeError(
                    f"Incomplete: {len(collected)}/{setsize} rows, missing {gaps}.\n"
                    "  Refusing to emit a partial transcript. Retry with more steps:\n"
                    f"    --steps {steps * 2}"
                )
            print(f"  complete: {len(collected)}/{setsize} rows")

            lines, dropped = format_lines(collected)
            unexpected = [p for p in dropped if not is_system_notice(collected[p])]
            if unexpected:
                detail = "\n".join(
                    f"    posinset={p}: label={collected[p][0]!r} "
                    f"text={' '.join(collected[p][1].split())[:70]!r}"
                    for p in unexpected[:10]
                )
                raise ScrapeError(
                    f"{len(unexpected)} row(s) were collected but produced no "
                    f"transcript line — they would be silently missing:\n{detail}\n"
                    "  Refusing to emit. The label format has probably changed."
                )
            skipped = len(dropped) - len(unexpected)
            print(f"  {len(lines)} lines ({skipped} transcription notice(s) skipped)")

            recorded_at = _recording_date(page)

            duration = page.evaluate(
                "() => { const v = document.querySelector('video');"
                " return v ? v.duration : 0; }"
            )
            # A live stream or unloaded metadata reports Infinity or NaN.
            if not isinstance(duration, (int, float)) or not math.isfinite(duration):
                duration = 0
            frames: list[tuple[int, str]] = []
            if frames_at or frames_every:
                try:
                    if frames_at:
                        wanted = parse_timestamps(frames_at, duration)
                    else:
                        wanted = interval_timestamps(duration, frames_every)
                except ValueError as exc:
                    raise ScrapeError(str(exc)) from exc
                if wanted and frames_dir is not None:
                    print(f"  capturing {len(wanted)} frame(s)")
                    frames = capture_frames(
                        page, wanted, frames_dir, capture_settle_ms
                    )
                    if len(frames) < len(wanted):
                        print(
                            f"  WARNING: {len(wanted) - len(frames)} frame(s) "
                            "could not be captured"
                        )
                    if frames_every and frames:
                        before = len(frames)
                        frames = dedupe_captured(frames, frames_dir)
                        print(f"  kept {len(frames)} of {before} after removing near-duplicates")

            _seek(page, 0)
            return lines, recorded_at, frames, int(duration)
        finally:
            ctx.close()


def _recording_date(page) -> datetime | None:
    """Best-effort recording date from the page, for the note's frontmatter."""
    try:
        body = page.inner_text("body")[:3000]
    except Exception:
        return None
    match = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", body)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y")
    except ValueError:
        return None


def write_vault_note(
    lines: list[str],
    dt: datetime,
    meeting_name: str | None,
    frames: list[tuple[int, str]] | None = None,
    frames_dir: Path | None = None,
    duration_seconds: int = 0,
    context: str | None = None,
) -> int:
    """Summarise and write a note, mirroring the recorded-meeting pipeline."""
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from config import USER_CONFIG_PATH, load_config  # noqa: PLC0415
    from notes.vault import (  # noqa: PLC0415
        log_to_daily_note,
        prepare_context,
        strip_unknown_links,
    )
    from notes.writer import week_folder, write_note  # noqa: PLC0415
    from summarizer.llm import (  # noqa: PLC0415
        LLMSettings,
        SummaryUnavailableError,
        suggest_title,
        summarize,
    )

    try:
        cfg = load_config(USER_CONFIG_PATH)
    except FileNotFoundError:
        return _fail("CONFIG", f"No config at {USER_CONFIG_PATH}. Run the app once first.")
    except ValueError as exc:
        # load_config also validates whisper paths, which this script never uses.
        return _fail("CONFIG", f"Config is invalid: {exc}")

    warning = None
    try:
        llm = LLMSettings.from_config(cfg)
        print(f"Summarising with {llm.label}...")
        summary_context, vault_ctx = prepare_context(cfg.vault, context, dt.date())
        summary = summarize(lines, llm, context=summary_context)
        if vault_ctx is not None:
            summary = strip_unknown_links(summary, vault_ctx.allowed_links)
        if not meeting_name:
            meeting_name = suggest_title(summary, llm)
    except SummaryUnavailableError as exc:
        summary = "⚠ Summary unavailable — no LLM was reachable during processing."
        warning = f"Summarizer unavailable: {exc}. Summary was not generated."
        print(f"  {warning}")

    timestamp = dt.strftime("%Y-%m-%d-%Hh%M")
    slug = re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", (meeting_name or "").strip().lower()))[:60]
    session_dir = cfg.output_dir / week_folder(dt) / (slug or timestamp)
    session_dir.mkdir(parents=True, exist_ok=True)

    if warning:
        (session_dir / "summarize.error").write_text(
            f"stage: summarize\nerror: {warning}\n"
        )

    # Move captured frames beside the note so Obsidian's embeds resolve, then
    # interleave them into the transcript at the points they belong to.
    note_lines = lines
    if frames and frames_dir is not None:
        landed = place_frames(frames, frames_dir, session_dir)
        if landed:
            note_lines = interleave_frames(lines, landed)
            print(f"  {len(landed)} frame(s) placed in the session folder")

    try:
        note_path = write_note(
            dt=dt,
            duration_seconds=duration_seconds,
            summary=summary,
            transcript_lines=note_lines,
            output_dir=session_dir,
            overwrite=True,
            meeting_name=meeting_name,
        )
    except Exception as exc:
        return _fail("WRITE_NOTE", str(exc))

    print(f"\nNote written: {note_path}")
    if cfg.vault is not None:
        try:
            if log_to_daily_note(cfg.vault, note_path, meeting_name or session_dir.name, dt):
                print("  Logged in the daily note")
        except Exception as exc:  # write-back must never fail a written note
            print(f"  Could not log to the daily note: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape a meeting transcript from a Stream recording page.",
        epilog='Quote the URL: stream_transcript.py "https://..."',
    )
    parser.add_argument("url", help="Recording URL from the address bar (quote it)")
    parser.add_argument("--out", type=Path, help="Write the transcript to this file")
    parser.add_argument("--note", action="store_true", help="Summarise and write a vault note")
    parser.add_argument("--name", help="Meeting title (otherwise the LLM suggests one)")
    parser.add_argument("--steps", type=int, default=50, help="Seek steps (default: 50)")
    parser.add_argument(
        "--settle-ms", type=int, default=900, help="Wait after each seek (default: 900)"
    )
    parser.add_argument(
        "--login-timeout", type=int, default=300, help="Wait for the panel (default: 300)"
    )
    parser.add_argument("--date", help="Recording date as YYYY-MM-DD (overrides detection)")
    parser.add_argument(
        "--context",
        help="Attendees, project names, terms — passed to the summarizer",
    )
    parser.add_argument(
        "--frames-at",
        help="Capture screenshare frames at these times, e.g. 7:46,19:40,1:02:15",
    )
    parser.add_argument(
        "--frames-every", type=int, metavar="SECONDS",
        help="Capture a frame every N seconds, dropping near-duplicates "
             "(cannot be combined with --frames-at)",
    )
    parser.add_argument(
        "--capture-settle-ms", type=int, default=400,
        help="Extra wait after the player reports ready (default: 400)",
    )
    args = parser.parse_args(argv)

    if not args.url.lower().startswith(("http://", "https://")):
        return _fail(
            "SETUP",
            f"Not a URL: {args.url!r}\n"
            '  Paste the recording URL in quotes: "https://..."',
        )
    if args.frames_at and args.frames_every:
        return _fail("SETUP", "Use either --frames-at or --frames-every, not both.")
    if args.frames_every is not None and args.frames_every < 1:
        return _fail("SETUP", "--frames-every must be at least 1 second.")
    if not args.out and not args.note:
        print("Note: neither --out nor --note given; printing to stdout only.\n")

    try:
        wants_frames = bool(args.frames_at or args.frames_every)
        frames_dir = Path(tempfile.mkdtemp(prefix='mr-frames-')) if wants_frames else None
        lines, detected_date, frames, duration = run(
            args.url, args.steps, args.settle_ms, args.login_timeout,
            frames_at=args.frames_at, frames_dir=frames_dir,
            capture_settle_ms=args.capture_settle_ms,
            frames_every=args.frames_every,
        )
    except ScrapeError as exc:
        return _fail("SCRAPE", str(exc))

    if not lines:
        return _fail("SCRAPE", "No transcript lines parsed — every row failed to parse.")

    print(f"\n{len(lines)} transcript lines.")

    if args.out:
        args.out.write_text("\n".join(lines) + "\n")
        print(f"Transcript written: {args.out}")

    if not args.out and not args.note:
        print()
        for line in lines:
            print(line)

    if args.note:
        if args.date:
            try:
                dt = datetime.strptime(args.date, "%Y-%m-%d")
            except ValueError:
                return _fail("SETUP", f"--date must be YYYY-MM-DD, got {args.date!r}")
        elif detected_date:
            dt = detected_date
            print(f"Recording date: {dt:%Y-%m-%d} (detected from the page)")
        else:
            return _fail(
                "SETUP",
                "Could not detect the recording date, and the note's date decides which "
                "day it files under.\n  Pass it explicitly: --date YYYY-MM-DD",
            )
        return write_vault_note(
            lines, dt, args.name, frames, frames_dir,
            duration_seconds=duration, context=args.context,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
