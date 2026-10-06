# Handoff — capturing screenshare frames from a Stream recording

**Written:** 2026-09-21 · **For:** a fresh local session on macOS
**Repo state:** `07bc2bd`, working tree clean

Self-contained. You do not need the conversation that produced it.

---

## 0. The problem

A transcript records what people said. It loses what they *showed*. For a meeting that is
mostly a screenshare — a dashboard walkthrough, a design review, a slide deck — the transcript
is the smaller half of the content. Lines like "if you see this over here" and "this is how the
older trace used to look" are unreadable without the frame they refer to.

The goal: alongside the scraped transcript, capture frames of the shared screen and embed them
in the note at the timestamps they belong to, so the note reads as slides interleaved with
what was said about them.

---

## 1. Why this is small

`scripts/stream_transcript.py` already does the hard part.

The scraper seeks the **paused** video across the timeline and harvests the transcript rows the
panel mounts at each position. That mechanism — a controlled seek, a settle wait, a read — is
exactly what a frame capture needs. The addition is one more read at each stop.

Relevant existing pieces:

- **`_seek(page, seconds)` (`scripts/stream_transcript.py:168`)** sets `video.currentTime` via
  `page.evaluate`. Already proven to move the playhead reliably.
- **`scrape(page, steps, settle_ms)` (`:176`)** owns the seek loop, reads `video.duration`
  (`:186`), and errors clearly when there is no `<video>` (`:190`).
- **`--settle-ms` (default 900, `:420`)** is the existing wait-after-seek. A frame capture needs
  this same settle, possibly longer — see §4.
- **Auth is solved.** The script drives the real Google Chrome against a persistent profile at
  `~/.cache/meeting-recorder-spike/chrome-profile`, and the user signs in by hand. No new auth
  work. The script never sees a password, and this must stay true.
- **`playwright` is in `.venv` but deliberately not in `requirements.txt`** — the menu-bar app
  must not take a browser dependency. This feature lives in the same script and inherits that
  rule. **Do not add playwright to requirements.**

---

## 2. Check the simpler path first

**Before building any of this, check whether the recording can be downloaded.**

Stream often offers a download on the recording. If it is enabled, the whole feature collapses
into the existing local-import path: download the `.mp4`, run ffmpeg over it, extract frames
with no browser, no auth and no player quirks. That is strictly more robust.

```bash
ffmpeg -i recording.mp4 -vf "select='gt(scene,0.3)',showinfo" -vsync vfr slide-%03d.png
```

The transcript scraper exists precisely because the transcript **Download** button is disabled
by tenant policy (`docs/stream-transcripts.md`). The *video* download may not be. They are
separate permissions. **Verify before assuming.**

Build the browser path only if video download is also blocked. If it is available, the better
feature is frame extraction in the local-import pipeline
(`docs/handoffs/2026-09-17-import-local-recording.md`), which helps every video import rather
than Stream alone.

---

## 3. The first thing to verify

**A `<video>` element may screenshot black.** Protected playback paths often render outside
what the page compositor exposes to a screenshot. Stream is usually fine, but this is unproven
here and it decides whether the feature is possible at all.

Spend five minutes on this before writing anything else:

```python
# with the page open and signed in, video paused at a frame with a visible share
el = page.locator("video")
el.screenshot(path="/tmp/probe.png")
```

Open `/tmp/probe.png`. If it is black or empty, element screenshots are blocked. Fallbacks, in
order:

1. **Full-page screenshot cropped to the video's bounding box** (`el.bounding_box()`). Composits
   differently and sometimes succeeds where element capture fails.
2. **`page.screenshot()` with the player fullscreened**, then crop.
3. **Capture the canvas** if the player draws to one — inspect the DOM for `<canvas>` rather
   than assuming `<video>`.
4. If all fail, stop and report it. Do not ship a feature that writes black PNGs.

---

## 4. Design

### Capture mode — two, not one

**Interval capture** is the general case: capture every N seconds across the recording, then
discard near-duplicates. A screenshare mostly holds still, so naive interval capture produces
hundreds of nearly identical frames. Deduplication is not optional.

Dedupe cheaply: compare consecutive frames and drop one whose difference from the previous kept
frame is below a threshold. A perceptual hash (`imagehash`) or a downscaled mean-absolute-diff
both work. **Prefer the latter** — it needs only Pillow, which is likelier already present than
a new dependency. Measure before adding anything.

