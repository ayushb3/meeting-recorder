# Importing Stream transcripts

For meetings recorded in Teams/Stream by someone else. The app records meetings you attend;
this covers the other case — a recording you can watch but did not make.

You can already read such a transcript in the browser. The problem is getting it *out*: the
panel keeps only about twenty lines in the DOM at a time, so selecting and copying a long
transcript takes roughly twenty separate operations. This does it in one run.

The result is a normal note in your vault, with one advantage over a recorded meeting — it has
**real speaker names** rather than just distinguishing you from everyone else.

---

## Before you start

**Check the panel's Download button first.** Open the transcript panel on any recording and
look for **Download** at the top. If it is enabled, use it — a downloaded `.vtt` is simpler and
more reliable than anything here. This whole feature exists because that button is disabled by
tenant policy. If your admin ever enables it, stop using this.

**One-time setup.** The scraper needs `playwright`, which is deliberately *not* a dependency of
the app — a menu-bar recorder should not ship a browser automation stack:

```bash
.venv/bin/pip install playwright
```

That is the whole install. No browser download is needed: it drives the **Google Chrome you
already have** rather than a bundled Chromium, because tenant sign-in policy treats a branded
browser more kindly than a generic automation build.

**You also need:** Google Chrome installed, and Ollama running if you want a summary.

---

## Running it

### From the menu

**Import Transcript from Stream… ↗** → paste the recording URL → **Scrape Transcript**.

Terminal opens and runs the scrape where you can watch it. The `↗` means it leaves the app, the
same as the Ollama items.

### From the command line

```bash
.venv/bin/python scripts/stream_transcript.py "<recording URL>" --note
```

Quote the URL — Stream URLs contain `&`, and an unquoted one gets mangled by the shell.

| Flag | Effect |
|---|---|
| `--note` | Summarise and write a note into the vault |
| `--out FILE` | Also write the raw transcript to a file |
| `--name TITLE` | Set the meeting title (otherwise the LLM suggests one) |
| `--date YYYY-MM-DD` | Recording date, if it cannot be detected from the page |
| `--steps N` | Seek steps across the timeline (default 50) |

With neither `--out` nor `--note`, the transcript prints to stdout.

---

## What you have to do by hand

Two steps cannot be automated, so plan on being present for the first ten seconds:

1. **Sign in**, if prompted. Microsoft's login flow does not work in a headless browser — this
   is documented behaviour, not a limitation of this script. The script never sees, stores or
   types your password; you type it into the Chrome window yourself. The session then persists,
   so later runs usually skip this.
2. **Open the transcript panel** in the player UI. The script waits for it rather than clicking
   it open — one less undocumented selector to guess wrong, and it avoids clicking around inside
   your authenticated session.

After that it runs unattended. A 74-minute meeting takes about 45 seconds.

Do not click transcript lines while it runs — that seeks the player and starts playback.

---

## How it works

Worth knowing, because it explains the constraints:

The transcript panel **scrolls itself to follow the video's playhead**. That sync owns the
scroll position — setting `scrollTop` programmatically does nothing at all, and a manual scroll
gets snapped back within a second.

So the scraper uses the sync rather than fighting it: it seeks the (paused) video across the
timeline and harvests whatever the panel renders at each position. Each row carries its own
ordinal (`aria-posinset`) and the total (`aria-setsize`), so rows are collected into a dictionary
keyed by ordinal and the run stops when it has them all.

**Completeness is enforced, not assumed.** Two gates: every ordinal from 1 to the total must be
collected, and every collected row must produce a transcript line. Anything else aborts the run.
This is not paranoia — an early version passed the first gate with 263/263 rows and still lost
seven real utterances, because lines falling exactly on a minute boundary are labelled
`"18 minutes"` with no seconds and the parser rejected them. A transcript that is silently short
but looks complete is the worst outcome here.

---

## When it breaks

It will, eventually — the selectors are undocumented and Microsoft changes the markup. Failures
are loud and specific by design.

| Message | Meaning |
|---|---|
| `playwright is not installed` | Run the install command above |
| `Could not launch Google Chrome` | Chrome is not at `/Applications/Google Chrome.app` |
| `No transcript markup found` | The panel is not open — open it and re-run |
| `Transcript-like markup is present but no rows matched` | Markup changed. The message lists what it *did* find; re-derive the selector in DevTools |
| `Incomplete: N/M rows, missing ...` | The sweep missed rows. Retry with `--steps` doubled |
| `N row(s) were collected but produced no transcript line` | The label format changed. The message shows the offending rows |
| `No aria-setsize on any row` | Completeness cannot be verified, so it refuses to emit anything |

If sign-in fails with an `AADSTS` code, Conditional Access is refusing the automated browser.
No change to this script fixes that — the code is quotable to IT as-is.

---

## Notes

- The Chrome profile lives in `~/.cache/meeting-recorder-spike/chrome-profile`, outside the
  repo, because it holds live session tokens. Delete it to force a fresh sign-in.
- Microsoft's Services Agreement restricts automated access to their services. The practical
  risk for personal use on content you are entitled to read is low, but it is a real term and
  worth knowing that it applies.
- The `--note` path writes into the same week-folder structure as a recorded meeting, so
  imported notes appear in **Meetings ▸** alongside the rest. If Ollama is unreachable the note
  is still written, marked degraded, and can be reprocessed later — exactly like a recording.
