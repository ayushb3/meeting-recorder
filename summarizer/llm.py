"""Summarize a transcript with whichever LLM the config names.

Three backends share one prompt:

- ``ollama``    — a local Ollama server (the original, offline default)
- ``openai``    — any OpenAI-compatible Chat Completions API
- ``anthropic`` — the Anthropic Messages API

The two hosted backends take a ``base_url`` that already includes the version
segment (``https://api.openai.com/v1``, or a proxy such as
``http://localhost:6655/openai/v1``), so one setting covers the vendor API and
any gateway that fronts it.

The API key is never stored in config.toml. It comes from the
``MEETING_RECORDER_LLM_KEY`` environment variable, else from the macOS Keychain
(service ``MeetingRecorder``, account ``llm-api-key``). A menu-bar app launched
from Finder does not inherit a shell environment, so the Keychain is the path
the app itself uses; the variable is for scripts and tests.

When a hosted backend fails and ``fallback_to_ollama`` is set, the summary is
retried against Ollama, so being off VPN costs summary quality rather than the
summary.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass

import requests

from summarizer.ollama import (
    CONTEXT_BLOCK,
    PROMPT_TEMPLATE,
    TITLE_PROMPT,
    SummaryUnavailableError,
)
from summarizer.ollama import suggest_title as _ollama_title
from summarizer.ollama import summarize as _ollama_summarize

log = logging.getLogger(__name__)

PROVIDERS = ("ollama", "openai", "anthropic")
KEYCHAIN_SERVICE = "MeetingRecorder"
KEYCHAIN_ACCOUNT = "llm-api-key"
KEY_ENV_VAR = "MEETING_RECORDER_LLM_KEY"
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class LLMSettings:
    """Everything needed to produce a summary, independent of Config."""

    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    ollama_model: str = ""
    ollama_host: str = "http://localhost:11434"
    prompt: str | None = None
    terms: str | None = None
    fallback_to_ollama: bool = True

    @classmethod
    def from_config(cls, cfg) -> "LLMSettings":
        return cls(
            provider=cfg.llm_provider,
            model=cfg.llm_model,
            base_url=cfg.llm_base_url,
            ollama_model=cfg.ollama_model,
            ollama_host=cfg.ollama_host,
            prompt=cfg.ollama_prompt,
            terms=cfg.llm_terms,
            fallback_to_ollama=cfg.llm_fallback_to_ollama,
        )

    @property
    def hosted(self) -> bool:
        return self.provider in ("openai", "anthropic")

    @property
    def label(self) -> str:
        """Short description, e.g. 'gpt-5.6-luna via openai'."""
        if self.hosted:
            return f"{self.model} via {self.provider}"
        return f"{self.ollama_model} via ollama"


def api_key() -> str | None:
    """The hosted-backend API key from the environment or the Keychain, or None."""
    key = os.environ.get(KEY_ENV_VAR, "").strip()
    if key:
        return key
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def merge_context(terms: str | None, context: str | None) -> str | None:
    """Standing terms plus this meeting's context, as one context string."""
    parts = []
    if terms and terms.strip():
        parts.append(f"Names and terms that may appear (use these spellings): {terms.strip()}")
    if context and context.strip():
        parts.append(context.strip())
    return "\n".join(parts) or None


def build_prompt(lines: list[str], context: str | None, custom_template: str | None) -> str:
    """The same prompt Ollama gets — see summarizer.ollama.summarize."""
    transcript = "\n".join(lines)
    if custom_template is not None:
        return custom_template.replace("{transcript}", transcript).replace(
            "{context}", context or ""
        )
    block = CONTEXT_BLOCK.format(context=context) if context else ""
    return PROMPT_TEMPLATE.format(transcript=transcript, context_block=block)


