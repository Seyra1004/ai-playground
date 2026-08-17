"""Claude Code CLI (subscription-authenticated) implementation of
report.llm_interface.StructuredLLM -- an alternative to llm_anthropic.py's
direct Anthropic Python SDK / ANTHROPIC_API_KEY path. Selected via
LLM_PROVIDER=claude_cli (see report/llm_interface.py's build_llm()).

Shells out to the ALREADY-authenticated `claude` CLI (Claude Code) in
non-interactive print mode (`claude -p ... --output-format json
--json-schema <schema> --tools "" --no-session-persistence`), using
whatever OAuth/keychain session the machine's own `claude auth login`
already established. Never reads or requires ANTHROPIC_API_KEY (explicitly
stripped from the child process environment below), never imports/calls
the `anthropic` SDK, never calls api.anthropic.com directly via this
module, and never falls back to llm_anthropic.py on any failure -- a CLI
failure/timeout/malformed-output/rate-limit here raises ClaudeCLIError (or
the ClaudeCLIRateLimitError subclass), which every orchestrator that calls
build_llm() already treats like any other StructuredLLM failure (broad
except Exception -> REPORT_FAILED / no-evidence-day, never a crash, never
a silent retry against a different provider).

Verified manually before this was written (see SUPER_NEWS_CURRENT_STATE.md
for the full record): `claude auth status` reports
authMethod="claude.ai"/subscriptionType="pro" on this machine, and repeated
`claude -p ... --json-schema ... --tools ""` probes succeeded with
ANTHROPIC_API_KEY explicitly stripped from the child process environment,
producing a top-level `structured_output` field matching the requested
JSON Schema.

--tools "" disables every built-in tool (Bash/Edit/Read/...) for the
child session -- text generation only, so no file/bash/edit permission
prompt can ever occur, and no code-writing capability is ever granted.
--no-session-persistence keeps this from accumulating on-disk session
state for what is, from this codebase's point of view, a stateless
one-shot call. --system-prompt REPLACES the CLI's own default system
prompt (which is where CLAUDE.md auto-discovery/project context normally
gets folded in for an interactive session) -- so a synthesis call here
never leaks unrelated repo instructions into the prompt, matching
llm_anthropic.py's own `system=system_prompt` semantics exactly.
"""

import json
import logging
import os
import shutil
import subprocess

from config import get_optional_env
from report.llm_interface import LLMResponse, StructuredLLM

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
_DEFAULT_TIMEOUT_SECONDS = 180

# Best-effort classification only, for clearer logging -- NOT a retry
# trigger and NOT a fallback trigger. A rate-limit/quota failure is still
# just a ClaudeCLIError to every caller (isinstance ClaudeCLIRateLimitError
# lets a caller special-case it later if that's ever useful, but no
# orchestrator in this codebase does today).
_RATE_LIMIT_MARKERS = ("rate_limit", "rate limit", "usage_limit", "usage limit", "429", "quota", "overloaded")


class ClaudeCLIError(RuntimeError):
    """The claude CLI subprocess failed, timed out, or returned an
    unusable/malformed result. Never caught inside this module to silently
    retry against a paid API."""


class ClaudeCLIRateLimitError(ClaudeCLIError):
    """The claude CLI's failure looks like a subscription rate-limit/usage
    quota exhaustion rather than a generic error."""


def _resolve_executable():
    """CLAUDE_CLI_PATH lets a caller pin an exact path (e.g. if `claude`
    isn't on this process's PATH, or to pin a specific install); default is
    a safe PATH lookup via shutil.which -- never a shell-interpreted
    string, never string-formatted into a shell command."""
    override = get_optional_env("CLAUDE_CLI_PATH", "")
    if override:
        return override
    resolved = shutil.which("claude")
    if not resolved:
        raise ClaudeCLIError("claude CLI not found on PATH -- install Claude Code or set CLAUDE_CLI_PATH.")
    return resolved


