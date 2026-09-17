#!/usr/bin/env python3
"""demo_vault.py — populate a throwaway vault with fictional meeting data.

Used so documentation screenshots never contain real meeting names.
Run this, point the app at the generated folder, take screenshots, then
switch back to your real vault and delete the demo folder.

Usage:
    python scripts/demo_vault.py [TARGET_DIR] [--force]

TARGET_DIR defaults to ~/Documents/MeetingRecorderDemo.

Safety rule: the script refuses to run if TARGET_DIR already contains files
(i.e. is non-empty), unless --force is passed. This prevents accidental
overwrites of the real vault.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Summary bodies — all fictional names and content
# ---------------------------------------------------------------------------

def _standup_summary() -> str:
    return """\
## TL;DR

Quick team sync. No blockers. Alex Chen finishing the auth service PR today; \
Priya Raman will pick up the API documentation ticket.

## Topics Covered

- Status updates from each team member
- Deployment schedule for the week
- Reminder about Friday retro at 4 pm

## Key Decisions

- Hold the release until end of week to include Priya's docs update.

## Action Items

- **Alex Chen** — open PR for auth service by EOD
- **Priya Raman** — draft API docs outline, share by Wednesday
- **Jordan Lee** — update the deployment runbook before Friday
"""


def _design_review_summary() -> str:
    return """\
## TL;DR

Reviewed new dashboard mockups for the analytics module. Team aligned on \
the card-based layout; colour palette needs one more iteration before sign-off.

## Topics Covered

- Walkthrough of Figma mockups v3 from Morgan Blake (design lead)
- Accessibility contrast ratios on primary CTA buttons
- Mobile breakpoint behaviour for the summary cards
- Handoff checklist and timeline

## Key Decisions

- Adopt card layout (option B) for the analytics dashboard.
- Morgan Blake to update the colour palette by next Monday.
- Dev handoff blocked on palette approval — target: next Wednesday.

## Action Items

- **Morgan Blake** — revise colour palette, share updated Figma link by Monday
- **Alex Chen** — review contrast ratios on the new palette once available
- **Jordan Lee** — add mobile breakpoint specs to the design system by Friday
"""


def _roadmap_sync_summary() -> str:
    return """\
## TL;DR

Q3 roadmap reviewed with leadership. Two features pushed to Q4 due to \
capacity. Infrastructure migration stays on track.

## Topics Covered

- Q3 feature completion status (78% complete)
- Capacity shortfall: search improvements and notification centre deprioritised
- Infrastructure migration milestone review
- Q4 preview — what moves from backlog

## Key Decisions

- Deprioritise search v2 and notification centre to Q4 to protect Q3 launch.
- Infrastructure migration milestone stays in Q3; no scope changes.
- Schedule a stakeholder update email by end of week.

## Action Items

- **Priya Raman** — update the roadmap board and notify affected teams
- **Sam Ortega** — draft stakeholder update email, send by Friday
- **Alex Chen** — confirm engineering capacity estimate for Q4 search work
"""


def _one_on_one_summary() -> str:
    return """\
## TL;DR

Career check-in with Alex Chen. Good progress on technical skills; \
interested in taking on a project lead role next quarter.

## Topics Covered

- Progress against H2 growth goals
- Interest in project lead opportunity on the data pipeline initiative
- Feedback on recent code reviews
- Work/life balance check-in

## Key Decisions

- Alex Chen to shadow the data pipeline project lead this sprint as preparation.
- Revisit project lead candidacy in the next 1:1 (in two weeks).

## Action Items

- **Alex Chen** — prepare a short proposal for data pipeline involvement
- **[you]** — connect Alex with the current project lead by end of week
"""


def _vendor_call_summary() -> str:
    return """\
## TL;DR

Introductory call with CloudBridge (vendor). Evaluated their data connector \
product for potential integration. Promising, but pricing needs negotiation.

## Topics Covered

- CloudBridge product demo: data connector and ETL pipeline features
- Pricing tiers and enterprise licensing options
- Integration complexity estimate with our existing stack
- Security and compliance (SOC 2 Type II, GDPR)
- Next steps and evaluation timeline

## Key Decisions

- Request a 30-day trial licence to run a proof of concept.
- Do not share internal schema details until NDA is in place.

## Action Items

- **Sam Ortega** — follow up with CloudBridge rep (Dana Webb) to arrange NDA
- **Jordan Lee** — set up trial environment for PoC
- **Priya Raman** — define PoC success criteria by end of week
"""


def _sprint_planning_summary() -> str:
    return """\
## TL;DR

Sprint 24 planning complete. 34 story points committed. Key focus areas: \
auth service hardening, dashboard performance, and documentation.

## Topics Covered

- Velocity review: Sprint 23 delivered 31 of 36 points
- Backlog grooming — 8 stories re-estimated
- Capacity: Alex Chen at 80% (PTO Friday); full capacity for rest of team
- Risk: external API dependency for the notifications feature

## Key Decisions

