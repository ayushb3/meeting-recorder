import re

import requests


class OllamaUnavailableError(Exception):
    pass


PROMPT_TEMPLATE = """\
Given this meeting transcript, produce:
1. A 2-3 sentence TL;DR
2. Topics covered
3. Key decisions made
4. Action items (person + task + deadline if mentioned)

Use markdown headings (## TL;DR, ## Topics Covered, ## Key Decisions, ## Action Items).
Format action items as: - Person — task by deadline
{context_block}
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
