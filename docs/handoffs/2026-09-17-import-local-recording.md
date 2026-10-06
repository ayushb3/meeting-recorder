# Handoff — importing a local audio or video file

**Written:** 2026-09-17 · **For:** a fresh local session on macOS
**Repo state:** `b6f386b`, 206 tests passing, working tree clean

This document is self-contained. You do not need the conversation that produced it.

It supersedes `2026-09-17-ingest-existing-recordings.md`, whose Feature B is now built and
shipped. What remains is Feature A, described here with the groundwork that landed since.

---

## 0. What this is for

The app records meetings you attend. This covers the other case: a recording that already
exists as a file — one a colleague sent you, one you downloaded normally, a conference talk,
a podcast. Point the app at it and get the same summarised note a recorded meeting produces.

Everything downstream of audio already exists and works. The job is getting a single audio
file into a pipeline that was built for two.

**A sibling feature already shipped.** `scripts/stream_transcript.py` imports a *transcript*
scraped from a Teams/Stream recording (see `docs/stream-transcripts.md`). That one has no
audio at all and plugs in at `summarize()`. This one has audio and no transcript, so it plugs
in further up, at `transcribe()`. They are complementary, not alternatives — and the Stream
scraper is a useful reference for how a non-recorded import writes its note.

---

## 1. Current state — verified at `b6f386b`

- **`run_pipeline()` (`pipeline/processor.py:34`)** is the entry point. It takes **two** audio
  paths and fails if either is missing (`pipeline/processor.py:65,69`, the `setup` stage).
  This is the main thing an import has to work around: it has one track, not two.
- **`run_pipeline` calls `shutil.move` on both inputs** (`pipeline/processor.py:66,70`),
  relocating them into the session folder. **This is the single most important hazard in this
  feature** — it would move a file out of the user's Downloads or Documents folder without
  asking. An import must never take that path.
- **`transcribe_raw(audio_path, whisper_binary, model, source)`**
  (`transcriber/whisper.py:84`) returns `list[Segment]`; `transcribe(...)`
  (`transcriber/whisper.py:105`) returns preformatted `list[str]`. Neither assumes a recorded
  session — they run on any audio whisper.cpp can read.
- **`mix_wavs()` (`recorder/mixer.py:6`)** averages two tracks and requires matching sample
  rates. An import has one track, so it must not be called.
- **`write_note()` (`notes/writer.py:48`)** — signature:
  `write_note(dt, duration_seconds, summary, transcript_lines, output_dir, audio_files=None, overwrite=False, meeting_name=None) -> Path`.
  Frontmatter keys: `date`, `time`, `duration`, `tags`, `title`. `duration_seconds // 60`
  becomes the displayed duration (`notes/writer.py:24`).
- **`week_folder(dt)` (`notes/writer.py:5`)** returns `"YYYY-Www"`. Session folders are
  `output_dir/<week>/<slug-or-timestamp>/meeting.md`, timestamp format `%Y-%m-%d-%Hh%M`
  (`pipeline/processor.py:50`).
- **`_pick_file(title, file_types)` (`ui/settings_window.py:686`)** is an existing NSOpenPanel
  helper returning a path string or `None`. **Reuse it** rather than writing a new picker.
- **`ffmpeg` and `ffprobe` 8.1.1** are both at `/opt/homebrew/bin/`. Confirmed present.
- **`playwright` is now installed in `.venv`** (for the Stream scraper) but is deliberately
  absent from `requirements.txt`. Feature A does not need it; do not add it.

---

## 2. The one real design decision

`run_pipeline()` wants two tracks. Three options, in order of preference:

1. **Add a `single_source: Path | None` parameter.** When set, skip the mic/system split
   entirely: transcribe the one file, skip `mix_wavs`, skip the two `shutil.move` calls, write
   the note. Cleanest. The note should record that the recording was imported rather than
   captured.
2. Pass the same file as both `mic_path` and `system_path`. **Avoid** — `merge_transcripts`
   would deduplicate the file against itself, and the `(you)` speaker labelling would be
   meaningless.
3. A separate `run_import_pipeline()`. Avoid unless option 1 proves invasive; duplicating the
   note-writing and error-marker logic is how two code paths drift apart.

