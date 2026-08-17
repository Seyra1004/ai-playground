"""Claude Code CLI (subscription-authenticated) translation provider --
selected automatically by report.translation.build_translation_provider()
under SUPER_NEWS_NO_PAID_API=1 when the claude CLI is available, so a
fresh (never-before-cached) English headline can still get a real Korean
ko_title without any paid api.anthropic.com call. Mirrors report.
llm_claude_cli.ClaudeCLIStructuredLLM's own subprocess pattern (same
executable resolution, same env stripped of ANTHROPIC_API_KEY, same
--output-format json envelope) -- reused directly, not duplicated: this
module imports report.llm_claude_cli's own _resolve_executable/
_looks_like_rate_limit/ClaudeCLIError/DEFAULT_MODEL rather than
reimplementing them.

This is a PLAIN-TEXT translation call (one short headline in, one short
headline out) -- deliberately never uses --json-schema/structured_output,
since a translated headline has no schema to validate against beyond
"is it non-empty text."""

import json
import os
import subprocess

from config import get_optional_env
from report.llm_claude_cli import (
    DEFAULT_MODEL,
    ClaudeCLIError,
    _looks_like_rate_limit,
    _resolve_executable,
)
from report.translation import TranslationProvider, TransientTranslationError, TranslationUnavailableError
from report.translation_validation import validate_translation_facts

_DEFAULT_TIMEOUT_SECONDS = 60

_SYSTEM_PROMPT = (
    "Translate this news headline naturally into Korean. Preserve proper nouns, "
    "company names, artist names, product names, numbers and factual meaning. "
    "Do not summarize. Do not add information. Return only the translated headline, "
    "with no surrounding quotes, markdown, or explanation. If the text is already "
    "written in Korean, return it unchanged."
)

# FIX ONLY: LAST ENGLISH HEADLINE pass (2026-08-18, confirmed real case):
# a first translation attempt can fail report.translation_validation.
# validate_translation_facts (e.g. a dropped/altered proper noun or
# number) even though the CLI is working fine -- ONE retry with a
# stricter, more explicit factual-preservation prompt, self-validated
# with the SAME unweakened validator, recovers a real Korean headline in
# that case. If the retry also fails validation, the ORIGINAL (invalid)
# translation is still returned unchanged -- report.translation.
# translate_and_cache's own existing validation gate rejects it exactly
# as before this pass, so a title that genuinely can't be safely
# translated still degrades to the real English original, never a
# fabricated Korean string.
_STRICT_RETRY_SYSTEM_PROMPT = (
    "Translate this headline into Korean with strict factual preservation. "
    "Every proper noun, company name, product name, person name, number, and "
    "factual qualifier must remain equivalent to the original. Do not summarize. "
    "Do not omit facts. Do not add facts. Return only the Korean headline, with no "
    "surrounding quotes, markdown, or explanation."
)
MAX_TRANSLATION_RETRIES = 1


def _strip_wrapping(text):
    """Removes accidental surrounding quotes/markdown fences/whitespace a
    chat-tuned model sometimes adds even when told not to -- never
    rewrites the actual translated content itself."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped.strip("`").strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        stripped = stripped[1:-1].strip()
    return stripped


class ClaudeCLITranslationProvider(TranslationProvider):
    def __init__(self, model=None, executable=None, timeout_seconds=None):
        self._model = model or get_optional_env("LLM_MODEL_CLAUDE_CLI", DEFAULT_MODEL)
        self._executable_override = executable
        self._timeout_seconds = timeout_seconds or _DEFAULT_TIMEOUT_SECONDS

    @property
    def model_name(self):
        return self._model

    def is_configured(self):
        """Deterministic, no network round-trip -- same executable-
        resolution check report.llm_claude_cli.ClaudeCLIStructuredLLM
        itself relies on, just non-raising here."""
        if self._executable_override:
            return True
        try:
            _resolve_executable()
        except ClaudeCLIError:
            return False
        return True

    def translate(self, text, target_lang):
        if target_lang != "ko":
            raise ValueError(f"ClaudeCLITranslationProvider only supports target_lang='ko', got {target_lang!r}")

        translated = self._call_cli(text, _SYSTEM_PROMPT)
        if validate_translation_facts(text, translated).ok:
            return translated
        # ONE constrained retry only (MAX_TRANSLATION_RETRIES=1) -- never a
        # loop. If this also fails validation, the (still invalid) first
        # attempt is returned unchanged so report.translation.
        # translate_and_cache's own existing validator reaches the exact
        # same PERMANENT-failure conclusion it always would have -- this
        # retry can only ever RECOVER a real translation, never suppress a
        # legitimate rejection.
        retried = self._call_cli(text, _STRICT_RETRY_SYSTEM_PROMPT)
        if validate_translation_facts(text, retried).ok:
            return retried
        return translated

    def _call_cli(self, text, system_prompt):
        executable = self._executable_override or _resolve_executable()
        cmd = [
            executable, "-p", "--output-format", "json",
            "--tools", "", "--no-session-persistence", "--model", self._model,
            "--system-prompt", system_prompt,
        ]
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        try:
            result = subprocess.run(
                cmd, shell=False, capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=self._timeout_seconds, input=text,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransientTranslationError(f"claude CLI translation timed out after {self._timeout_seconds}s") from exc
        except OSError as exc:
            raise TranslationUnavailableError(f"claude CLI failed to start: {type(exc).__name__}: {exc}") from exc

        stderr_snippet = (result.stderr or "").strip()[:300]
        if result.returncode != 0:
            if _looks_like_rate_limit(result.stdout, result.stderr):
                raise TransientTranslationError(
                    f"claude CLI reported a rate-limit/usage-quota failure (exit={result.returncode}): {stderr_snippet}"
                )
            raise RuntimeError(f"claude CLI exited {result.returncode}: {stderr_snippet}")

        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(f"claude CLI produced non-JSON output on stdout: {type(exc).__name__}: {exc}") from exc

        if payload.get("is_error"):
            message = str(payload.get("result", ""))[:300]
            if _looks_like_rate_limit(message, ""):
                raise TransientTranslationError(f"claude CLI reported is_error=true (rate-limit/quota): {message}")
            raise RuntimeError(f"claude CLI reported is_error=true: {message}")

        raw = payload.get("result")
        if not isinstance(raw, str) or not raw.strip():
            raise RuntimeError("claude CLI returned no usable translated text")
        translated = _strip_wrapping(raw)
        if not translated:
            raise RuntimeError("claude CLI translation was empty after stripping wrapping")
        return translated
