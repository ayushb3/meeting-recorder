#!/usr/bin/env python3
"""Generate SVG diagrams for the Meeting Recorder documentation.

Run from the repo root:
    .venv/bin/python docs/diagrams/generate.py

Outputs three SVG files next to this script:
    pipeline-flow.svg
    capture-architecture.svg
    menu-tree.svg

No third-party dependencies — pure hand-written SVG.
"""

from pathlib import Path

OUT_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Shared palette — works on both light and dark backgrounds.
# Uses strokes and semi-transparent fills rather than pure white or black.
# ---------------------------------------------------------------------------
PALETTE = {
    "bg":          "none",           # transparent canvas
    "box_fill":    "#2a4a6e",        # dark-ish blue, readable on light or dark
    "box_stroke":  "#5a9fd4",
    "label":       "#e8f0fa",        # near-white text on dark boxes
    "arrow":       "#8ab8d8",
    "dim":         "#546880",        # muted text / disabled box
    "warn":        "#c0703a",        # orange for the warning/error path
    "success":     "#3a8a5a",        # green accent
    "neutral":     "#3a5068",        # separator / section box
    "caption":     "#8ab8d8",        # section header text
}

TEXT_FONT = "font-family=\"system-ui, -apple-system, 'Segoe UI', sans-serif\""


def _rect(x, y, w, h, fill, stroke, rx=6):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" rx="{rx}"/>')


def _esc(txt):
    """Escape text for XML content. Labels contain things like 'macOS < 14.2'."""
    return (str(txt).replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _text(x, y, txt, fill, size=13, anchor="middle", weight="normal"):
    return (f'<text x="{x}" y="{y}" {TEXT_FONT} '
            f'font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}">{_esc(txt)}</text>')


def _arrow_h(x1, y, x2, color):
    """Horizontal arrow from (x1,y) to (x2,y)."""
    d = f"M{x1},{y} L{x2},{y}"
    tip = f"M{x2-8},{y-5} L{x2},{y} L{x2-8},{y+5}"
    return (f'<path d="{d}" stroke="{color}" stroke-width="1.5" fill="none"/>'
            f'<path d="{tip}" stroke="{color}" stroke-width="1.5" fill="none"/>')


def _arrow_v(x, y1, y2, color):
    """Vertical arrow from (x,y1) to (x,y2)."""
    d = f"M{x},{y1} L{x},{y2}"
    tip = f"M{x-5},{y2-8} L{x},{y2} L{x+5},{y2-8}"
    return (f'<path d="{d}" stroke="{color}" stroke-width="1.5" fill="none"/>'
            f'<path d="{tip}" stroke="{color}" stroke-width="1.5" fill="none"/>')


def _line(x1, y1, x2, y2, color, dash=""):
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{color}" '
            f'stroke-width="1.5" fill="none"{dash_attr}/>')


def svg_wrap(width, height, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="#1a2a3a" rx="10"/>'
            f'{body}'
            f'</svg>')


# ---------------------------------------------------------------------------
# 1. Pipeline flow diagram
# ---------------------------------------------------------------------------