---

## 3. Details worth getting right

- **Never move the user's file.** `shutil.move` is right for a recording the app itself made
  in a temp directory; it is wrong for a file the user chose. Copy into the session folder if
  `keep_audio` is true, otherwise leave the original alone and touch nothing.
- **Converting to WAV.** whisper.cpp wants 16 kHz mono PCM. For video or non-WAV audio:
  `ffmpeg -i <input> -vn -ac 1 -ar 16000 -c:a pcm_s16le <output.wav>`. Write the converted
  file into the session folder or a temp directory — never beside the user's original.
- **Duration** comes from `ffprobe`, not a recording timer:
  `ffprobe -v error -show_entries format=duration -of csv=p=0 <file>`.
- **Session timestamp.** Use the file's mtime as the default, but let the user confirm or
  override it. A recording imported today may be from last week, and the note's `date`/`time`
  frontmatter decides which day it files under in **Meetings ▸**. The Stream scraper hit this
  same problem and solved it with a `--date` flag plus best-effort detection — see
  `scripts/stream_transcript.py`.
- **Long files.** A 60-minute recording is a long whisper.cpp run. Use the existing daemon
  thread pattern: `_set_processing()` on the main thread, work on a `threading.Thread(daemon=True)`,
  results marshalled back through `_call_on_main()`. See `ui/menu.py` around the recording
  pipeline for the canonical example.
- **Notify on completion** through `_notify()`, as recordings do.
- **Degraded path.** Follow `run_pipeline`'s handling of `OllamaUnavailableError`
  (`pipeline/processor.py:89-108`): still write the note with a placeholder summary, drop a
  `summarize.error` marker (`stage: ...\nerror: ...\n`) beside it so it surfaces as degraded
  in the menu.

---

## 4. Shape

A menu item under the root menu — **Import Recording… ↗** would sit naturally beside
**Import Transcript from Stream… ↗**, which shipped in `b6f386b` and is a direct precedent for
a non-recorded import.

Unlike the Stream scraper, **this one belongs in-process**, not shelled out to Terminal. It
needs no browser, no sign-in, no manual steps, and no dependency the app lacks — ffmpeg is a
subprocess call like whisper.cpp already is. It can run entirely on the daemon-thread pattern
and report through notifications, so no `↗` suffix is warranted.

Use `_pick_file()` (`ui/settings_window.py:686`) filtered to audio and video types.

---

## 5. Tests

Mock `ffmpeg`/`ffprobe` and whisper via `unittest.mock.patch` on `subprocess.run` — see
`tests/test_whisper.py:61-63` for the established pattern (patch the module's `subprocess.run`,
pre-write the JSON output file to `tmp_path` so the real file-reading path still runs), and
`tests/test_processor.py` for patching at the module boundary
(`pipeline.processor.transcribe_raw` etc.).

Assert specifically:
- The single-source path skips `mix_wavs` entirely.
- **The user's original file still exists at its original path** after import, with
  `keep_audio` both true and false. This is the regression that matters most.
- The note lands in the right week folder with correct frontmatter.
- A missing or corrupt input produces a clear error, not a traceback.
- A video file and a non-WAV audio file both route through ffmpeg; a 16 kHz mono WAV does not.

---

## 6. Definition of done

- `pytest tests/ -q` green (206 now, plus new).
- Importing an `.mp4` and an `.m4a` both produce a note with a real transcript.
- The user's original file is untouched and unmoved.
- The note appears under the right day in **Meetings ▸**, alongside recorded meetings.
- A long import shows the processing state and does not freeze the menu.

---

## 7. Notes for whoever picks this up

- The app must be launched as a bundle for audio *capture*, but **import does not need that** —
  it touches no audio devices, so `python app.py` is fine for development.
- `scripts/demo_vault.py` generates a vault of fictional meetings; useful for checking that
  imported notes group correctly without touching real data.
- **The repo is public.** Do not put real meeting titles, colleague names, or the employer's
  name in committed files — including tests and docstrings. Use invented names. This has been
  caught once already in review.
- `docs/stream-transcripts.md` documents the sibling feature; worth a read for how an imported
  note is described to the user, and for the "check the simpler path first" framing.
