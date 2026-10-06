"""Vault integration: a synthetic vault with invented names, nothing real."""
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from notes.vault import (
    VaultConfig,
    build_context,
    extract_sections,
    log_to_daily_note,
    merge_contexts,
    prepare_context,
    strip_unknown_links,
    VaultContext,
)

DAILY = """\
---
type: daily
---
# 2026-03-10

## Today
- [ ] Review the Orchard rollout
- [ ] Ping Dana about the schema

## Personal
- [ ] Buy stamps

## Active Projects
### Orchard
**About:** rollout tooling

## Accomplished
- did a thing

*← [[2026-03-09]] | [[2026-03-11]]*
"""


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Daily Notes").mkdir(parents=True)
    (root / "Daily Notes" / "2026-03-10.md").write_text(DAILY)
    (root / "Daily Notes" / "2026-03-09.md").write_text("## Today\n- [ ] Older item\n")
    (root / "Daily Notes" / "2026-03-20.md").write_text("## Today\n- [ ] Future item\n")
    (root / "Daily Notes" / "notes.md").write_text("## Today\n- [ ] not a daily note\n")
    (root / "NOW.md").write_text(
        "---\ntype: active\n---\n# Now\n- Finish [[Orchard]] handoff\n<!-- generated -->\n"
    )
    (root / "vault-index.json").write_text(
        json.dumps({"name_to_path": {"Dana Reyes": "People/Dana Reyes.md"}})
    )
    return root


def cfg(root, **kw):
    return VaultConfig(root=root, **kw)


class TestExtractSections:
    def test_keeps_only_wanted_sections(self):
        out = extract_sections(DAILY, ("Today", "Active Projects"))
        assert "Orchard rollout" in out and "rollout tooling" in out
        assert "stamps" not in out and "did a thing" not in out

    def test_case_insensitive_and_drops_frontmatter(self):
        out = extract_sections(DAILY, ("today",))
        assert "type: daily" not in out and "Dana" in out


class TestBuildContext:
    def test_reads_status_and_recent_dailies_up_to_the_date(self, vault):
        ctx = build_context(cfg(vault), as_of=date(2026, 3, 10))
        assert "Finish [[Orchard]] handoff" in ctx.context
        assert "Review the Orchard rollout" in ctx.context
        assert "Older item" in ctx.context
        assert "Future item" not in ctx.context          # after the meeting date
        assert "not a daily note" not in ctx.context
        assert "generated" not in ctx.context            # HTML comment stripped

    def test_allowed_links_include_context_and_index(self, vault):
        ctx = build_context(cfg(vault), as_of=date(2026, 3, 10))
        assert {"Orchard", "Dana Reyes"} <= ctx.allowed_links

    def test_respects_daily_note_count(self, vault):
        ctx = build_context(cfg(vault, daily_notes_to_read=1), as_of=date(2026, 3, 10))
        assert "Review the Orchard rollout" in ctx.context
        assert "Older item" not in ctx.context

    def test_is_truncated_to_the_budget(self, vault):
        ctx = build_context(cfg(vault, context_max_chars=120), as_of=date(2026, 3, 10))
        body = ctx.context.split("\n\n", 1)[1]
        assert len(body) <= 122 and body.endswith("…")

    def test_missing_root_degrades_to_nothing(self, tmp_path):
        assert build_context(cfg(tmp_path / "nope")).context is None

    def test_orient_command_failure_is_not_fatal(self, vault):
        with patch("notes.vault.subprocess.run", side_effect=OSError("no such command")):
            ctx = build_context(cfg(vault, orient_command="missing-cmd"), as_of=date(2026, 3, 10))
        assert ctx.context is not None

    def test_orient_command_runs_in_the_vault(self, vault):
        with patch("notes.vault.subprocess.run") as run:
            build_context(cfg(vault, orient_command="python3 scripts/orient.py"),
                          as_of=date(2026, 3, 10))
        args, kwargs = run.call_args
        assert args[0] == ["python3", "scripts/orient.py"]
        assert kwargs["cwd"] == vault


class TestPrepareContext:
    def test_no_vault_leaves_context_alone(self):
        assert prepare_context(None, "attendees: A") == ("attendees: A", None)

    def test_merges_user_context_and_allows_its_links(self, vault):
        merged, vctx = prepare_context(cfg(vault), "Met with [[Casey]]", date(2026, 3, 10))
        assert merged.startswith("Met with [[Casey]]")
        assert "Orchard" in merged
        assert "Casey" in vctx.allowed_links


class TestStripUnknownLinks:
    def test_keeps_known_flattens_unknown(self):
        out = strip_unknown_links("See [[Orchard]] and [[Invented Thing]].", {"Orchard"})
        assert out == "See [[Orchard]] and Invented Thing."

    def test_alias_is_used_when_flattening(self):
        assert strip_unknown_links("[[Nope|the plan]]", set()) == "the plan"

    def test_case_insensitive(self):
        assert strip_unknown_links("[[orchard]]", {"Orchard"}) == "[[orchard]]"


