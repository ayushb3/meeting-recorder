# Handoff — frames everywhere, and vault-aware summaries

**Written:** 2026-10-05 · **Branch:** `claude/project-status-video-frames-2743f7`, on top of the hosted-LLM summarizer work.

Self-contained. You do not need the conversation that produced it.

## What exists now

- **Frames from imported video.** `pipeline/frames.py` extracts a frame every N seconds with
  ffmpeg from the *original* file, drops near-duplicates (Pillow, mean-abs-diff against the
  last kept frame, threshold `DEDUPE_THRESHOLD = 0.5`), and `run_pipeline(frames=, frames_dir=)`
  places them beside the note and interleaves them at their timestamps. Exposed as a field in
  the Import Recording dialog; blank = off.
- **Frames from Stream.** `--frames-every N` on `scripts/stream_transcript.py`, same dedupe.
  The Stream dialog's Frames field accepts `every 10` as well as timestamps.
- **Shared helpers.** `notes/frames.py` (`interleave_frames`, `frame_filename`, `place_frames`)
  because `scripts/` is not bundled into the app.
- **Vault integration.** `notes/vault.py`, driven by an optional `[vault]` config section:
  bounded context in (optional refresh command, status file, last N daily notes' active
  sections), invented `[[links]]` flattened out, and an opt-in daily-note log line out.
  `[llm] prompt_file` keeps a private prompt outside the repo.

## Decisions worth knowing

- **Threshold 0.5, not higher.** Measured on two real 10-second captures (127 and 131 frames):
  consecutive-frame medians were ~0.15–0.2; 0.5 kept 18 and 58; 1.5 began dropping frames where
  a screenshare gained a few lines of text. Lower is noisier but loses nothing.
- **Frame capture is non-fatal.** Any failure leaves a note without frames.
- **Settings saves rewrite the whole config.** `[vault]` and `prompt_file` have no controls in
  the Settings window, so `preserved_config_fields` carries them through a save. Any new
  config-only option needs the same treatment or a save will delete it.
- **Daily-note links are path-style** (`[[Folder/…/meeting|Title]]`) because every note this
  app writes is named `meeting.md`.
- **A missing daily note is never created** — whatever owns that structure should.
- **Action items are checkboxes in the default prompt**, and exclude ideas nobody took on.
  Vault action items are *not* auto-added to a task list; that stays a human decision.

## Not done

- No speaker diarization.
- AppKit layout of the new Import Recording field is untested (tests cover only the pure helpers).
  Rebuild the app from the real checkout and click through it.
- Daily-note logging is `## Notes Created` only; it does not place the link inline under a
  matching project block.
- No eval harness for summary quality. A fixed, anonymised regression transcript would let
  models and prompts be compared; the earlier comparison found context mattered as much as
  the model.

## Privacy

The repo is public. Vault paths, project names and people live only in the user's own config
and prompt file. Tests use an invented vault. Captured frames are gitignored.
