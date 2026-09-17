# Handoff — ingesting recordings the app did not make

**Written:** 2026-09-17 · **For:** a fresh local session on macOS
**Repo state:** `61df05f`, 153 tests passing, working tree clean

This document is self-contained. You do not need the conversation that produced it.

---

## 0. What this is for

The app records meetings you attend. This feature covers the other case, in the user's
words: *"when you were not in a meeting but still want to ingest the recording."*

There is **no local audio** for these. Everything downstream of audio already exists and
works; the job is to get a transcript or an audio file into the existing pipeline.

Two features, in priority order. **Feature A is the one that matters** and should ship
first — it is useful on its own and carries no complications.

---

## 1. Current state

- `run_pipeline()` (`pipeline/processor.py:32`) is the entry point. It takes **two** audio
  paths (`mic_path`, `system_path`) and **fails if either is missing**
  (`pipeline/processor.py:63-69`, the `setup` stage). That is the main thing an imported
  recording has to work around — it has one track, not two.
- `transcribe(audio_path, whisper_binary, model)` (`transcriber/whisper.py:105`) runs on
  any audio file whisper.cpp can read. Nothing about it assumes a recorded session.
- `mix_wavs()` (`recorder/mixer.py:6`) requires matching sample rates and averages two
  tracks. An import has one track, so it should not be called.
- `write_note()` / `format_note()` (`notes/writer.py`) produce the note. Frontmatter keys
  are `date`, `time`, `duration`, `tags`, `title`.
- `summarize(transcript_lines, model, host, context=..., custom_template=...)`
  (`summarizer/ollama.py:125`) takes a list of lines — it does not care where they came
  from. This is the seam a transcript import plugs into.
- `ffmpeg 8.1.1` is installed at `/opt/homebrew/bin/ffmpeg`.
- `playwright` is **not** installed. Feature B needs it; Feature A does not.

---

## 2. Feature A — ingest a local audio or video file

**Build this first.** It is genuinely useful independent of anything Teams-related: any
recording you legitimately have — one a colleague sent you, one you downloaded normally,
a podcast, a conference talk — becomes a summarised note.

### Shape

A menu item under `Meetings ▸`, something like **Import Recording… ↗**, opening an
`NSOpenPanel` filtered to audio and video types. Then:

1. If the file is video or a non-WAV audio format, extract audio with ffmpeg to a 16 kHz
   mono WAV, which is what the rest of the pipeline uses:
   `ffmpeg -i <input> -vn -ac 1 -ar 16000 -c:a pcm_s16le <output.wav>`
2. Feed it to the existing pipeline.
3. Write the note into the normal week folder structure so it appears in `Meetings ▸`
   alongside recorded meetings.

### The one real design decision

`run_pipeline()` wants two tracks. Three options, in order of preference:

1. **Add a `single_source: Path | None` parameter.** When set, skip the mic/system split
   entirely: transcribe the one file, skip `mix_wavs`, write the note. Cleanest, and the
   note should say the recording was imported rather than captured.
2. Pass the same file as both `mic_path` and `system_path`. Avoid — `merge_transcripts`
   would deduplicate a file against itself and the behaviour is hard to reason about.
3. A separate `run_import_pipeline()`. Avoid unless option 1 turns out to be invasive;
   duplicating the note-writing and error-marker logic is how they drift apart.

### Details worth getting right

- **Session timestamp.** Use the file's mtime, or better, let the user confirm it — a
  recording imported today may be from last week, and the note's `date`/`time` frontmatter
  drives which day it appears under in `Meetings ▸`.
- **Duration** comes from `ffprobe`, not from a recording timer.
- **`keep_audio`** should not move or delete the user's original file. Copy into the
  session folder if `keep_audio` is true; never `shutil.move` a file the user chose.
- **Long files.** A 60-minute recording is a long whisper.cpp run. The menu should show
  the processing state (`_set_processing()` already exists) and must not block the main
  thread — follow the existing daemon-thread pattern in `ui/menu.py`.
- **Notify on completion** through `_notify()`, as recordings do.

### Tests

Mock ffmpeg and whisper; assert the single-source path skips mixing, that the note lands
in the right week folder with correct frontmatter, that a missing/corrupt file produces a
clear error rather than a traceback, and that the user's original file is never moved.

---

## 3. Feature B — pull a transcript from the Stream panel

### What this is, precisely

The user can already open the recording and read its transcript. They can select and copy
it by hand. This automates that — the motivation is that a long transcript takes roughly
twenty copy-paste operations because of how the panel is built (see below), which is
enough friction that they do not read recordings they are only tangentially involved in.

### Check this first — it may make the whole feature unnecessary