def _pipeline_flow() -> str:
    W, H = 680, 420
    parts = []

    def box(x, y, w, h, label, sub="", fill=PALETTE["box_fill"], stroke=PALETTE["box_stroke"]):
        parts.append(_rect(x, y, w, h, fill, stroke))
        ty = y + h // 2 + (5 if sub else 5)
        if sub:
            ty = y + h // 2 - 4
        parts.append(_text(x + w // 2, ty, label, PALETTE["label"], size=13, weight="bold"))
        if sub:
            parts.append(_text(x + w // 2, y + h // 2 + 14, sub, PALETTE["caption"], size=11))

    # Input sources — left column
    box(20, 60, 140, 44, "Microphone", "sounddevice")
    box(20, 140, 140, 44, "System Audio", "Core Audio tap")

    # Merge arrow — to transcription block
    cx_src = 160
    cy_mic = 82
    cy_sys = 162
    cy_merge = 200

    # lines from sources to a join point
    parts.append(_line(cx_src, cy_mic, 240, cy_mic, PALETTE["arrow"]))
    parts.append(_line(cx_src, cy_sys, 240, cy_sys, PALETTE["arrow"]))
    parts.append(_line(240, cy_mic, 240, cy_sys, PALETTE["arrow"]))

    # arrow into whisper block
    parts.append(_arrow_h(240, cy_merge - 38, 280, PALETTE["arrow"]))

    # Whisper block
    box(280, 140, 140, 44, "whisper.cpp", "two-pass transcription")
    # arrow down
    parts.append(_arrow_v(350, 184, 220, PALETTE["arrow"]))
    # Merge block
    box(280, 220, 140, 44, "Merge + dedup", "bleed removal")
    parts.append(_arrow_v(350, 264, 300, PALETTE["arrow"]))
    # Ollama block
    box(280, 300, 140, 44, "Ollama LLM", "summary + title")
    parts.append(_arrow_v(350, 344, 380, PALETTE["arrow"]))
    # Note block
    box(280, 380, 140, 44, "meeting.md", "Obsidian vault")

    # Notification arrow out the right
    parts.append(_arrow_h(420, 402, 500, PALETTE["arrow"]))  # notification
    box(500, 380, 140, 44, "Notification", "click to open")

    # Labels on source arrows
    parts.append(_text(200, 74, "16 kHz mono", PALETTE["caption"], size=10))
    parts.append(_text(200, 154, "16 kHz mono", PALETTE["caption"], size=10))

    # Title
    parts.append(_text(W // 2, 34, "Pipeline Flow", PALETTE["caption"], size=15, weight="bold"))

    # Step numbers
    for i, (lx, ly, lt) in enumerate([
        (350, 136, "1"),
        (350, 216, "2"),
        (350, 296, "3"),
        (350, 376, "4"),
    ], 1):
        parts.append(f'<circle cx="{lx - 25}" cy="{ly + 22}" r="9" '
                     f'fill="{PALETTE["neutral"]}" stroke="{PALETTE["box_stroke"]}" stroke-width="1"/>')
        parts.append(_text(lx - 25, ly + 26, str(i), PALETTE["label"], size=10, weight="bold"))

    return svg_wrap(W, H, "\n".join(parts))


# ---------------------------------------------------------------------------
# 2. Capture architecture diagram
# ---------------------------------------------------------------------------

def _capture_architecture() -> str:
    W, H = 720, 480
    parts = []

    def box(x, y, w, h, label, sub="", fill=PALETTE["box_fill"], stroke=PALETTE["box_stroke"]):
        parts.append(_rect(x, y, w, h, fill, stroke))
        ty = y + h // 2 + 5 if not sub else y + h // 2 - 4
        parts.append(_text(x + w // 2, ty, label, PALETTE["label"], size=12, weight="bold"))
        if sub:
            parts.append(_text(x + w // 2, y + h // 2 + 14, sub, PALETTE["caption"], size=10))

    # --- Left panel: legacy BlackHole path ---
    parts.append(_rect(20, 40, 310, 400, "#1e2e3e", "#3a5068", rx=8))
    parts.append(_text(175, 64, "Legacy path (macOS < 14.2)", PALETTE["caption"], size=12, weight="bold"))

    box(55, 80, 120, 40, "App audio")
    parts.append(_arrow_v(115, 120, 150, PALETTE["arrow"]))
    box(55, 150, 120, 40, "BlackHole 2ch", "virtual driver")
    parts.append(_arrow_v(115, 190, 220, PALETTE["arrow"]))
    box(55, 220, 120, 40, "Multi-Output", "Audio MIDI Setup")

    # branch: BlackHole → recorder, Multi-Output → speakers
    parts.append(_line(115, 260, 115, 280, PALETTE["arrow"]))
    parts.append(_line(115, 280, 75, 280, PALETTE["arrow"]))
    parts.append(_line(115, 280, 175, 280, PALETTE["arrow"]))
    parts.append(_arrow_v(75, 280, 310, PALETTE["arrow"]))
    parts.append(_arrow_v(175, 280, 310, PALETTE["arrow"]))

    box(35, 310, 100, 40, "Recorder", "WAV file", fill=PALETTE["success"], stroke="#5abf8a")
    box(140, 310, 100, 40, "Speakers /", "headphones")

    # Warning: sample rate trap
    parts.append(_rect(30, 370, 270, 50, "#3a2010", PALETTE["warn"], rx=6))
    parts.append(_text(165, 390, "Sample-rate trap:", PALETTE["warn"], size=11, weight="bold"))
    parts.append(_text(165, 408, "BT earbuds (44100) vs BlackHole (48000)", PALETTE["caption"], size=10))

    # --- Right panel: tap path ---
    parts.append(_rect(380, 40, 310, 400, "#1e2e3e", "#3a5068", rx=8))
    parts.append(_text(535, 64, "New path (macOS 14.2+)", PALETTE["caption"], size=12, weight="bold"))

    box(450, 80, 120, 40, "App audio")

    # tap reads before output
    parts.append(_line(510, 120, 510, 160, PALETTE["arrow"]))
    # branch at y=160
    parts.append(_line(510, 160, 450, 160, PALETTE["arrow"]))
    parts.append(_line(510, 160, 570, 160, PALETTE["arrow"]))

    box(420, 170, 90, 40, "Core Audio", "process tap", fill=PALETTE["success"], stroke="#5abf8a")
    box(540, 170, 90, 40, "Output device", "speakers / BT")

    parts.append(_arrow_v(465, 210, 260, PALETTE["arrow"]))
    box(415, 260, 100, 40, "Recorder", "WAV file", fill=PALETTE["success"], stroke="#5abf8a")

    # "output device irrelevant" note
    parts.append(_rect(420, 330, 250, 50, "#102030", "#3a5068", rx=6))
    parts.append(_text(545, 350, "Output device is irrelevant:", PALETTE["caption"], size=11, weight="bold"))
    parts.append(_text(545, 368, "BT, wired, speakers — all captured", PALETTE["caption"], size=10))

    # Title
    parts.append(_text(W // 2, 24, "Capture Architecture", PALETTE["caption"], size=15, weight="bold"))

    return svg_wrap(W, H, "\n".join(parts))


# ---------------------------------------------------------------------------
# 3. Menu tree diagram
# ---------------------------------------------------------------------------

def _menu_tree() -> str:
    W, H = 560, 560
    parts = []

    # Draw a simple indented tree
    indent = 28
    row_h = 32
    start_y = 50
    start_x = 30

    rows = [
        # (depth, label, note, color_key)
        (0, "Menu Bar Icon", "", "caption"),
        (1, "● Start Recording", "→ ■ Stop Recording — MM:SS while active", "label"),
        (1, "— separator —", "", "dim"),
        (1, "Meetings ▸", "submenu", "label"),
        (2, "Today — Wed 16 Sep", "section header", "caption"),
        (3, "HH:MM  Meeting Name", "click to open note", "label"),
        (3, "⚠ HH:MM  Meeting Name", "degraded — has submenu", "warn"),
        (4, "Open Note ↗", "", "label"),
        (4, "Retry Summary", "re-runs pipeline", "label"),
        (4, "Reveal in Finder ↗", "", "label"),
        (2, "Earlier this week", "section header", "caption"),
        (3, "Mon    Meeting Name", "click to open note", "label"),
        (2, "— separator —", "", "dim"),
        (2, "~/Documents/…/Meetings", "location footer (non-clickable)", "dim"),
        (2, "Open Meetings Folder ↗", "", "label"),
        (2, "Change Location…", "opens Settings", "label"),
        (1, "Import Transcript from Stream… ↗", "URL prompt, then scrapes in Terminal", "label"),
        (1, "🟢/🟡/🔴 Ollama ▸", "colour shows server state", "label"),
        (2, "🟢 Running  /  🟡 Running — model not pulled  /  🔴 Not running", "", "caption"),
        (2, "model · host", "detail line", "dim"),
        (2, "— separator —", "", "dim"),
        (2, "Start Ollama in Terminal ↗", "opens Terminal with ollama serve", "label"),
        (2, "Pull Model… ↗", "opens Terminal with ollama pull", "label"),
        (2, "Refresh Ollama Status", "async re-probe", "label"),
        (1, "— separator —", "", "dim"),
        (1, "Settings…", "folder + device pickers", "label"),
        (1, "Quit", "", "label"),
    ]

    text_colors = {
        "caption": PALETTE["caption"],
        "label": PALETTE["label"],
        "dim": PALETTE["dim"],
        "warn": PALETTE["warn"],
    }

    parts.append(_text(W // 2, 28, "Menu Tree", PALETTE["caption"], size=15, weight="bold"))

    for i, (depth, label, note, color_key) in enumerate(rows):
        y = start_y + i * row_h + row_h // 2
        x = start_x + depth * indent
        col = text_colors.get(color_key, PALETTE["label"])

        # connector lines for indented items
        if depth > 0:
            px = start_x + (depth - 1) * indent + 14
            parts.append(_line(px, y - row_h // 2, px, y, PALETTE["neutral"]))
            parts.append(_line(px, y, x - 4, y, PALETTE["neutral"]))

        parts.append(_text(x, y + 4, label, col, size=11, anchor="start"))
        if note:
            nx = x + len(label) * 6.5 + 8
            parts.append(_text(nx, y + 4, f"  ← {note}", PALETTE["dim"], size=10, anchor="start"))

    return svg_wrap(W, H, "\n".join(parts))


# ---------------------------------------------------------------------------
# Write files
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    diagrams = {
        "pipeline-flow.svg": _pipeline_flow(),
        "capture-architecture.svg": _capture_architecture(),
        "menu-tree.svg": _menu_tree(),
    }
    for filename, content in diagrams.items():
        path = OUT_DIR / filename
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path}")
