# Screenshots

This folder contains screenshot assets used throughout the documentation. All eight screenshots have been captured and are ready to use.

---

## Existing screenshots

| File | What it shows |
|---|---|
| `menu-idle.png` | Root menu, idle state: ● Start Recording, Meetings ▸, 🟢 Ollama ▸, Settings…, Quit |
| `menu-recording.png` | Root menu while recording: ■ Stop Recording — elapsed time counter |
| `menu-processing.png` | Root menu during the pipeline: Processing… item greyed out and not clickable |
| `menu-meetings.png` | Meetings submenu expanded — Today grouping with times and titles, a ⚠ degraded entry (Roadmap Sync) with a disclosure arrow, Earlier this week section, Failed (no note) section with a ⚠ entry, path caption, Open Meetings Folder ↗, Change Location… |
| `notification.png` | macOS notification: Meeting Recorder / Note saved / Cloud Feature Development |
| `ollama-green.png` | Ollama submenu, healthy: 🟢 Running, gemma:latest · http://localhost:11434, action items |
| `ollama-red.png` | Ollama submenu, unreachable: 🔴 Not running, no server at http://localhost:11434, action items |
| `settings-window.png` | Settings window: Recording Storage, Audio Devices (with dropdowns and mic threshold), Transcription, AI Summary, Processing sections, Cancel/Save buttons |

> `menu-meetings.png` was captured against the demo vault (`scripts/demo_vault.py`). Re-captures should use the demo vault too so no real meeting names, colleague names, or internal project names appear in shared docs.

---

## Re-capturing a screenshot

Use the same technique for all captures: **Command-Shift-4, then Space** to switch to window-capture mode, then click the menu or window. This produces a retina PNG with a drop shadow.

### Prerequisite: switch to the demo vault

Before capturing any screenshot that shows meeting names, generate and switch to the demo vault so real names do not appear in docs.

**Generate the vault:**

```bash
python scripts/demo_vault.py
```

This writes fictional meetings to `~/Documents/MeetingRecorderDemo`. Pass `--force` to overwrite an existing demo vault.

**Switch to it** (Settings UI recommended):

1. Click the menu bar icon → **Settings…** → **Output Folder** → **Browse…**
2. Navigate to `~/Documents/MeetingRecorderDemo` and select it.
3. Click **Save**.

**Restore your real vault** when done:

1. **Settings…** → **Output Folder** → **Browse…** → select your original path.
2. Verify the app shows your real meetings in the Meetings submenu.
3. Optionally delete the demo vault: `rm -rf ~/Documents/MeetingRecorderDemo`

---

### `menu-idle.png`

**Steps:**
1. Launch Meeting Recorder from Finder (not from a terminal).
2. Make sure `ollama serve` is running.
3. Click the menu bar icon to open the menu.
4. Command-Shift-4 → Space → click the open menu.

**Used in:** README.md, getting-started.md, menu-reference.md

---

### `menu-recording.png`

**Steps:**
1. Click **● Start Recording**.
2. Wait a few seconds so the timer shows a non-zero elapsed time.
3. Click the menu bar icon to open the menu without stopping the recording.
4. Capture the open menu.

**Used in:** menu-reference.md

---

### `menu-processing.png`

**Steps:**
1. Stop an active recording.
2. While the pipeline is running (menu shows Processing…), click the icon to open the menu.
3. Capture the menu — the Processing… item will be greyed out.

**Used in:** menu-reference.md

---

### `menu-meetings.png`

> Capture with the demo vault active — it includes a Roadmap Sync session with a `summarize.error` file that appears as a ⚠ degraded entry.

**Steps:**
1. Switch to the demo vault (see prerequisite above).
2. Click the menu bar icon to open the root menu.
3. Hover over **Meetings ▸** to open the submenu.
4. Capture the submenu (Command-Shift-4, drag to capture a region if the full submenu does not fit in window-capture mode).

**Used in:** README.md, menu-reference.md

---

### `notification.png`

**Steps:**
1. Complete a recording with the demo vault active so the meeting title is fictional.
2. Capture the macOS notification that appears in the top-right corner immediately after the pipeline finishes.

**Used in:** menu-reference.md, getting-started.md

---

### `ollama-green.png`

**Steps:**
1. Make sure `ollama serve` is running and the configured model is pulled.
2. Click the menu bar icon → hover over **🟢 Ollama ▸**.
3. Capture the submenu.

**Used in:** menu-reference.md

---

### `ollama-red.png`

**Steps:**
1. Quit Ollama (`pkill ollama` or quit the Ollama desktop app) and wait a few seconds.
2. Click **Refresh Ollama Status** or wait for the 45-second probe.
3. Click the menu bar icon → hover over **🔴 Ollama ▸**.
4. Capture the submenu.

**Used in:** menu-reference.md, troubleshooting.md

---

### `settings-window.png`

**Steps:**
1. Click **Settings…** from the menu.
2. Command-Shift-4 → Space → click the Settings window.

**Used in:** README.md, getting-started.md

---

## Referencing screenshots in docs

From files inside `docs/` (e.g. `docs/menu-reference.md`):

```markdown
![Alt text describing what the screenshot shows](screenshots/filename.png)
```

From the project root `README.md`:

```markdown
![Alt text describing what the screenshot shows](docs/screenshots/filename.png)
```
