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

Transcript:
{transcript}
"""


def summarize(transcript_lines: list[str], model: str, host: str) -> str:
    """Send transcript to Ollama and return the summary text."""
    transcript = "\n".join(transcript_lines)
    prompt = PROMPT_TEMPLATE.format(transcript=transcript)
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
