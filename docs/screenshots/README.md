# Screenshots

This folder holds screenshot assets for the documentation. The screenshots below need to be captured manually — they require the app to be running and interacted with in real time, which cannot be automated.

Capture method: use Command-Shift-4, then press Space to switch to window-capture mode, then click the menu or window you want. This produces a retina PNG with a shadow.

---

## Needed screenshots

### 1. `menu-idle.png` — Menu bar menu, idle state

**What to show:** The full menu open with Ollama running (🟢) and at least one meeting in the Meetings submenu.

**Steps:**
1. Launch Meeting Recorder from Finder.
2. Make sure `ollama serve` is running.
3. Click the menu bar icon to open the menu.
4. Press Command-Shift-4, then Space, then click the open menu.

**Used in:** getting-started.md (overview section)

---

### 2. `menu-recording.png` — Menu bar menu, recording state

**What to show:** The menu open while recording is active, showing the "■ Stop Recording — MM:SS" item and the elapsed-time title next to the icon.

**Steps:**
1. Click **● Start Recording**.
2. Wait a few seconds so the timer shows a non-zero time.
3. Click the icon again to open the menu (without stopping the recording).
4. Capture the open menu.

**Used in:** menu-reference.md

---

### 3. `menu-meetings-degraded.png` — Meetings submenu with a ⚠ entry

**What to show:** The Meetings submenu open, with at least one entry showing the ⚠ prefix and the sub-items (Open Note, Retry Summary, Reveal in Finder) visible.

**Steps:**
1. You need an existing session that has a `*.error` file alongside its note — either from a real Ollama failure, or by creating an empty `.error` file in any session folder.
2. Hover over the Meetings submenu to open it.
3. Hover over a ⚠ entry so its submenu appears.
4. Capture the full menu tree (both levels).

**Tip:** Use Command-Shift-4 with a drag to capture a region rather than a window if the two-level submenu does not fit in window-capture mode.

**Used in:** menu-reference.md (degraded entries section)

---

### 4. `ollama-submenu.png` — Ollama submenu states

Ideally three separate screenshots, one per state:

- `ollama-green.png` — 🟢 Running
- `ollama-yellow.png` — 🟡 Running — model not pulled
- `ollama-red.png` — 🔴 Not running

**Steps for 🔴:** Run `pkill ollama` or quit the Ollama app, wait a few seconds, then click Re-check Now.
**Steps for 🟡:** Pull a model name that does not exist: add `model = "nonexistent:latest"` to `[ollama]` in config temporarily, then Re-check Now.
**Steps for 🟢:** Normal state with `ollama serve` running and the correct model pulled.

**Used in:** menu-reference.md (Ollama submenu section)

---

### 5. `settings-window.png` — Settings window

**What to show:** The Settings window open with the folder picker and device dropdowns visible.

**Steps:**
1. Click **Settings…** from the menu.
2. Capture the window using Command-Shift-4 + Space + click.

**Used in:** getting-started.md (configuration section)

---

### 6. `stop-modal.png` — Stop recording dialog

**What to show:** The two dialogs that appear after clicking Stop Recording — first the meeting name dialog, then the context dialog.

**Steps:**
1. Start a short recording.
2. Click Stop.
3. Capture the "Meeting Saved" name dialog before clicking Next.
4. Click Next, then capture the "Meeting Context" dialog.

**Used in:** getting-started.md (recording section)

---

## Referencing screenshots in docs

Once captured, drop the PNG files into this folder. The docs reference them relative to the project root. For example, to add screenshot 1 to a doc:

```markdown
![Menu bar menu, idle state](../docs/screenshots/menu-idle.png)
```

The docs currently contain placeholder notes where screenshots would go rather than broken image tags.