class TestLogToDailyNote:
    def _note(self, vault):
        path = vault / "Meetings" / "2026-W11" / "kickoff" / "meeting.md"
        path.parent.mkdir(parents=True)
        path.write_text("x")
        return path

    def test_disabled_by_default(self, vault):
        assert log_to_daily_note(cfg(vault), self._note(vault), "Kickoff",
                                 datetime(2026, 3, 10, 9)) is False

    def test_adds_path_link_under_existing_section(self, vault):
        daily = vault / "Daily Notes" / "2026-03-10.md"
        daily.write_text(DAILY.replace("## Accomplished", "## Notes Created\n- [[earlier]] — x\n\n## Accomplished"))
        ok = log_to_daily_note(cfg(vault, write_daily_note=True), self._note(vault),
                               "Kickoff", datetime(2026, 3, 10, 9))
        text = daily.read_text()
        assert ok
        assert "- [[Meetings/2026-W11/kickoff/meeting|Kickoff]] — meeting note" in text
        assert text.index("[[earlier]]") < text.index("Kickoff") < text.index("## Accomplished")

    def test_creates_section_before_footer(self, vault):
        log_to_daily_note(cfg(vault, write_daily_note=True), self._note(vault),
                          "Kickoff", datetime(2026, 3, 10, 9))
        text = (vault / "Daily Notes" / "2026-03-10.md").read_text()
        assert text.index("## Notes Created") < text.index("*←")
        assert text.rstrip().endswith("*← [[2026-03-09]] | [[2026-03-11]]*")

    def test_idempotent(self, vault):
        c = cfg(vault, write_daily_note=True)
        note = self._note(vault)
        assert log_to_daily_note(c, note, "Kickoff", datetime(2026, 3, 10, 9)) is True
        assert log_to_daily_note(c, note, "Kickoff", datetime(2026, 3, 10, 9)) is False
        assert (vault / "Daily Notes" / "2026-03-10.md").read_text().count("Kickoff") == 1

    def test_missing_daily_note_is_not_created(self, vault):
        assert log_to_daily_note(cfg(vault, write_daily_note=True), self._note(vault),
                                 "Kickoff", datetime(2026, 4, 1, 9)) is False
        assert not (vault / "Daily Notes" / "2026-04-01.md").exists()

    def test_note_outside_vault_is_skipped(self, vault, tmp_path):
        outside = tmp_path / "elsewhere" / "meeting.md"
        outside.parent.mkdir()
        outside.write_text("x")
        assert log_to_daily_note(cfg(vault, write_daily_note=True), outside,
                                 "Kickoff", datetime(2026, 3, 10, 9)) is False


def test_merge_contexts_skips_blanks():
    assert merge_contexts(None, VaultContext()) is None
    assert merge_contexts("  ", VaultContext("v")) == "v"


class TestPipelineIntegration:
    def _run(self, tmp_path, vault_cfg, summary="## TL;DR\nTalked about [[Orchard]] and [[Made Up]]."):
        from pipeline.processor import run_pipeline
        from transcriber.whisper import Segment

        source = tmp_path / "in.wav"
        source.write_bytes(b"x")
        segs = [Segment(start_seconds=1.0, text="Hello.", source="system")]
        with patch("pipeline.processor.transcribe_raw", return_value=segs), \
             patch("pipeline.processor.summarize", return_value=summary) as summ:
            result = run_pipeline(
                mic_path=source, system_path=source,
                session_dt=datetime(2026, 3, 10, 9, 0), duration_seconds=60,
                output_dir=tmp_path / "vault" / "Meetings", whisper_binary=Path("w"),
                whisper_model=Path("m"), ollama_model="m", ollama_host="h",
                keep_audio=False, meeting_name="Kickoff", single_source=source,
                llm_context="Attendee: Casey", vault=vault_cfg,
            )
        return result, summ

    def test_context_flows_in_and_unknown_links_are_flattened(self, vault, tmp_path):
        result, summ = self._run(tmp_path, cfg(vault, write_daily_note=True))
        assert "Orchard rollout" in summ.call_args.kwargs["context"]
        assert "Attendee: Casey" in summ.call_args.kwargs["context"]
        body = result.note_path.read_text()
        assert "[[Orchard]]" in body and "[[Made Up]]" not in body and "Made Up" in body

    def test_note_is_logged_in_the_daily_note(self, vault, tmp_path):
        self._run(tmp_path, cfg(vault, write_daily_note=True))
        assert "Kickoff" in (vault / "Daily Notes" / "2026-03-10.md").read_text()

    def test_no_vault_changes_nothing(self, tmp_path):
        result, summ = self._run(tmp_path, None)
        assert summ.call_args.kwargs["context"] == "Attendee: Casey"
        assert "[[Made Up]]" in result.note_path.read_text()   # untouched without a vault
