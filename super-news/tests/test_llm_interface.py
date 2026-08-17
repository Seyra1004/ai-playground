"""report.llm_interface.build_llm(): provider selection (anthropic vs
claude_cli) and the SUPER_NEWS_NO_PAID_API refusal guard. No real network/
CLI/API calls -- report.llm_anthropic/report.llm_claude_cli's own
constructors are monkeypatched out."""

import pytest

from report.llm_interface import build_llm


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("SUPER_NEWS_NO_PAID_API", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_default_provider_is_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    sentinel = object()
    monkeypatch.setattr("report.llm_anthropic.AnthropicStructuredLLM", lambda: sentinel)

    assert build_llm() is sentinel


def test_claude_cli_provider_selected_explicitly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    sentinel = object()
    monkeypatch.setattr("report.llm_claude_cli.ClaudeCLIStructuredLLM", lambda: sentinel)

    assert build_llm() is sentinel


def test_claude_cli_provider_never_touches_anthropic_module(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")

    def _fail_if_constructed():
        raise AssertionError("AnthropicStructuredLLM must never be constructed when LLM_PROVIDER=claude_cli")

    monkeypatch.setattr("report.llm_anthropic.AnthropicStructuredLLM", _fail_if_constructed)
    monkeypatch.setattr("report.llm_claude_cli.ClaudeCLIStructuredLLM", lambda: object())

    build_llm()  # must not raise


def test_unsupported_provider_raises_value_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "some_other_provider")

    with pytest.raises(ValueError):
        build_llm()


# ---- SUPER_NEWS_NO_PAID_API refusal guard ----------------------------------


def test_no_paid_api_guard_blocks_default_anthropic_provider(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")

    with pytest.raises(RuntimeError, match="SUPER_NEWS_NO_PAID_API"):
        build_llm()


def test_no_paid_api_guard_blocks_explicit_anthropic_provider(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "true")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")

    with pytest.raises(RuntimeError, match="SUPER_NEWS_NO_PAID_API"):
        build_llm()


def test_no_paid_api_guard_does_not_block_claude_cli_provider(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("LLM_PROVIDER", "claude_cli")
    sentinel = object()
    monkeypatch.setattr("report.llm_claude_cli.ClaudeCLIStructuredLLM", lambda: sentinel)

    assert build_llm() is sentinel


def test_no_paid_api_guard_falsy_values_do_not_block_anthropic(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    sentinel = object()
    monkeypatch.setattr("report.llm_anthropic.AnthropicStructuredLLM", lambda: sentinel)

    assert build_llm() is sentinel
