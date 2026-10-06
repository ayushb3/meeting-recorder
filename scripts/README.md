# scripts/

Helper scripts for development and documentation tasks.

## demo_vault.py

Generates a throwaway vault populated with believable but entirely fictional
meeting notes. Its sole purpose is to allow documentation screenshots to be
taken without exposing real meeting names, colleague names, or internal project
names from the user's actual vault.

Usage:

```
python scripts/demo_vault.py [TARGET_DIR] [--force]
```

`TARGET_DIR` defaults to `~/Documents/MeetingRecorderDemo`. The script prints
step-by-step instructions for switching the app to the demo vault, taking
screenshots, and restoring the real vault afterwards.

**Safety:** the script refuses to run if the target directory already contains
files, unless `--force` is passed. This makes it impossible to accidentally
overwrite the real vault.

See `docs/screenshots/README.md` for the full screenshot workflow.

## stream_transcript.py

Scrapes the transcript out of a Teams/Stream recording page and optionally
summarises it into a vault note, for meetings you attended but did not record.
The panel's own Download button (`.docx`/`.vtt`) is disabled by tenant policy;
if it is ever enabled, delete this script and parse the downloaded file instead.

Needs `playwright`, which is deliberately **not** in `requirements.txt` — the app
must not take a browser-automation dependency:

```
.venv/bin/pip install playwright
```

No browser download is needed: it drives the installed Google Chrome through
`channel="chrome"`, which tenant sign-in policy treats more kindly than a
generic Chromium bundle.

Usage:

```
python scripts/stream_transcript.py "<recording URL>" --out transcript.txt
python scripts/stream_transcript.py "<recording URL>" --note --date 2026-09-16
```

Quote the URL — Stream URLs contain `&`. Chrome opens headed; you sign in by
hand and open the transcript panel yourself. The script never handles
credentials, and the session persists in a profile under `~/.cache/` (outside
this repo, since it holds tenant tokens).

**How it works.** The panel keeps only ~22 rows in the DOM and scrolls itself to
follow the video's playhead — setting `scrollTop` does nothing. So the script
seeks the paused video across the timeline and harvests whatever the panel
renders at each position, keyed by each row's `aria-posinset`, stopping when it
has `aria-setsize` rows.

**Completeness.** Every collected row must yield a transcript line; the run
aborts if any does not, apart from the "started/stopped transcription" notices.
A silently short transcript that looks complete is the failure this guards
against — it has happened once already, when lines falling exactly on a minute
boundary carried no seconds in their label.

**Fragility.** The selectors are undocumented and Microsoft changes the markup.
Failures are loud and specific by design; a selector miss reports what markup it
did find so the selector can be re-derived.
