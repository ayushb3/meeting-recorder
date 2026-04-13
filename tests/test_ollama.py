import pytest
from unittest.mock import patch, MagicMock


SAMPLE_OLLAMA_RESPONSE = """\
## TL;DR
- We agreed to ship the feature by Friday.
- John will follow up on API access.

## Topics Covered
- Sprint planning
- API access issue

## Key Decisions
- Ship by Friday

## Action Items
- John — follow up on API access by EOW
"""


def test_summarize_returns_text():
    from summarizer.ollama import summarize

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": SAMPLE_OLLAMA_RESPONSE}

    with patch("summarizer.ollama.requests.post", return_value=mock_response):
        result = summarize(
            transcript_lines=["[00:00] Hello.", "[00:01] Let's discuss the sprint."],
            model="llama3.2",
            host="http://localhost:11434",
        )

    assert "TL;DR" in result
    assert "Action Items" in result


def test_summarize_raises_on_connection_error():
    from summarizer.ollama import OllamaUnavailableError, summarize
    import requests

    with patch("summarizer.ollama.requests.post", side_effect=requests.ConnectionError):
        with pytest.raises(OllamaUnavailableError):
            summarize(["[00:00] Hello."], "llama3.2", "http://localhost:11434")
