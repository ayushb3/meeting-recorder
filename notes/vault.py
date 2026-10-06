"""Optional Obsidian-vault integration: context in, a daily-note link out.

Entirely driven by an optional ``[vault]`` section of the user's config; with
none, nothing here runs. Nothing about any particular vault is baked in — the
section names to read, the command that refreshes a status file, and the folder
layout are all settings.

Two jobs:

* **Before summarizing**, gather a small, bounded block of "what is going on"
  (an optional refresh command, a status file such as ``NOW.md``, and the active
  sections of the last few daily notes) so the summary can use real project and
  people names instead of inventing them.
* **After writing the note**, log it in the daily note for the meeting's date.

Every failure degrades to "no vault context" or "no link"; nothing here may
block a note from being written.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_SECTIONS = ("Today", "Active Projects", "Waiting on / Blocked")

_DAILY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|([^\]]*))?\]\]")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


@dataclass(frozen=True)
class VaultConfig:
    root: Path
    orient_command: str | None = None
    status_file: str = "NOW.md"
    daily_notes_dir: str = "Daily Notes"
    daily_notes_to_read: int = 2
    sections: tuple[str, ...] = DEFAULT_SECTIONS
    context_max_chars: int = 3000
    index_file: str = "vault-index.json"
    write_daily_note: bool = False
    notes_section: str = "Notes Created"


@dataclass
class VaultContext:
    """What the summarizer is told, plus which wikilinks it may keep."""

    context: str | None = None
    allowed_links: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return _COMMENT_RE.sub("", _FRONTMATTER_RE.sub("", text, count=1)).strip()


def extract_sections(text: str, wanted: tuple[str, ...]) -> str:
    """The bodies of the ``## <heading>`` sections named in *wanted*.

    Matching is case-insensitive on the heading text. Anything else in the note
    is dropped, which keeps long daily notes from drowning the prompt.
    """
    wanted_l = {w.lower() for w in wanted}
    out: list[str] = []
    keep = False
    for line in _clean(text).splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            keep = heading.lower() in wanted_l
            if keep:
                out.append(f"## {heading}")
            continue
        if keep and line.strip():
            out.append(line.rstrip())
    return "\n".join(out)


def _run_orient(cfg: VaultConfig) -> None:
    if not cfg.orient_command:
        return
    try:
        subprocess.run(
            shlex.split(cfg.orient_command),
            cwd=cfg.root, capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        log.warning("Vault orient command failed (continuing without it): %s", exc)


def _recent_daily_notes(cfg: VaultConfig, as_of: date, count: int) -> list[Path]:
    folder = cfg.root / cfg.daily_notes_dir
    if count <= 0 or not folder.is_dir():
        return []
    dated: list[tuple[date, Path]] = []
    for path in folder.iterdir():
        match = _DAILY_RE.match(path.name)
        if not match:
            continue
        try:
            when = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            continue
        if when <= as_of:
            dated.append((when, path))
    dated.sort(reverse=True)
    return [path for _, path in dated[:count]]


def _index_names(cfg: VaultConfig) -> set[str]:
    """Note names from the vault index, so existing notes stay linkable."""
    path = cfg.root / cfg.index_file
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names = data.get("name_to_path") if isinstance(data, dict) else None
    return {str(k) for k in names} if isinstance(names, dict) else set()


def _link_targets(text: str) -> set[str]:
    return {m.group(1).strip() for m in _WIKILINK_RE.finditer(text)}


_INSTRUCTIONS = (
    "Current work context from the user's notes. Use it only to recognise names "
    "and to relate the meeting to existing work; do not summarise it. When you "
    "mention a project or person that appears below, write it as [[Name]] using "
    "that exact spelling. Never create a [[link]] to anything not listed below."
)


def build_context(cfg: VaultConfig, as_of: date | None = None) -> VaultContext:
    """Gather the bounded context block. Never raises."""
    try:
        return _build_context(cfg, as_of or date.today())
    except Exception as exc:  # the vault is an enhancement, never a dependency
        log.warning("Could not build vault context: %s", exc)
        return VaultContext()


def _build_context(cfg: VaultConfig, as_of: date) -> VaultContext:
    if not cfg.root.is_dir():
        log.warning("Vault root %s is not a directory; skipping vault context", cfg.root)
        return VaultContext()

    _run_orient(cfg)

    chunks: list[str] = []
    status = cfg.root / cfg.status_file
    if status.is_file():
        chunks.append(_clean(status.read_text(encoding="utf-8")))
    for path in _recent_daily_notes(cfg, as_of, cfg.daily_notes_to_read):
        body = extract_sections(path.read_text(encoding="utf-8"), cfg.sections)
        if body:
            chunks.append(f"### {path.stem}\n{body}")

    body = "\n\n".join(c for c in chunks if c)
    if not body:
        return VaultContext()

    allowed = _link_targets(body) | _index_names(cfg)
    if len(body) > cfg.context_max_chars:
        body = body[: cfg.context_max_chars].rstrip() + "\n…"
    return VaultContext(context=f"{_INSTRUCTIONS}\n\n{body}", allowed_links=allowed)


def merge_contexts(user_context: str | None, vault_context: VaultContext) -> str | None:
    parts = [p for p in (user_context, vault_context.context) if p and p.strip()]
    return "\n\n".join(parts) or None


def prepare_context(
    cfg: VaultConfig | None, user_context: str | None, as_of: date | None = None
) -> tuple[str | None, VaultContext | None]:
    """The context to summarize with, and the vault context to guard output with.

    With no vault configured this returns *user_context* untouched and None, so
    callers can treat "no vault" and "vault gave nothing" identically.
    """
    if cfg is None:
        return user_context, None
    vault_context = build_context(cfg, as_of)
    allowed = vault_context.allowed_links | _link_targets(user_context or "")
    vault_context = VaultContext(vault_context.context, allowed)
    return merge_contexts(user_context, vault_context), vault_context


# ---------------------------------------------------------------------------
# Guarding the model's output
# ---------------------------------------------------------------------------

def strip_unknown_links(text: str, allowed: set[str]) -> str:
    """Replace [[links]] to unknown notes with their plain text.

    A model told to link only known names will still occasionally invent one, and
    an invented link silently creates a dangling note in Obsidian. Matching is
    case-insensitive; with no allowed set at all, every link is flattened.
    """
    allowed_l = {a.lower() for a in allowed}

    def repl(match: re.Match) -> str:
        target, alias = match.group(1).strip(), match.group(2)
        if target.lower() in allowed_l:
            return match.group(0)
        return (alias or target).strip()

    return _WIKILINK_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# Writing back
# ---------------------------------------------------------------------------

def _vault_relative_link(cfg: VaultConfig, note_path: Path) -> str | None:
    """Path-style wikilink target. Plain 'meeting' would be ambiguous: every note
    this app writes is named meeting.md."""
    try:
        rel = note_path.resolve().relative_to(cfg.root.resolve())
    except ValueError:
        return None
    return rel.with_suffix("").as_posix()


def log_to_daily_note(
    cfg: VaultConfig, note_path: Path, title: str, when: datetime
) -> bool:
    """Add a link to the note under the daily note's notes section.

    Returns True when a link was added. Idempotent. Does not create a missing
    daily note — the vault's own tooling owns that structure — and does nothing
    if the note lives outside the vault.
    """
    if not cfg.write_daily_note:
        return False
    link = _vault_relative_link(cfg, note_path)
    if link is None:
        log.info("Note is outside the vault; not logging it in a daily note")
        return False
    daily = cfg.root / cfg.daily_notes_dir / f"{when.date().isoformat()}.md"
    if not daily.is_file():
        log.info("No daily note at %s; not logging the meeting", daily.name)
        return False
    try:
        text = daily.read_text(encoding="utf-8")
        if f"[[{link}" in text:
            return False
        bullet = f"- [[{link}|{title}]] — meeting note"
        daily.write_text(_insert_bullet(text, cfg.notes_section, bullet), encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("Could not update daily note %s: %s", daily.name, exc)
        return False


_FOOTER_RE = re.compile(r"^\*←.*\*\s*$")


def _insert_bullet(text: str, section: str, bullet: str) -> str:
    """Append *bullet* to ``## <section>``, creating it before the footer if absent."""
    lines = text.rstrip("\n").split("\n")
    heading = f"## {section}"
    start = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if start is None:
        footer = next((i for i in range(len(lines) - 1, -1, -1) if _FOOTER_RE.match(lines[i])), None)
        block = ["", heading, "", bullet]
        if footer is None:
            lines.extend(block)
        else:
            lines[footer:footer] = block[1:] + [""]
        return "\n".join(lines) + "\n"
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## ") and not _FOOTER_RE.match(lines[end]):
        end += 1
    # insert after the last non-blank line of the section
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, bullet)
    return "\n".join(lines) + "\n"
