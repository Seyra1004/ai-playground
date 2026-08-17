"""report.translation_claude_cli.ClaudeCLITranslationProvider: subprocess
construction, plain-text translation parsing, and failure-mode mapping
onto report.translation's provider contract. subprocess.run is mocked
throughout -- no real `claude` CLI invocation in this test file."""

import json
from unittest.mock import patch

import pytest

from report.llm_claude_cli import ClaudeCLIError
from report.translation import TransientTranslationError, TranslationUnavailableError
from report.translation_claude_cli import ClaudeCLITranslationProvider


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _success_stdout(result_text):
    return json.dumps({"is_error": False, "result": result_text, "usage": {}})


@pytest.fixture
def provider():
    return ClaudeCLITranslationProvider(model="claude-sonnet-5", executable="claude", timeout_seconds=30)


def test_is_configured_true_when_executable_resolves(provider):
    assert provider.is_configured() is True  # executable explicitly given in the fixture


def test_is_configured_false_when_cli_not_on_path(monkeypatch):
    monkeypatch.setattr(
        "report.translation_claude_cli._resolve_executable",
        lambda: (_ for _ in ()).throw(ClaudeCLIError("not found")),
    )
    provider_no_override = ClaudeCLITranslationProvider()
    assert provider_no_override.is_configured() is False


def test_english_headline_returns_korean_translation(provider):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=_success_stdout("한국어 번역 제목"))):
        result = provider.translate("Original English Headline", "ko")
    assert result == "한국어 번역 제목"


def test_strips_accidental_quotes_and_fences(provider):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=_success_stdout('```\n"번역된 제목"\n```'))):
        result = provider.translate("Some Headline", "ko")
    assert result == "번역된 제목"


def test_empty_result_raises_runtime_error_not_crash(provider):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=_success_stdout("   "))):
        with pytest.raises(RuntimeError):
            provider.translate("Some Headline", "ko")


def test_malformed_stdout_raises_runtime_error(provider):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout="not json at all")):
        with pytest.raises(RuntimeError):
            provider.translate("Some Headline", "ko")


def test_nonzero_exit_raises_runtime_error(provider):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(1, stdout="", stderr="boom")):
        with pytest.raises(RuntimeError):
            provider.translate("Some Headline", "ko")


def test_rate_limit_exit_raises_transient_error(provider):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(
        1, stdout="", stderr="Error: usage_limit exceeded, please upgrade your plan",
    )):
        with pytest.raises(TransientTranslationError):
            provider.translate("Some Headline", "ko")


def test_timeout_raises_transient_error(provider):
    import subprocess as subprocess_module
    with patch("subprocess.run", side_effect=subprocess_module.TimeoutExpired(cmd="claude", timeout=30)):
        with pytest.raises(TransientTranslationError):
            provider.translate("Some Headline", "ko")


def test_executable_not_found_raises_translation_unavailable(provider):
    with patch("subprocess.run", side_effect=OSError("not found")):
        with pytest.raises(TranslationUnavailableError):
            provider.translate("Some Headline", "ko")


def test_never_calls_anthropic_api_key_environment(monkeypatch, provider):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeCompletedProcess(0, stdout=_success_stdout("번역"))

    with patch("subprocess.run", side_effect=_fake_run):
        provider.translate("Some Headline", "ko")
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_only_target_lang_ko_supported(provider):
    with pytest.raises(ValueError):
        provider.translate("Some Headline", "ja")


# ---- FIX ONLY: LAST ENGLISH HEADLINE pass (2026-08-18) -- one
# constrained retry when the first translation fails fact validation ----


def test_failed_validation_retries_once_and_recovers():
    """First attempt drops the year (2026 -> missing) and fails
    validate_translation_facts; the retry attempt preserves it and
    passes -- the recovered, valid translation is used."""
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return _FakeCompletedProcess(0, stdout=_success_stdout("어떤 회사가 발표했다"))  # drops 2026
        return _FakeCompletedProcess(0, stdout=_success_stdout("어떤 회사가 2026년에 발표했다"))

    provider = ClaudeCLITranslationProvider(model="claude-sonnet-5", executable="claude", timeout_seconds=30)
    with patch("subprocess.run", side_effect=_fake_run):
        result = provider.translate("Some Company Announced It In 2026", "ko")

    assert result == "어떤 회사가 2026년에 발표했다"
    assert len(calls) == 2
    # The retry's own --system-prompt carries the strict factual-preservation instruction.
    second_system_prompt = calls[1][calls[1].index("--system-prompt") + 1]
    assert "factual preservation" in second_system_prompt


def test_failed_validation_on_both_attempts_returns_original_invalid_text_unchanged():
    """Neither attempt preserves the year -- report.translation.
    translate_and_cache's own existing validator (unchanged, never
    weakened) is what actually rejects this; this provider must NEVER
    fabricate a passing translation, so it returns the first attempt's
    text exactly, and exactly ONE retry (not more) was made."""
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(0, stdout=_success_stdout("어떤 회사가 발표했다"))  # never has 2026

    provider = ClaudeCLITranslationProvider(model="claude-sonnet-5", executable="claude", timeout_seconds=30)
    with patch("subprocess.run", side_effect=_fake_run):
        result = provider.translate("Some Company Announced It In 2026", "ko")

    assert result == "어떤 회사가 발표했다"
    assert len(calls) == 2  # exactly one retry, never more


def test_translation_that_passes_first_attempt_never_retries():
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(0, stdout=_success_stdout("어떤 회사가 2026년에 발표했다"))

    provider = ClaudeCLITranslationProvider(model="claude-sonnet-5", executable="claude", timeout_seconds=30)
    with patch("subprocess.run", side_effect=_fake_run):
        result = provider.translate("Some Company Announced It In 2026", "ko")

    assert result == "어떤 회사가 2026년에 발표했다"
    assert len(calls) == 1
