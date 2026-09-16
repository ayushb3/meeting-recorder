# Handoff — menu bar rework + Teams recording ingest

**Written:** 2026-09-16 · **For:** a local Claude Code session on macOS
**Branch:** `claude/tender-cannon-up95tk` · **PR:** [#1](https://github.com/ayushb3/meeting-recorder/pull/1) (draft)

This document is self-contained. You do not need the conversation that produced it.

---

## 0. Why this moved to a local session

The prior session ran in a Linux cloud container. Three things are impossible there and
are the reason this handoff exists:

1. **`rumps` cannot be installed** (macOS + pyobjc only). Every menu bar change would be
   written blind — not even an import check, let alone seeing the menu.
2. **Notifications cannot be tested at all.** They need a signed `.app` bundle and a real
   Notification Center. See bug **B4** — unbundled, `rumps.notification` *raises*.
3. **The Teams/SharePoint work is untestable by definition.** It depends on an
   authenticated SAP SSO session in a real browser. The target URL was also blocked by
   the container's egress proxy.

Also unavailable there: `sounddevice` (needs PortAudio), so `tests/test_audio.py` cannot
run; and PyInstaller `.app` builds.

**What was verified there:** all non-UI Python. `pytest tests/` → **24 passed**, with the
2 `test_audio.py` failures being the environmental `sounddevice` gap above. Expect 26
passing on a Mac with deps installed.

---

## 1. Current state

### Already committed on this branch (commit `77f7c93`)

Two helpers, **additive and wired to nothing** — behaviour on this branch is identical to
`main`:

- **`check_status(model, host, timeout=2.0) -> OllamaStatus`** in `summarizer/ollama.py`.
  Probes `GET {host}/api/tags`. Reports two separate facts: server reachable, and
  configured model actually pulled. Frozen dataclass with `.ready` and a `.label` property
  (`"Ollama: ready (llama3.1:8b)"` / `"Ollama: not running"` / `"Ollama: model X not
  pulled"`). **Never raises** — a failed probe is the answer. Summaries are optional and
  this must never block a recording.
- **`find_notes_for_date(output_dir, dt) -> list[Path]`** in `notes/writer.py`. Finds
  notes by the `date:` in their own frontmatter, oldest-first by `time:`. Exists because
  folder names carry no date once renamed — see **B1**.

Neither has committed test coverage. Both were verified by direct execution.

### Design decision already made

**Variant B won.** Standalone mockup committed at
`docs/prototypes/menu-bar-variant-b.html` — open it in a browser. Full three-variant
canvas (including the recording and degraded states B doesn't draw):
<https://claude.ai/artifact/XhnSNUFQ6tZ9APsof1xfW5>

Rationale, in the user's words: *"its a small submenu with clear information in the panels
but at a glance you have the hero actions."*

---

## 2. Verified bugs

All reproduced against real code paths. Do not re-litigate these; they are established.

### B1 — "Open Today's Note" is broken for every successfully-named meeting ⚠ highest priority

`pipeline/processor.py` renames each session folder to a title slug
(`q2-planning-with-design-team`) once the meeting has a name. `ui/menu.py:305` looks for
today's note by globbing `2026-09-16-*/meeting.md` — a date prefix the renamed folder no
longer carries.

Reproduced across four realistic days:

| Day | On disk | Menu opens |
|---|---|---|
| 3 meetings, all named (**the normal day**) | `standup`, `q2-planning`, `11-with-sven` | **"No note found for today."** |
| 3 meetings, none named (Ollama died) | 3 timestamp folders | the 15:00 one ✓ |
| Mixed: 2 named, 1 unnamed | `standup`, `q2-planning`, `2026-09-16-11h00` | **the unnamed one** |
| Two meetings, same title | `standup`, `2026-09-16-15h00` | the timestamped one |

The feature works *only* when everything else has failed. Row 3 is the perverse case: with
two good meetings and one broken one, it opens the broken one. `find_notes_for_date()`
(already committed) is the fix, but **is not wired in** — the bug is still live.

### B2 — An Ollama outage is reported as complete success

`pipeline/processor.py:96` catches `OllamaUnavailableError`, writes
`"⚠ Summary unavailable"` into the note, and returns `success=True`. The user gets a
notification reading **"Note saved"**. No `.error` file is written, so `_has_error_files()`
(`ui/menu.py:87`) stays false and **"Reprocess Last Meeting" remains greyed out** — there
is no way to retry the summary. A degraded result is announced as a win, with no recovery
path. Compounding it: `suggest_title` also fails, so the folder keeps its timestamp name.

### B3 — After any failure, reprocess is unreachable anyway

The failure branch (`ui/menu.py:246-251`) never calls `_set_idle()`, which is the only
thing that re-enables the reprocess item. It stays greyed out until the app restarts.

### B4 — Unbundled, a successful run reports as failure

`rumps.notification` raises `RuntimeError` when there is no `CFBundleIdentifier`
([rumps source](https://github.com/jaredks/rumps/blob/master/rumps/notifications.py)).
Running `python app.py` (README Option B), the success notification at `ui/menu.py:241`
raises *inside* the `try`, is caught by `except Exception` at line 252, and sets the menu
bar to **"⚠ Error"** after a perfectly good run. The second notification then raises
uncaught in the daemon thread.

Related and worth knowing: `NSUserNotification` (what rumps uses) is deprecated since
macOS 11, and `meeting_recorder.spec` sets `codesign_identity=None`. Notification delivery
from an unsigned bundle is unreliable. **This is the architectural reason Ollama status
belongs in the menu bar, not in a notification** — the menu bar always works and needs no
permission.

### B5 — `reprocess` crashes on a slug-named folder

`ui/menu.py:291` does `datetime.strptime(session_name[:16], "%Y-%m-%d-%Hh%M")` on the
folder name. Folders renamed to a slug have no timestamp, so this raises `ValueError` on
the main thread. Currently rare (errors usually precede the rename) but **fixing B2 makes
it common**, because the `write_note` stage errors *after* the rename. Fix B5 and B2
together. Fall back to file mtime when the parse fails.

### B6 — Two meetings with the same title collide

Second rename fails with `OSError: Directory not empty`; the folder silently keeps its
timestamp name. No data loss *only* because the first folder is non-empty — if it were
ever empty, POSIX `rename` would replace it. Disambiguate (append `-2`, or the time).

---

## 3. Implementation plan

Order matters: 1 → 2 → 3, then 4.

### Step 1 — Logic layer (no macOS needed, fully testable)

| # | File | Change |
|---|---|---|
| 1.1 | `notes/writer.py` | Add `list_notes(output_dir, weeks=2)` returning entries with `date`, `time`, `title`, `path`, and `degraded` (a `*.error` file beside the note). This backs the `Meetings ▸` submenu. Build on the committed `find_notes_for_date`. |
| 1.2 | `pipeline/processor.py` | Add `summary_ok: bool = True` and `warning: str \| None` to `PipelineResult`. On `OllamaUnavailableError`, keep writing the note **and** write a `summarize.error` marker so reprocess lights up. Fixes **B2**. |
| 1.3 | `pipeline/processor.py` | Disambiguate colliding slugs before rename. Fixes **B6**. |
| 1.4 | `config.py` + `config.template.toml` | Add `[ollama] prompt` — the custom AI prompt (user request). Validate at load that it contains `{transcript}`; fail with a clear message if not. |
| 1.5 | `summarizer/ollama.py` | Accept the custom template. **Use `str.replace()` for `{transcript}` / `{context}`, not `.format()`** — user prompts may contain literal braces (JSON examples), which would make `.format()` raise `KeyError`. |
| 1.6 | `tests/` | Cover: `check_status` (reachable / unreachable / model-missing), `list_notes` with renamed folders, the degraded pipeline result, prompt validation rejecting a template without `{transcript}`. |

Verify: `pytest tests/ -v` → all green (26 on a Mac).

### Step 2 — Menu bar, Variant B (needs macOS)

Reference `docs/prototypes/menu-bar-variant-b.html`. The tree:

```
● Start Recording              ← hero action, always first
────────
Meetings ▸
      Today — Tue 16 Sep       (disabled caption)
      09:00  Standup
      11:00  FDE Roadshow
      15:00  ⚠ 2026-09-16-15h00   ← degraded: transcript, no summary
             Retry summary
      ────────
      Earlier this week
      Mon    Design Review
      ────────
      Browse All Meetings…     → opens output_dir in Finder
Ollama ▸
      ● Running                (disabled status)
      llama3.1:8b · localhost:11434   (disabled)
      ────────
      Start Ollama in Terminal
      Pull Model…
      Re-check Now
Save Location ▸
      ~/Documents/Meetings     (disabled)
      ────────
      Open Meetings Folder
      Change Location…         → opens config
────────
Settings…
Quit
```

**Confirmed rumps API** (verified against source — build on these, don't re-derive):

- Nested submenus: `('Parent', [child, child])` inside a menu iterable. Recursive, any depth.
- Separator: `None` or `rumps.separator`.
- `rumps.notification(title, subtitle, message, data=None, sound=True, action_button=None, other_button=None, icon=None)`
- Click handling: the `@rumps.notifications` decorator (module-level; `rumps.notifications`
  is the alias for `on_notification`). The handler receives the `data` payload — this is how
  a notification opens the note it refers to. **No new dependency is needed for clickable
  notifications.**

Work items:

- **2.1** `_notify()` helper wrapping `rumps.notification` in `try/except RuntimeError`,
  logging instead. Fixes **B4**. Do this first — it unblocks dev-mode testing of everything else.
- **2.2** Rebuild the menu as the tree above. Root menu must not grow with meeting count.
- **2.3** Poll `check_status()` on a `rumps.Timer` (30–60s) and on menu open; drive both the
  root-menu dot and the `Ollama ▸` panel. Cache it — do not probe on every draw.
- **2.4** Warn on **Start Recording** if Ollama is down — that is the only moment it is
  cheaply fixable. **Warn, never block**: recording must always work.
- **2.5** "Start Ollama in Terminal" → `subprocess.run(["open", "-a", "Terminal", ...])`
  running `ollama serve`. This is the user's explicit "easy click option to get it setup in terminal".
- **2.6** Wire `Meetings ▸` to `list_notes()`. **Deletes `open_note` entirely** — a single
  "today's note" item cannot represent a day with three meetings. Fixes **B1**.
- **2.7** Success notification carries the meeting name and opens the note on click
  (`data` + `@rumps.notifications`), plus `action_button="Open"`.
- **2.8** Call `_set_idle()` on every failure path. Fixes **B3**.
- **2.9** Make `reprocess` tolerate slug folders (mtime fallback). Fixes **B5**.
- **2.10** Drop the Obsidian assumption. `open` on the `.md` uses the user's default editor;
  add "Reveal in Finder"; make the location settable. Nothing here should assume a vault.

Verify: build the `.app` (`pyinstaller meeting_recorder.spec`), run it, and check each state
by hand — Ollama up, Ollama killed mid-session, a real short recording, a degraded run
(stop `ollama serve` before pressing Stop).

### Step 3 — Custom AI prompt in Settings

Config-file based (1.4/1.5) is the minimum and matches how the app already does settings.
Ship that first. A GUI editor is a larger piece of work; treat it as optional follow-up.

### Step 4 — Teams / SharePoint recording ingest

**Use case (user's words):** *"when you were not in a meeting but still want to ingest the
recording."* So there is **no local audio** — unlike a recorded meeting, nothing in the
existing pipeline has a source yet. That is what makes this feature worth building.

Target is a **SharePoint Stream** URL of the form:

```
https://<tenant>-my.sharepoint.com/personal/<user>/_layouts/15/stream.aspx
    ?id=/personal/<user>/Documents/Recordings/<Meeting Name>-<stamp>-Meeting Recording.mp4
```

Note what that `id` parameter is: **an `.mp4` file in someone's OneDrive.**

#### ⚠ Do this check first — it decides the whole feature's shape

**Open the recording in a browser and look for a Download button.**

- **If download works** → the feature is: download the mp4 → extract audio (ffmpeg) →
  **feed the existing pipeline**. No scraping, no brittle selectors, no ToS question.
  You already own every piece downstream of the audio. This is by far the better path and
  most of the research below becomes moot. Build this.
- **If download is disabled** (organizer/tenant policy) → fall back to scraping the
  transcript panel, below.

#### Fallback: scraping the transcript panel

Established by research, with sources:

- **The copy problem is list virtualization, confirmed.** The Stream transcript panel is a
  Fluent UI virtualized `List` — roughly 44 rows exist in the DOM at once; the rest are
  destroyed. Select-all copies only what is mounted, which is exactly the reported
  "head and tail, middle dropped". Not a Teams bug.
- **Dedup key: `aria-posinset`.** Each row carries Teams' own 1..N ordinal, and the
  container carries `aria-setsize` (the true total). Key a dict on `aria-posinset` and stop
  deterministically when you have `aria-setsize` entries. This is **better than a
  speaker+timestamp+text hash**, which collides when someone repeats a short phrase or two
  lines share a second.
- **Selectors** (unofficial, from a working community extractor —
  [Nara7788/ms-teams-transcript-extractor](https://github.com/Nara7788/ms-teams-transcript-extractor),
  verified 312/312 lines on a real recording):
  | Element | Selector |
  |---|---|
  | Scroll container | `#scrollToTargetTargetedFocusZone`, fallback first `[data-is-scrollable="true"]` containing a `[id^="sub-entry-"]` |
  | Transcript line | `[id^="sub-entry-"]` (carries `aria-posinset`, `aria-setsize`) |
  | Speaker + time | `aria-label` on nearest ancestor `[role="group"]`, e.g. `"Name 0 minutes 7 seconds"` |

  **These target the Stream player specifically — which is what our URL is**, so they
  should apply. Verify in DevTools before building on them; they are undocumented and
  Microsoft changes the markup. Fail fast and loudly if a selector misses.
- **Scroll strategy:** step `viewportHeight * 0.8` (deliberately under a full page so
  windows overlap), re-assert `scrollTop` for ~150ms after each step because the list
  bounces back, and re-query all rows each step rather than diffing.
- **Auth:** Playwright `launch_persistent_context(user_data_dir=...)` with a **one-time
  headed login**. Headless is
  [documented as failing against Microsoft's login flow](https://github.com/microsoft/playwright/issues/32034)
  independent of Conditional Access. **Plan on headed.** SAP's Conditional Access may
  refuse automated Chromium regardless.

#### ⚠ Read before building the fallback

The Microsoft Services Agreement §3.a.vi prohibits "impermissible scraping" and automated
access. Being entitled to view the recording is **not** a carve-out — the restriction is on
the *method*, not the entitlement. SAP's own IT policy is a separate and likely more
immediate concern. Microsoft is also
[expanding bot detection in Teams](https://www.bleepingcomputer.com/news/microsoft/microsoft-adds-smarter-bot-protection-to-teams-meetings/)
with admin controls to block third-party notetakers; this design is not a join-as-participant
bot, which is a different risk profile, but the direction is clear.

**This is the user's call, not the agent's.** Confirm before building the scraping path.
The download path above carries none of this risk.

#### Dead ends — do not spend time here

- **Microsoft Graph transcript API.** Transcripts hang off the *organizer's* meeting object.
  `getAllTranscripts` requires `meetingOrganizerUserId` and returns only meetings that user
  organized; non-organizers get `404 ResourceNotFound`. The only real routes are a
  tenant-admin Application Access Policy or an app installed in the meeting chat *before the
  meeting ends*. No personal-token, DSAR, or recap-API shortcut exists.
  ([list transcripts](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts?view=graph-rest-1.0),
  [getAllTranscripts](https://learn.microsoft.com/en-us/graph/api/onlinemeeting-getalltranscripts?view=graph-rest-1.0))
- **Teams local cache.** The user checked `~/Library/Application Support/Microsoft/Teams/`
  — does not exist on their machine. New Teams uses
  `~/Library/Containers/com.microsoft.teams2/Data/Library/Application Support/Microsoft/MSTeams/`,
  worth one look, but transcripts are streamed from SharePoint rather than persisted to the
  local chat DB, so this was always speculative. Low priority now that the download path exists.

---

## 4. Open decisions for the user

1. **Does that recording have a Download button?** Decides Step 4 entirely.
2. **If not — is the ToS/SAP-policy risk acceptable?** Their call; do not decide it for them.
3. **Custom prompt: config file only, or a GUI editor too?** Config file ships first regardless.
4. **Same-title collision (B6): append `-2`, or the time?**

## 5. Definition of done

- `pytest tests/ -v` green (26 on macOS).
- The `.app` builds and runs from `/Applications`.
- Menu matches Variant B; root menu does not grow with meeting count.
- Ollama status visible at a glance, with working one-click Terminal start.
- A day with 3 meetings lists all 3 and opens the right one — **B1 regression-tested**.
- Killing `ollama serve` mid-run produces a note, a visible degraded marker, and a working
  retry — **B2 regression-tested**.
- Nothing assumes Obsidian.