def _looks_like_rate_limit(*texts):
    combined = " ".join(t or "" for t in texts).lower()
    return any(marker in combined for marker in _RATE_LIMIT_MARKERS)


class ClaudeCLIStructuredLLM(StructuredLLM):
    def __init__(self, model=None, executable=None, timeout_seconds=None):
        self._model = model or get_optional_env("LLM_MODEL_CLAUDE_CLI", DEFAULT_MODEL)
        self._executable = executable or _resolve_executable()
        self._timeout_seconds = timeout_seconds or int(
            get_optional_env("CLAUDE_CLI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
        )

    def generate_structured(self, system_prompt, user_prompt, schema):
        # user_prompt is NEVER placed in argv -- for real synthesis calls it
        # can be tens of KB, which exceeds Windows' CreateProcess command-line
        # length limit (~32,767 chars total) and fails near-instantly with no
        # useful error. `-p` with no positional prompt argument makes the
        # Claude CLI print-mode read the prompt from stdin instead, which has
        # no such length ceiling.
        cmd = [
            self._executable,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--tools", "",
            "--no-session-persistence",
            "--model", self._model,
        ]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]

        # Explicit strip, even though the caller (report/llm_interface.py's
        # build_llm()) already refuses to construct a paid AnthropicStructuredLLM
        # when SUPER_NEWS_NO_PAID_API is set -- defense in depth: this
        # provider must never depend on ANTHROPIC_API_KEY even if
        # constructed directly by a test or future caller.
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        logger.info(
            "claude_cli generate_structured STARTING model=%s prompt_chars=%d timeout_s=%d",
            self._model, len(user_prompt), self._timeout_seconds,
        )

        try:
            result = subprocess.run(
                cmd, shell=False, capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=self._timeout_seconds,
                input=user_prompt,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCLIError(f"claude CLI timed out after {self._timeout_seconds}s") from exc
        except OSError as exc:
            raise ClaudeCLIError(f"claude CLI failed to start: {type(exc).__name__}: {exc}") from exc

        stderr_snippet = (result.stderr or "").strip()[:500]

        if result.returncode != 0:
            if _looks_like_rate_limit(result.stdout, result.stderr):
                raise ClaudeCLIRateLimitError(
                    f"claude CLI reported a rate-limit/usage-quota failure (exit={result.returncode}): {stderr_snippet}"
                )
            raise ClaudeCLIError(f"claude CLI exited {result.returncode}: {stderr_snippet}")

        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ClaudeCLIError(
                f"claude CLI produced non-JSON output on stdout: {type(exc).__name__}: {exc}"
            ) from exc

        if payload.get("is_error"):
            message = str(payload.get("result", ""))[:500]
            if _looks_like_rate_limit(message, ""):
                raise ClaudeCLIRateLimitError(f"claude CLI reported is_error=true (rate-limit/quota): {message}")
            raise ClaudeCLIError(f"claude CLI reported is_error=true: {message}")

        parsed = payload.get("structured_output")
        if parsed is None:
            # --json-schema is expected to populate structured_output --
            # fall back to parsing payload["result"] as raw JSON text if it
            # didn't (still a local parse, never a paid-API fallback).
            raw_result_text = payload.get("result")
            if not isinstance(raw_result_text, str):
                raise ClaudeCLIError("claude CLI response had neither structured_output nor a text result field.")
            try:
                parsed = json.loads(raw_result_text)
            except json.JSONDecodeError as exc:
                raise ClaudeCLIError(
                    f"claude CLI result was not valid JSON matching the schema: {type(exc).__name__}: {exc}"
                ) from exc

        usage = payload.get("usage") or {}
        logger.info(
            "claude_cli generate_structured SUCCESS model=%s input_tokens=%s output_tokens=%s",
            self._model, usage.get("input_tokens"), usage.get("output_tokens"),
        )

        return LLMResponse(
            parsed=parsed,
            raw_text=json.dumps(parsed),
            model_used=self._model,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
        )