- Cap the sprint at 34 points to account for reduced capacity.
- Notifications feature pushed one sprint due to external API uncertainty.
- Add automated regression tests to the auth service stories as acceptance criteria.

## Action Items

- **Alex Chen** — break down auth service stories into sub-tasks before standup
- **Priya Raman** — schedule mid-sprint review with stakeholders
- **Jordan Lee** — investigate external API status, report back by Tuesday
- **Morgan Blake** — finalise UX specs for the performance dashboard tickets
"""


# ---------------------------------------------------------------------------
# Fake transcripts
# ---------------------------------------------------------------------------

_TRANSCRIPTS: dict[str, list[str]] = {
    "Weekly Standup": [
        "[00:00] Alex Chen: Morning everyone. I'm wrapping up the auth service PR today.",
        "[00:15] Priya Raman: All good on my end. I'll start the API docs ticket this afternoon.",
        "[00:30] Jordan Lee: Deployment runbook is updated. No blockers.",
        "[00:45] Alex Chen: Let's keep the release until end of week then?",
        "[01:00] Priya Raman: Agreed. Docs need to be in before we ship.",
    ],
    "Design Review": [
        "[00:00] Morgan Blake: Here are the v3 mockups. I went with a card layout for the dashboard.",
        "[01:30] Alex Chen: The contrast on these primary buttons might not pass AA.",
        "[02:00] Morgan Blake: Good catch. I'll run them through the checker and revise.",
        "[03:00] Jordan Lee: Mobile breakpoints — do we have specs in the design system yet?",
        "[03:30] Morgan Blake: Not yet. I'll add those by Friday.",
    ],
    "Roadmap Sync": [
        "[00:00] Sam Ortega: We're at 78% on Q3. Search v2 and notifications are at risk.",
        "[02:00] Priya Raman: Engineering capacity is tight. We might need to push one of them.",
        "[04:00] Sam Ortega: Let's deprioritise both to protect the Q3 launch date.",
        "[05:30] Alex Chen: Confirmed. We can move them to Q4 without impacting the milestone.",
        "[07:00] Sam Ortega: I'll send a stakeholder update by Friday.",
    ],
    "1:1 with Alex Chen": [
        "[00:00] Alex Chen: I've been thinking about the project lead opportunity you mentioned.",
        "[02:00] [you]: How are you feeling about your current workload?",
        "[02:30] Alex Chen: Manageable. I'd like to take on more ownership.",
        "[05:00] [you]: The data pipeline initiative needs a lead next quarter. Want to shadow it?",
        "[05:45] Alex Chen: Absolutely. I'll put together a short proposal.",
    ],
    "Vendor Call": [
        "[00:00] Dana Webb (CloudBridge): Thanks for joining. Let me walk you through our connector.",
        "[05:00] Jordan Lee: What does the enterprise pricing look like?",
        "[05:30] Dana Webb (CloudBridge): Starts at $24k/year. Volume discounts available.",
        "[08:00] Sam Ortega: We'd need an NDA before sharing any schema details.",
        "[08:30] Dana Webb (CloudBridge): Absolutely. I'll send one over today.",
    ],
    "Sprint Planning": [
        "[00:00] Alex Chen: Last sprint we got 31 of 36 points. Notifications slipped.",
        "[02:00] Priya Raman: External API dependency is still unresolved.",
        "[03:00] Alex Chen: Let's push notifications one more sprint.",
        "[06:00] Jordan Lee: Auth service hardening and dashboard perf are the priority.",
        "[08:00] Alex Chen: I'm at 80% this week — PTO Friday.",
        "[09:00] Priya Raman: Capping at 34 points sounds right then.",
    ],
}


# ---------------------------------------------------------------------------
# Meeting specs
# Each dict: title, duration_min, hour, minute, days_ago, degraded, error_only
# ---------------------------------------------------------------------------

_MEETINGS = [
    # TODAY — several meetings at different times
    {
        "title": "Weekly Standup",
        "duration_min": 15,
        "hour": 9,
        "minute": 0,
        "days_ago": 0,
        "degraded": False,
        "summary_fn": _standup_summary,
    },
    {
        "title": "Design Review",
        "duration_min": 45,
        "hour": 10,
        "minute": 30,
        "days_ago": 0,
        "degraded": False,
        "summary_fn": _design_review_summary,
    },
    {
        "title": "Roadmap Sync",
        "duration_min": 60,
        "hour": 13,
        "minute": 0,
        "days_ago": 0,
        "degraded": True,   # ⚠ — summarize.error present
        "summary_fn": _roadmap_sync_summary,
    },
    {
        "title": "1:1 with Alex Chen",
        "duration_min": 30,
        "hour": 15,
        "minute": 0,
        "days_ago": 0,
        "degraded": False,
        "summary_fn": _one_on_one_summary,
    },
    # EARLIER THIS WEEK
    {
        "title": "Vendor Call",
        "duration_min": 50,
        "hour": 11,
        "minute": 0,
        "days_ago": 2,
        "degraded": False,
        "summary_fn": _vendor_call_summary,
    },
    {
        "title": "Sprint Planning",
        "duration_min": 90,
        "hour": 14,
        "minute": 0,
        "days_ago": 3,
        "degraded": False,
        "summary_fn": _sprint_planning_summary,
    },
]

# A session that failed before any note was written (no meeting.md)
_ERROR_ONLY_SESSION = {
    "hour": 16,
    "minute": 30,
    "days_ago": 1,
    "error_stage": "transcribe",
}


# ---------------------------------------------------------------------------
# Core generation logic
# ---------------------------------------------------------------------------

def _is_real_data(target: Path) -> bool:
    """Return True if target already contains any files."""
    if not target.exists():
        return False
    return any(target.rglob("*"))


def _build_dt(days_ago: int, hour: int, minute: int) -> datetime:
    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days_ago) + timedelta(hours=hour, minutes=minute)


def _session_folder_name(dt: datetime) -> str:
    """Return YYYY-MM-DD-HHhMM timestamp folder name matching the app convention."""
    return dt.strftime("%Y-%m-%d-%Hh%M")


def generate_demo_vault(target: Path) -> None:
    """Write all demo sessions into *target* (must already exist and be empty or absent)."""
    # We need write_note and week_folder from the repo
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from notes.writer import write_note, week_folder  # noqa: PLC0415

    for spec in _MEETINGS:
        title = spec["title"]
        dt = _build_dt(spec["days_ago"], spec["hour"], spec["minute"])
        duration_sec = spec["duration_min"] * 60
        summary_text = spec["summary_fn"]()
        transcript = _TRANSCRIPTS.get(title, [f"[00:00] {title} transcript."])

        # Session directory: week_folder / YYYY-MM-DD-HHhMM-<slug>
        slug = title.lower().replace(" ", "-").replace(":", "").replace("'", "")
        session_name = f"{_session_folder_name(dt)}-{slug}"
        session_dir = target / week_folder(dt) / session_name
        session_dir.mkdir(parents=True, exist_ok=True)

        write_note(
            dt=dt,
            duration_seconds=duration_sec,
            summary=summary_text,
            transcript_lines=transcript,
            output_dir=session_dir,
            meeting_name=title,
            overwrite=True,
        )

        if spec["degraded"]:
            err_file = session_dir / "summarize.error"
            err_file.write_text(
                "stage: summarize\n"
                "error: Connection refused — Ollama was not running at http://localhost:11434\n"
            )

    # Error-only session (no meeting.md — appears under "Failed (no note)")
    e = _ERROR_ONLY_SESSION
    err_dt = _build_dt(e["days_ago"], e["hour"], e["minute"])
    error_session_dir = target / week_folder(err_dt) / _session_folder_name(err_dt)
    error_session_dir.mkdir(parents=True, exist_ok=True)
    (error_session_dir / f"{e['error_stage']}.error").write_text(
        f"stage: {e['error_stage']}\n"
        "error: whisper-cli returned non-zero exit code 1\n"
    )

    print(f"Demo vault written to: {target}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a demo vault with fictional meetings for documentation screenshots."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=str(Path.home() / "Documents" / "MeetingRecorderDemo"),
        help="Directory to write demo data into (default: ~/Documents/MeetingRecorderDemo)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite target even if it already contains files.",
    )
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().resolve()

    # Safety check: refuse to run against non-empty directories without --force
    if _is_real_data(target) and not args.force:
        print(
            f"ERROR: '{target}' already contains files.\n"
            "Re-run with --force to overwrite, or choose an empty/absent directory.\n"
            "This guard exists so you cannot accidentally splat over your real vault.",
            file=sys.stderr,
        )
        return 1

    target.mkdir(parents=True, exist_ok=True)
    generate_demo_vault(target)
    _print_usage_instructions(target)
    return 0


def _print_usage_instructions(target: Path) -> None:
    config_path = "~/Library/Application Support/MeetingRecorder/config.toml"
    print(
        f"""
================================================================================
Demo vault ready at:
  {target}

HOW TO USE IT FOR SCREENSHOTS
──────────────────────────────
1. Note your real output_dir FIRST so you can restore it later:
     cat "{config_path}"
   Look for the `output_dir` line and copy its value somewhere safe.

2. Switch the app to the demo vault using ONE of these methods:

   Option A — Settings UI (recommended):
     * Click the menu bar icon -> Settings... -> Output Folder -> Browse...
     * Navigate to and select: {target}
     * Click Save.

   Option B — Edit config directly (app must not be running):
     Edit `{config_path}`, set:
       output_dir = "{target}"
     Then re-launch the app.

3. Click the menu bar icon and take your screenshots.
   Use Cmd-Shift-4, Space, then click a menu/window for retina+shadow PNGs.

4. RESTORE your real vault when done:
     * Settings UI: repeat step 2 with your original output_dir path, OR
     * Config file: restore the original output_dir value and re-launch.

5. Delete the demo vault when you no longer need it:
     rm -rf "{target}"

Real config path: {config_path}
================================================================================
"""
    )


if __name__ == "__main__":
    sys.exit(main())
