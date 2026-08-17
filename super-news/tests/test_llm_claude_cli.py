"""report.llm_claude_cli.ClaudeCLIStructuredLLM: subprocess construction,
ANTHROPIC_API_KEY stripped from the child environment, structured-response
parsing, and every failure mode (malformed output, timeout, nonzero exit,
rate-limit/quota). subprocess.run is mocked throughout -- no real `claude`
CLI invocation in this test file."""

import json
import subprocess
from unittest.mock import patch

import pytest

from report.llm_claude_cli import (
    ClaudeCLIError,
    ClaudeCLIRateLimitError,
    ClaudeCLIStructuredLLM,
)

_SCHEMA = {
    "type": "object",
    "properties": {"greeting": {"type": "string"}},
    "required": ["greeting"],
}


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _success_stdout(structured_output, input_tokens=10, output_tokens=5):
    return json.dumps({
        "is_error": False,
        "structured_output": structured_output,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    })


@pytest.fixture
def llm():
    return ClaudeCLIStructuredLLM(model="claude-sonnet-5", executable="claude", timeout_seconds=30)


# ---- environment / subprocess construction --------------------------------


def test_api_key_stripped_from_child_environment(monkeypatch, llm):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        captured["shell"] = kwargs.get("shell")
        captured["cmd"] = cmd
        return _FakeCompletedProcess(0, stdout=_success_stdout({"greeting": "hi"}))

    with patch("subprocess.run", side_effect=_fake_run):
        llm.generate_structured("sys", "user prompt", _SCHEMA)

    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert captured["shell"] is False
    assert captured["cmd"][0] == "claude"


def test_json_schema_passed_as_explicit_arg_no_shell_injection_risk(llm):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompletedProcess(0, stdout=_success_stdout({"greeting": "hi"}))

    with patch("subprocess.run", side_effect=_fake_run):
        llm.generate_structured("sys", "user; rm -rf /", _SCHEMA)

    cmd = captured["cmd"]
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--json-schema" in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == _SCHEMA


# ---- stdin prompt transport (never argv) -----------------------------------


def test_prompt_passed_via_stdin_not_argv(llm):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return _FakeCompletedProcess(0, stdout=_success_stdout({"greeting": "hi"}))

    with patch("subprocess.run", side_effect=_fake_run):
        llm.generate_structured("sys", "user; rm -rf /", _SCHEMA)

    assert captured["input"] == "user; rm -rf /"
    assert "user; rm -rf /" not in captured["cmd"]
    assert "-p" in captured["cmd"]


def test_large_production_size_prompt_passed_via_stdin_not_argv(llm):
    large_prompt = "x" * 60000  # exceeds Windows CreateProcess argv limit (~32,767 chars)
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return _FakeCompletedProcess(0, stdout=_success_stdout({"greeting": "hi"}))

    with patch("subprocess.run", side_effect=_fake_run):
        llm.generate_structured("sys", large_prompt, _SCHEMA)

    assert captured["input"] == large_prompt
    assert not any(large_prompt in (arg or "") for arg in captured["cmd"])
    total_argv_chars = sum(len(arg) for arg in captured["cmd"])
    assert total_argv_chars < 5000


# ---- successful structured response ----------------------------------------


def test_successful_structured_response(llm):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(
        0, stdout=_success_stdout({"greeting": "hello"}, input_tokens=100, output_tokens=20)
    )):
        response = llm.generate_structured("sys", "user", _SCHEMA)

    assert response.parsed == {"greeting": "hello"}
    assert response.model_used == "claude-sonnet-5"
    assert response.input_tokens == 100
    assert response.output_tokens == 20


def test_falls_back_to_parsing_result_text_when_structured_output_missing(llm):
    stdout = json.dumps({"is_error": False, "result": '{"greeting": "from result"}', "usage": {}})
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=stdout)):
        response = llm.generate_structured("sys", "user", _SCHEMA)

    assert response.parsed == {"greeting": "from result"}


# ---- failure modes -----------------------------------------------------


def test_malformed_json_stdout_raises_claude_cli_error(llm):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout="not json at all")):
        with pytest.raises(ClaudeCLIError):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_neither_structured_output_nor_valid_result_text_raises(llm):
    stdout = json.dumps({"is_error": False, "result": "not valid json", "usage": {}})
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=stdout)):
        with pytest.raises(ClaudeCLIError):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_timeout_raises_claude_cli_error(llm):
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30)):
        with pytest.raises(ClaudeCLIError, match="timed out"):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_nonzero_exit_raises_claude_cli_error(llm):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(1, stdout="", stderr="boom")):
        with pytest.raises(ClaudeCLIError):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_is_error_true_in_json_payload_raises_even_with_exit_zero(llm):
    stdout = json.dumps({"is_error": True, "result": "There's an issue with the selected model"})
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=stdout)):
        with pytest.raises(ClaudeCLIError):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_executable_not_found_raises_before_any_subprocess_call(monkeypatch):
    monkeypatch.setattr("report.llm_claude_cli.shutil.which", lambda name: None)
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)

    with pytest.raises(ClaudeCLIError, match="not found"):
        ClaudeCLIStructuredLLM()


# ---- rate-limit / quota exhaustion classification --------------------------


def test_rate_limit_stderr_on_nonzero_exit_raises_rate_limit_subclass(llm):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(
        1, stdout="", stderr="Error: usage_limit exceeded, please upgrade your plan"
    )):
        with pytest.raises(ClaudeCLIRateLimitError):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_rate_limit_message_in_is_error_payload_raises_rate_limit_subclass(llm):
    stdout = json.dumps({"is_error": True, "result": "429 rate_limit_error: too many requests"})
    with patch("subprocess.run", return_value=_FakeCompletedProcess(0, stdout=stdout)):
        with pytest.raises(ClaudeCLIRateLimitError):
            llm.generate_structured("sys", "user", _SCHEMA)


def test_generic_failure_is_not_misclassified_as_rate_limit(llm):
    with patch("subprocess.run", return_value=_FakeCompletedProcess(1, stdout="", stderr="unrecognized_model")):
        with pytest.raises(ClaudeCLIError) as exc_info:
            llm.generate_structured("sys", "user", _SCHEMA)
    assert not isinstance(exc_info.value, ClaudeCLIRateLimitError)


# ---- never falls back to a paid API -----------------------------------


def test_no_anthropic_sdk_import_anywhere_in_this_module():
    # Checks actual import statements, not prose -- the module docstring
    # legitimately mentions "api.anthropic.com"/"anthropic" to explain what
    # this module deliberately does NOT do.
    import report.llm_claude_cli as module
    import inspect

    real_import_lines = [
        line.strip() for line in inspect.getsource(module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("anthropic" in line.lower() for line in real_import_lines)
