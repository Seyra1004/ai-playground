"""Anthropic implementation of report.translation.TranslationProvider --
same SDK, same import-only-here discipline as report/llm_anthropic.py, but
a SEPARATE provider identity: translation is configured independently
(TRANSLATION_PROVIDER/ANTHROPIC_TRANSLATION_MODEL) from news synthesis
(LLM_PROVIDER/LLM_MODEL), even though both may point at the same
ANTHROPIC_API_KEY -- either can change provider/model without touching the
other (report.translation.build_translation_provider is the only caller of
this class; report.llm_interface.build_llm never imports it).

Translation-only system prompt: pure language conversion. Never summarizes,
adds information, infers facts, or edits meaning. The API key/model VALUE
is never logged, raised in an exception message, or otherwise surfaced --
only get_optional_env('ANTHROPIC_API_KEY') reads it, and it is passed
straight to the SDK client, never interpolated into a string this module
constructs itself.

Failure classification (Phase 3A.1; HTTP-semantics correction, Phase
3A.2) -- three real meanings, mapped against the official Anthropic API
error semantics, never conflated:

- CONFIG/PROVIDER UNAVAILABLE (401 authentication_error, 402 -- billing,
  403 permission_error, 404 not_found_error -- i.e. the model/resource
  itself is misconfigured). None of these say anything about THIS TEXT --
  they say the provider/account/config is unusable right now. Raised as
  TranslationUnavailableError (the SAME exception/outcome as a missing
  ANTHROPIC_API_KEY) so report.translation.translate_and_cache's existing
  handling applies unchanged: STATUS_UNAVAILABLE, NEVER persisted per-text
  (see report.translation's module docstring for why). Also trips a
  SEPARATE per-instance breaker (_unavailable_tripped) from the transient
  one below -- the remaining items in this same run fail fast with zero
  further network attempts, and because nothing is ever persisted for
  UNAVAILABLE, a fixed credential/config on the NEXT run (a fresh instance)
  is used immediately, with no stale row to block it.
- TRANSIENT (APIConnectionError/timeout, 409 conflict, 429 rate_limit_error,
  500/502/503 server errors, 529 overloaded_error). Raised as
  TransientTranslationError -- report.translation.translate_and_cache's
  existing bounded-backoff/retry/UPSERT-on-success contract applies
  unchanged. Also trips the existing per-instance _circuit_tripped breaker.
- TEXT/REQUEST PERMANENT: only a genuinely deterministic, retry-is-
  pointless failure for THIS exact input -- this module's own empty/unsafe-
  output check, or any other 4xx APIStatusError not in the CONFIG/PROVIDER
  UNAVAILABLE set above (e.g. 400 invalid_request_error, 413/422 request-
  too-large). Never trips either breaker (a single text's deterministic
  failure says nothing about the provider as a whole).

Both breakers are per-INSTANCE, not per-text: report.translation.
build_translation_provider() constructs exactly one provider instance per
report-generation run (reused across every item in that run), so tripping
either one stops this run from sending N more doomed network calls for the
remaining N items the moment ONE real provider-wide problem is observed.
Both reset naturally on the next run (a fresh instance).

SDK retry ownership (Phase 3A.2): the installed anthropic SDK (0.121.0)
defaults anthropic.Anthropic(...) to max_retries=2 -- i.e. a single
translate() call could silently trigger up to 3 real network attempts
inside the SDK itself before ever raising to this module, making "how many
network attempts did one translate() call make" unpredictable and
overlapping with SUPER NEWS's own attempt_count/retry_after/circuit-breaker
layer (which already owns retry policy end-to-end). This module
deliberately constructs the client with max_retries=0 so SUPER NEWS is the
SINGLE retry-policy owner -- one translate() call is exactly one real
network attempt, full stop.
"""

import anthropic

from config import get_optional_env
from report.translation import TranslationProvider, TranslationUnavailableError, TransientTranslationError

# 401/402/403/404 are provider/account/config-level -- never a text-specific
# failure. See module docstring's CONFIG/PROVIDER UNAVAILABLE section.
_CONFIG_UNAVAILABLE_STATUS_CODES = {401, 402, 403, 404}

