"""Tests for summarizer.llm — backend routing, fallback, key lookup."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from summarizer.llm import (
    LLMSettings,
    SummaryUnavailableError,
    api_key,
    build_prompt,
    check_status,
    complete,
    list_models,
    merge_context,
    suggest_title,
    summarize,
)
from summarizer.ollama import OllamaUnavailableError

OPENAI = LLMSettings(
    provider="openai", model="gpt-5.6-luna", base_url="http://proxy/openai/v1/",
    ollama_model="gemma", ollama_host="http://localhost:11434",
)
ANTHROPIC = LLMSettings(
    provider="anthropic", model="claude-x", base_url="http://proxy/anthropic/v1",
    ollama_model="gemma",
)


def _response(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = text
    return r


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("MEETING_RECORDER_LLM_KEY", "sk-test")


# ---------------------------------------------------------------- api_key


def test_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv("MEETING_RECORDER_LLM_KEY", "  from-env ")
    with patch("summarizer.llm.subprocess.run") as run:
        assert api_key() == "from-env"
    run.assert_not_called()


def test_api_key_reads_keychain(monkeypatch):
    monkeypatch.delenv("MEETING_RECORDER_LLM_KEY", raising=False)
    done = MagicMock(returncode=0, stdout="from-keychain\n")
    with patch("summarizer.llm.subprocess.run", return_value=done) as run:
        assert api_key() == "from-keychain"
    args = run.call_args.args[0]
    assert args[:2] == ["security", "find-generic-password"]
    assert "MeetingRecorder" in args and "llm-api-key" in args


def test_api_key_none_when_keychain_has_no_entry(monkeypatch):
    monkeypatch.delenv("MEETING_RECORDER_LLM_KEY", raising=False)
    with patch("summarizer.llm.subprocess.run", return_value=MagicMock(returncode=44, stdout="")):
        assert api_key() is None


# ---------------------------------------------------------------- prompt


def test_merge_context_puts_terms_first():
    merged = merge_context("Langfuse, SDD", "Attendees: Ana, Bo")
    assert merged.index("Langfuse") < merged.index("Attendees")


def test_merge_context_none_when_both_blank():
    assert merge_context("  ", None) is None


def test_build_prompt_custom_template_keeps_literal_braces():
    prompt = build_prompt(["[00:01] hi"], None, 'JSON {"a": 1}\n{transcript}\n{context}')
    assert '{"a": 1}' in prompt and "[00:01] hi" in prompt and "{context}" not in prompt


# ---------------------------------------------------------------- complete


def test_complete_openai_shape(key):
    ok = _response(payload={"choices": [{"message": {"content": "## TL;DR\nok"}}]})
    with patch("summarizer.llm.requests.post", return_value=ok) as post:
        assert complete("p", OPENAI) == "## TL;DR\nok"
    assert post.call_args.args[0] == "http://proxy/openai/v1/chat/completions"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer sk-test"}
    assert post.call_args.kwargs["json"]["model"] == "gpt-5.6-luna"


def test_complete_anthropic_shape(key):
    ok = _response(payload={"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]})
    with patch("summarizer.llm.requests.post", return_value=ok) as post:
        assert complete("p", ANTHROPIC) == "ab"
    assert post.call_args.args[0] == "http://proxy/anthropic/v1/messages"
    assert post.call_args.kwargs["headers"]["x-api-key"] == "sk-test"
    assert post.call_args.kwargs["json"]["max_tokens"] > 0


def test_complete_without_key_names_the_keychain_command(monkeypatch):
    monkeypatch.delenv("MEETING_RECORDER_LLM_KEY", raising=False)
    with patch("summarizer.llm.subprocess.run", return_value=MagicMock(returncode=44, stdout="")):
        with pytest.raises(SummaryUnavailableError, match="security add-generic-password"):
            complete("p", OPENAI)


@pytest.mark.parametrize("response", [
    _response(status=400, text="bad model"),
    _response(payload={"choices": []}),
    _response(payload={"choices": [{"message": {"content": "   "}}]}),
])
def test_complete_rejects_unusable_answers(key, response):
    with patch("summarizer.llm.requests.post", return_value=response):
        with pytest.raises(SummaryUnavailableError):
            complete("p", OPENAI)


def test_complete_connection_error(key):
    with patch("summarizer.llm.requests.post", side_effect=requests.ConnectionError):
        with pytest.raises(SummaryUnavailableError, match="Cannot reach"):
            complete("p", OPENAI)


# ---------------------------------------------------------------- summarize


def test_summarize_ollama_provider_goes_straight_to_ollama():
    settings = LLMSettings(ollama_model="gemma", terms="Langfuse")
    with patch("summarizer.llm._ollama_summarize", return_value="S") as ollama, \
         patch("summarizer.llm.complete") as hosted:
        assert summarize(["x"], settings, context="Attendees") == "S"
    hosted.assert_not_called()
    assert "Langfuse" in ollama.call_args.kwargs["context"]
    assert "Attendees" in ollama.call_args.kwargs["context"]


def test_summarize_hosted_success_does_not_touch_ollama(key):
    with patch("summarizer.llm.complete", return_value="S") as hosted, \
         patch("summarizer.llm._ollama_summarize") as ollama:
        assert summarize(["x"], OPENAI) == "S"
    hosted.assert_called_once()
    ollama.assert_not_called()


def test_summarize_falls_back_to_ollama():
    with patch("summarizer.llm.complete", side_effect=SummaryUnavailableError("off VPN")), \
         patch("summarizer.llm._ollama_summarize", return_value="local") as ollama:
        assert summarize(["x"], OPENAI) == "local"
    assert ollama.call_args.args[1:3] == ("gemma", "http://localhost:11434")


def test_summarize_no_fallback_when_disabled():
    settings = LLMSettings(**{**OPENAI.__dict__, "fallback_to_ollama": False})
    with patch("summarizer.llm.complete", side_effect=SummaryUnavailableError("off VPN")), \
         patch("summarizer.llm._ollama_summarize") as ollama:
        with pytest.raises(SummaryUnavailableError, match="off VPN"):
            summarize(["x"], settings)
    ollama.assert_not_called()


def test_summarize_reports_both_failures():
    with patch("summarizer.llm.complete", side_effect=SummaryUnavailableError("off VPN")), \
         patch("summarizer.llm._ollama_summarize", side_effect=OllamaUnavailableError("not running")):
        with pytest.raises(SummaryUnavailableError, match="off VPN.*not running"):
            summarize(["x"], OPENAI)


def test_ollama_error_is_a_summary_error():
    """processor catches SummaryUnavailableError; Ollama failures must still be caught."""
    assert issubclass(OllamaUnavailableError, SummaryUnavailableError)


# ---------------------------------------------------------------- title


def test_suggest_title_strips_markdown_and_quotes(key):
    with patch("summarizer.llm.complete", return_value='**"Eval Pipeline Sync"**\nextra'):
        assert suggest_title("summary", OPENAI) == "Eval Pipeline Sync"


def test_suggest_title_falls_back_then_gives_up_quietly():
    with patch("summarizer.llm.complete", side_effect=SummaryUnavailableError("x")), \
         patch("summarizer.llm._ollama_title", return_value=None) as ollama:
        assert suggest_title("summary", OPENAI) is None
    ollama.assert_called_once()


# ---------------------------------------------------------------- models / status


def test_list_models_drops_embeddings_and_sorts(key):
    payload = {"data": [{"id": "gpt-5"}, {"id": "text-embedding-3-small"}, {"id": "gpt-4.1"}]}
    ok = _response(payload=payload)
    ok.raise_for_status = MagicMock()
    with patch("summarizer.llm.requests.get", return_value=ok) as get:
        assert list_models("openai", "http://proxy/openai/v1") == ["gpt-4.1", "gpt-5"]
    assert get.call_args.args[0] == "http://proxy/openai/v1/models"


def test_list_models_empty_on_failure(key):
    with patch("summarizer.llm.requests.get", side_effect=requests.ConnectionError):
        assert list_models("openai", "http://proxy/openai/v1") == []


def test_list_models_empty_for_ollama(key):
    assert list_models("ollama", "http://x") == []


def test_check_status_ready(key):
    with patch("summarizer.llm.list_models", return_value=["gpt-5.6-luna"]):
        status = check_status(OPENAI)
    assert status.ready and "gpt-5.6-luna" in status.label


def test_check_status_model_not_offered(key):
    with patch("summarizer.llm.list_models", return_value=["gpt-5"]):
        assert not check_status(OPENAI).ready


def test_check_status_unreachable_mentions_fallback(key):
    with patch("summarizer.llm.list_models", return_value=[]):
        status = check_status(OPENAI)
    assert not status.ready and "fallback" in status.label.lower()