def complete(prompt: str, settings: LLMSettings, timeout: float = 180) -> str:
    """One prompt in, text out, from the hosted backend in *settings*.

    Raises SummaryUnavailableError on anything that is not a usable answer.
    """
    key = api_key()
    if not key:
        raise SummaryUnavailableError(
            f"No API key for {settings.provider}. Store one with: "
            f"security add-generic-password -s {KEYCHAIN_SERVICE} "
            f"-a {KEYCHAIN_ACCOUNT} -w"
        )
    base = settings.base_url.rstrip("/")
    if settings.provider == "anthropic":
        url = f"{base}/messages"
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        body = {
            "model": settings.model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        body = {"model": settings.model, "messages": [{"role": "user", "content": prompt}]}

    try:
        response = requests.post(url, json=body, headers=headers, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise SummaryUnavailableError(f"Cannot reach {base}") from exc
    if response.status_code != 200:
        # The body goes to the log only: this message lands in the note and the
        # error marker, and a gateway may echo request headers — the key — back.
        log.warning("%s %s: %s", url, response.status_code, response.text[:500].replace(key, "***"))
        raise SummaryUnavailableError(
            f"{settings.provider} returned HTTP {response.status_code} for {settings.model}"
        )
    try:
        data = response.json()
        if settings.provider == "anthropic":
            text = "".join(
                block.get("text", "") for block in data["content"] if block.get("type") == "text"
            )
        else:
            text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SummaryUnavailableError(f"Unexpected response shape from {url}") from exc
    if not text or not text.strip():
        raise SummaryUnavailableError(f"{settings.model} returned an empty answer")
    return text


def summarize(
    transcript_lines: list[str],
    settings: LLMSettings,
    context: str | None = None,
) -> str:
    """Summarize with the configured backend, falling back to Ollama if allowed.

    Raises SummaryUnavailableError when no backend produced a summary.
    """
    context = merge_context(settings.terms, context)
    if not settings.hosted:
        return _ollama_summarize(
            transcript_lines, settings.ollama_model, settings.ollama_host,
            context=context, custom_template=settings.prompt,
        )
    try:
        prompt = build_prompt(transcript_lines, context, settings.prompt)
        return complete(prompt, settings)
    except SummaryUnavailableError as exc:
        if not (settings.fallback_to_ollama and settings.ollama_model):
            raise
        log.warning("%s failed (%s) — falling back to Ollama", settings.label, exc)
        try:
            return _ollama_summarize(
                transcript_lines, settings.ollama_model, settings.ollama_host,
                context=context, custom_template=settings.prompt,
            )
        except SummaryUnavailableError as fallback_exc:
            raise SummaryUnavailableError(
                f"{exc}; Ollama fallback also failed: {fallback_exc}"
            ) from fallback_exc


def suggest_title(summary: str, settings: LLMSettings) -> str | None:
    """A short meeting title from the summary, or None on any failure."""
    if not settings.hosted:
        return _ollama_title(summary, settings.ollama_model, settings.ollama_host)
    try:
        text = complete(TITLE_PROMPT.format(summary=summary[:2000]), settings, timeout=30)
    except SummaryUnavailableError:
        if settings.fallback_to_ollama and settings.ollama_model:
            return _ollama_title(summary, settings.ollama_model, settings.ollama_host)
        return None
    title = text.strip().splitlines()[0].strip()
    title = re.sub(r'^["\'#*\s]+|["\'*\s]+$', "", title)
    return title or None


def list_models(provider: str, base_url: str, timeout: float = 5) -> list[str]:
    """Model ids the endpoint advertises, sorted; [] when it cannot be asked."""
    key = api_key()
    if provider not in ("openai", "anthropic") or not base_url or not key:
        return []
    headers = (
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        if provider == "anthropic"
        else {"Authorization": f"Bearer {key}"}
    )
    try:
        response = requests.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
        response.raise_for_status()
        ids = [m["id"] for m in response.json().get("data", []) if m.get("id")]
    except Exception:
        return []
    return sorted(i for i in ids if "embedding" not in i)


@dataclass(frozen=True)
class LLMStatus:
    """Result of a cheap liveness probe, for the menu."""

    ready: bool
    label: str
    detail: str


def check_status(settings: LLMSettings) -> LLMStatus:
    """Is the configured summarizer usable right now?"""
    if not settings.hosted:
        from summarizer.ollama import check_status as ollama_status  # noqa: PLC0415

        s = ollama_status(settings.ollama_model, settings.ollama_host)
        return LLMStatus(ready=s.ready, label=s.label, detail=s.detail)
    if not api_key():
        return LLMStatus(False, f"LLM: no API key ({settings.model})",
                         f"Store a key in the Keychain under service {KEYCHAIN_SERVICE}, "
                         f"account {KEYCHAIN_ACCOUNT}.")
    models = list_models(settings.provider, settings.base_url)
    if not models:
        fallback = " — Ollama fallback on" if settings.fallback_to_ollama else ""
        return LLMStatus(False, f"LLM: {settings.base_url} unreachable{fallback}",
                         f"Could not list models at {settings.base_url}.")
    if settings.model not in models:
        return LLMStatus(False, f"LLM: {settings.model} not offered",
                         f"{settings.base_url} does not list {settings.model}.")
    return LLMStatus(True, f"LLM: ready ({settings.model})",
                     f"{settings.model} is available at {settings.base_url}")
