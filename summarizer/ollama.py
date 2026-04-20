import re
from dataclasses import dataclass

import requests


class OllamaUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class OllamaStatus:
    """Result of a cheap liveness probe against the Ollama server."""

    reachable: bool
    model_present: bool
    model: str
    host: str
    detail: str

    @property
    def ready(self) -> bool:
        """True when the server answers and the configured model is pulled."""
        return self.reachable and self.model_present

    @property
    def label(self) -> str:
        """Short menu-bar label, e.g. 'Ollama: ready (llama3.1:8b)'."""
        if self.ready:
            return f"Ollama: ready ({self.model})"
        if self.reachable:
            return f"Ollama: model {self.model} not pulled"
        return "Ollama: not running"


def check_status(model: str, host: str, timeout: float = 2.0) -> OllamaStatus:
    """Probe Ollama without generating anything.

    Never raises: a probe failure is itself the answer. Summaries are optional,
    so this is only ever used to tell the user what will happen, never to block.
    """
    try:
        response = requests.get(f"{host}/api/tags", timeout=timeout)
        response.raise_for_status()
        models = response.json().get("models", [])
    except (requests.ConnectionError, requests.Timeout):
        return OllamaStatus(
            reachable=False, model_present=False, model=model, host=host,
            detail=f"Cannot reach Ollama at {host}. Start it with: ollama serve",
        )
    except Exception as e:  # malformed JSON, HTTP error, anything else
        return OllamaStatus(
            reachable=False, model_present=False, model=model, host=host,
            detail=f"Ollama at {host} responded but the probe failed: {e}",
        )

    # Ollama reports fully-qualified tags ("llama3.1:8b"); a config that omits
    # the tag should still match the ":latest" the server reports.
    names = {m.get("name", "") for m in models}
    present = (
        model in names
        or f"{model}:latest" in names
        or any(name.split(":")[0] == model for name in names)
    )
    detail = (
        f"{model} is loaded and ready at {host}"
        if present
        else f"Ollama is running at {host} but {model} is not pulled. Run: ollama pull {model}"
    )
    return OllamaStatus(
        reachable=True, model_present=present, model=model, host=host, detail=detail,
    )


PROMPT_TEMPLATE = """\
You are a meeting notes assistant. Analyze the transcript below and produce structured notes.

Rules:
- Be specific and concrete — extract actual decisions, not vague descriptions
- For action items, only list things explicitly assigned or volunteered; do not infer
- If the transcript labels some lines as "(you)", that speaker is the note-taker
- Ignore filler words, repeated lines, and cross-talk artifacts
{context_block}
Produce exactly these sections in order:

## TL;DR
2-3 sentences. What was this meeting actually about and what was resolved?

## Topics Covered
Bullet list of distinct topics discussed.

## Key Decisions
Bullet list of decisions made. If none, write "None recorded."

## Action Items
For each action item: - **Person** — what they will do (deadline if stated)
If no action items were stated, write "None recorded."

## Open Questions
Any unresolved questions or topics explicitly flagged for follow-up.

Transcript:
{transcript}
"""

CONTEXT_BLOCK = """\

Additional context provided by the organizer:
{context}

"""

TITLE_PROMPT = """\
Given this meeting summary, reply with ONLY a short title (3-6 words, title case, no punctuation).
Do not explain. Output the title alone on one line.

Summary:
{summary}
"""


def summarize(transcript_lines: list[str], model: str, host: str, context: str | None = None) -> str:
    """Send transcript to Ollama and return the summary text."""
    transcript = "\n".join(transcript_lines)
    context_block = CONTEXT_BLOCK.format(context=context) if context else ""
    prompt = PROMPT_TEMPLATE.format(transcript=transcript, context_block=context_block)
    try:
        response = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise OllamaUnavailableError(f"Cannot reach Ollama at {host}") from e

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        raise OllamaUnavailableError(f"Ollama returned error {response.status_code}: {response.text[:200]}") from e

    return response.json()["response"]


def suggest_title(summary: str, model: str, host: str) -> str | None:
    """Ask Ollama for a short meeting title based on the summary. Returns None on any failure."""
    prompt = TITLE_PROMPT.format(summary=summary[:2000])
    try:
        response = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        response.raise_for_status()
        title = response.json()["response"].strip().splitlines()[0].strip()
        # Strip surrounding quotes the model sometimes adds
        title = re.sub(r'^["\']|["\']$', "", title).strip()
        return title if title else None
    except Exception:
        return None