**Look for a transcript download in the panel's own `…` menu** (`.vtt` or `.docx`). That
permission is frequently separate from video download and often still enabled. The
recording's owner is on the user's team and has said they would share permissions — the
button is off by tenant default, not by anyone's decision.

**If a file can be downloaded, do that instead.** Parsing a `.vtt` is ~30 lines, needs no
browser automation, and does not break when Microsoft changes their markup. Ask for the
permission before building a scraper to work around not having it.

### Honest constraints — put these in front of the user, do not bury them

- **Microsoft's Services Agreement restricts automated access** regardless of purpose.
  The practical risk for personal use on content you are entitled to read is low, but it
  is a real term and the user should know it applies.
- **Conditional Access may simply refuse automated Chromium.** Treat this as a likely
  outcome rather than an edge case. If it blocks, the feature does not work and no amount
  of code fixes that — say so and stop rather than escalating.
- **Headless will not work.** Microsoft's login flow is
  [documented as failing headless](https://github.com/microsoft/playwright/issues/32034)
  independent of any corporate policy. Plan on a headed browser with a one-time login.
- **The selectors are undocumented** and Microsoft changes the markup. Fail loudly with a
  clear message when one misses; never return a partial transcript that looks complete.

### Research already done — do not re-derive

Recovered from the previous handoff (commit `61df05f^`), verified at the time:

- **Why manual copying loses the middle.** The panel is a Fluent UI **virtualized list** —
  roughly 44 rows exist in the DOM at once and the rest are destroyed. Select-all copies
  only what is mounted, which is exactly the "head and tail, middle dropped" behaviour.
  Not a bug, and the reason this needs scrolling rather than one copy.
- **Dedup key: `aria-posinset`.** Each row carries Teams' own 1..N ordinal, and the
  container carries `aria-setsize` (the true total). Key a dict on `aria-posinset` and stop
  when you have `aria-setsize` entries. Better than hashing speaker+timestamp+text, which
  collides when someone repeats a short phrase.
- **Selectors** (from [Nara7788/ms-teams-transcript-extractor](https://github.com/Nara7788/ms-teams-transcript-extractor),
  reported 312/312 lines on a real recording):

  | Element | Selector |
  |---|---|
  | Scroll container | `#scrollToTargetTargetedFocusZone`, fallback: first `[data-is-scrollable="true"]` containing a `[id^="sub-entry-"]` |
  | Transcript line | `[id^="sub-entry-"]` (carries `aria-posinset`, `aria-setsize`) |
  | Speaker + time | `aria-label` on nearest ancestor `[role="group"]`, e.g. `"Name 0 minutes 7 seconds"` |

  Verify in DevTools before building on them.
- **Scroll strategy:** step `viewportHeight * 0.8` so windows overlap, re-assert
  `scrollTop` for ~150ms after each step because the list bounces back, and re-query all
  rows each step rather than diffing.
- **Auth:** Playwright `launch_persistent_context(user_data_dir=...)` with a one-time
  headed login, so the session persists between runs.

### Dead ends — do not spend time here

- **Microsoft Graph transcript API.** Transcripts hang off the *organizer's* meeting
  object. `getAllTranscripts` requires `meetingOrganizerUserId` and returns only meetings
  that user organized; non-organizers get `404`. The only real routes are a tenant-admin
  Application Access Policy or an app installed in the meeting chat before it ends.
- **Teams local cache.** New Teams streams transcripts from SharePoint rather than
  persisting them locally. Checked, not present.

### Shape, if built

Keep it **entirely separate from the app bundle** — a standalone script under `scripts/`,
not a menu item, at least initially. It has a dependency the app does not need
(`playwright`), it needs a headed browser, and it may stop working without warning. Output
a transcript file, then let the existing import path (Feature A) take it from there.

---

## 4. Definition of done

**Feature A**
- `pytest tests/ -q` green (153 now, plus new).
- Importing an `.mp4` and an `.m4a` both produce a note with a real transcript.
- The user's original file is untouched.
- The note appears under the right day in `Meetings ▸`.
- A long import shows the processing state and does not freeze the menu.

**Feature B**
- A transcript file with `aria-setsize` lines — no gaps, verified against the visible count.
- Fails loudly and specifically when a selector misses or Conditional Access blocks.
- Never silently returns a partial transcript.

---

## 5. Notes for whoever picks this up

- The app must be launched as a bundle for audio capture, but **import does not need
  that** — it touches no audio devices, so `python app.py` is fine for developing
  Feature A.
- `scripts/demo_vault.py` generates a vault of fictional meetings; useful for testing that
  imported notes group correctly without touching real data.
- Do not put real meeting titles, colleague names, or the employer's name in committed
  files. The repo is public.