**Explicit timestamps** is the sharper case and the one worth supporting first: the user already
knows the moments that matter. `--at 7:46,19:40,24:36` seeks to each and captures one frame.
Twelve seeks, twelve frames, no dedupe needed, no guessing. For reviewing a known presentation
this beats interval capture outright.

Support both. Explicit timestamps is less code and more immediately useful — build it first.

### Settle time

Frame capture needs a longer settle than transcript harvesting does. After a seek the player
may show the *previous* frame briefly, or a loading state. The existing 900 ms default was tuned
for DOM rows mounting, not for pixels settling.

Do not guess a number. Wait for the player's own readiness signal —
`video.readyState >= 2` and `video.seeking === false` — then add a small fixed delay. Poll in
`page.evaluate` rather than sleeping blindly. Add `--capture-settle-ms` if a fixed extra delay
proves necessary, defaulting to whatever measurement shows.

### Where frames go

Into the session folder beside `meeting.md`, matching how audio files already sit there
(`notes/writer.py:48` takes `audio_files`). Name them by timestamp, not ordinal —
`frame-0746.png` survives a re-run with different steps; `slide-003.png` does not.

### Embedding in the note

`write_note()` (`notes/writer.py:48`) currently takes `audio_files` and writes Obsidian embeds.
Frames want the same treatment but positioned *within* the transcript rather than appended.

The transcript lines already carry `[MM:SS]` timestamps. Interleave: before emitting a
transcript line, emit any frame whose timestamp falls at or before it and has not yet been
emitted. That yields a note reading as slide → discussion → slide.

Keep this in `notes/writer.py`, behind a new optional `frames` parameter. Do not build a second
note writer.

---

## 5. Shape

A flag on the existing script, not a new one:

```bash
.venv/bin/python scripts/stream_transcript.py "<url>" --note --frames-at 7:46,19:40,24:36
.venv/bin/python scripts/stream_transcript.py "<url>" --note --frames-every 30
```

Both share the seek loop with the transcript scrape where possible — one pass across the
timeline doing both jobs is better than two passes, and halves the wall-clock time on a long
recording. If interleaving proves awkward, a second pass is acceptable; correctness first.

The menu item **Import Transcript from Stream… ↗** already shells out to Terminal. If frames
become a normal part of that flow, surface the option there too — but only after the CLI works.

---

## 6. Tests

Follow the established boundary-patching pattern (`tests/test_processor.py`).

Playwright itself must not run in tests. Patch the page object and assert on the calls:

- A `--frames-at` list produces exactly one capture per timestamp, at the right seconds.
- Timestamps are parsed correctly in both `MM:SS` and `H:MM:SS` forms.
- A timestamp past `video.duration` is clamped or rejected with a clear error, not passed
  through to a failed seek.
- Interval capture with dedupe drops near-identical frames and keeps distinct ones — feed it
  synthetic images, not a real recording.
- The note embeds each frame at the right position relative to transcript lines.
- **No frames requested means no behaviour change at all.** The existing transcript path must
  be untouched — this is the regression that matters most.

---

## 7. Definition of done

- `pytest tests/ -q` green, plus new tests.
- The probe in §3 confirmed non-black frames before any of this was built.
- `--frames-at` on a real recording produces readable PNGs of the shared screen.
- The note renders in Obsidian with frames inline at the right points in the transcript.
- A run with no frame flags produces a byte-identical note to one from before this change.
- Playwright is still absent from `requirements.txt`.

---

## 8. Notes for whoever picks this up

- **Check §2 first.** If the video can be downloaded, build frame extraction into the local
  import path instead and skip the browser entirely. That is the better feature.
- **Then check §3.** If element screenshots come back black and no fallback works, the feature
  is not viable in the browser — say so and stop rather than shipping something that silently
  writes black images.
- **The repo is public.** No real meeting titles, colleague names, or the employer's name in
  committed files, including tests and docstrings. Use invented names. This has been caught in
  review before. **Frames are a new hazard here** — never commit a captured screenshot, and add
  the frame output path to `.gitignore` before the first run, not after.
- Sign-in is manual and must stay that way. The script does not see, store or type a password.
- `docs/stream-transcripts.md` documents the sibling feature and is worth reading for how an
  imported note is described to the user.