# 409/429/5xx/529 are plausibly retryable -- see module docstring's
# TRANSIENT section. 529 (overloaded_error) is Anthropic-specific and must
# not be missed.
_TRANSIENT_STATUS_CODES = {409, 429, 500, 502, 503, 504, 529}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You are a precise translation engine, not an editor or summarizer. "
    "Translate the given text into natural, accurate Korean. Rules: never "
    "add information, opinions, or explanations that are not in the "
    "original; never omit meaning; never summarize or paraphrase away "
    "detail. Keep brand/company/product/model names, stock tickers, artist "
    "names, and album/track titles as they are in the original -- do not "
    "force-transliterate a proper noun into Korean unless a Korean form is "
    "the definitive, standard usage for it. If the input reads as a "
    "headline (short, no trailing sentence punctuation), the Korean output "
    "must also read as a headline in the same style, not a full sentence -- "
    "write it the way a professional Korean newsroom would phrase the SAME "
    "headline (concise, declarative, no unnecessary subject pronoun, no "
    "literal English sentence structure carried over word-for-word, no "
    "question-style teaser unless the original itself is a genuine "
    "question) -- while changing nothing about the actual reported fact. "
    "If the input is body/snippet text, preserve its original informational "
    "density -- do not compress or expand it, and do not add promotional "
    "phrasing. Some source outlets write their snippet/dek as a rhetorical "
    "question or a string of teaser questions (e.g. 'How does it actually "
    "work? Can it be hidden by editing?') -- when translating this into "
    "Korean, rephrase it as a normal declarative Korean news-summary "
    "sentence that states the same real fact/topic the question was "
    "gesturing at, the way a professional Korean newsroom would write a "
    "summary line, INSTEAD of a literal '~까요?' question translation -- "
    "never invent a fact that answers the question, only restate what the "
    "question was actually about as a statement. If the original snippet "
    "is a genuine, substantive question central to the reporting (rare), "
    "keep it as a real Korean question rather than forcing a statement. "
    "Output ONLY the translated text: no preamble, no quotation marks "
    "around it, no explanation, and no HTML or markup of any kind."
)


class AnthropicTranslationProvider(TranslationProvider):
    """Only ever calls the network from inside translate() -- never at
    import time or __init__ time -- so build_translation_provider() always
    succeeds structurally even with no credential configured. A missing
    ANTHROPIC_API_KEY, or a 401/402/403/404 provider/config-level error
    encountered at call time, surfaces as TranslationUnavailableError from
    translate() itself, exactly like NullTranslationProvider, and is
    handled identically by report.translation.translate_and_cache
    (STATUS_UNAVAILABLE, NEVER persisted per-text -- see module docstring)."""

    def __init__(self, model=None, api_key=None):
        self._model = model or get_optional_env("ANTHROPIC_TRANSLATION_MODEL", DEFAULT_MODEL)
        self._api_key = api_key or get_optional_env("ANTHROPIC_API_KEY")
        self._client = None
        self._circuit_tripped = False
        self._unavailable_tripped = False

    @property
    def model_name(self):
        return self._model

    def is_configured(self):
        return bool(self._api_key)

    def translate(self, text, target_lang):
        if not self._api_key:
            raise TranslationUnavailableError(
                "TRANSLATION_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."
            )
        if self._unavailable_tripped:
            # A provider/account/config-level error (401/402/403/404) was
            # already observed this run -- fail fast, no further network
            # attempts until the next run (fresh instance). See module
            # docstring's CONFIG/PROVIDER UNAVAILABLE section.
            raise TranslationUnavailableError(
                "Circuit breaker open: a provider/config-level error (401/402/403/404) "
                "was already observed this run."
            )
        if self._circuit_tripped:
            # A provider-wide transient failure was already observed this
            # run -- fail fast, no further network attempts until the next
            # run (fresh instance). See module docstring.
            raise TransientTranslationError(
                "Circuit breaker open: a provider-wide transient failure was already observed this run."
            )
        if self._client is None:
            # max_retries=0: SUPER NEWS owns retry policy end-to-end (see
            # module docstring's SDK retry ownership section) -- the SDK
            # must never silently retry underneath us.
            self._client = anthropic.Anthropic(api_key=self._api_key, max_retries=0)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Target language code: {target_lang}\n\n{text}"}],
            )
        except anthropic.APIConnectionError as exc:
            # Covers both plain connection errors and timeouts (APITimeout
            # Error is a subclass of APIConnectionError in this SDK).
            self._circuit_tripped = True
            raise TransientTranslationError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code in _CONFIG_UNAVAILABLE_STATUS_CODES:
                self._unavailable_tripped = True
                raise TranslationUnavailableError(str(exc)) from exc
            if exc.status_code in _TRANSIENT_STATUS_CODES:
                self._circuit_tripped = True
                raise TransientTranslationError(str(exc)) from exc
            raise  # remaining 4xx (bad request/request-too-large/etc.) -- permanent, not retryable.

        translated = "".join(block.text for block in response.content if block.type == "text").strip()
        if not translated or "<script" in translated.lower():
            # A genuine, deterministic runtime failure (empty/unsafe model
            # output) -- not retryable, and never evidence of a provider-
            # wide outage, so this does NOT trip the circuit breaker.
            # translate_and_cache records this as STATUS_FAILED/PERMANENT.
            raise ValueError("Anthropic translation returned empty or unsafe output.")
        return translated
