"""Translation provider abstraction + persistent cache (credential-
independent architecture pass, 2026-08-14; real-provider activation pass,
2026-08-14; failure-cache/retry safety pass, Phase 3A.1, 2026-08-14).

Provider-neutral: report.web_data_v2 (and everything above it) depends
only on build_translation_provider()/translate_and_cache() from this
file, never on a specific translation API SDK -- the same boundary
discipline report.llm_interface.py already established for the
news-synthesis LLM (build_llm()/StructuredLLM). Implementations today:
NullTranslationProvider (always TRANSLATION_UNAVAILABLE, never fabricates)
and report.translation_anthropic.AnthropicTranslationProvider (real
Anthropic-backed translation, only reachable via TRANSLATION_PROVIDER=
anthropic + a real ANTHROPIC_API_KEY -- see build_translation_provider()).
Swapping in a different provider later is a new provider class + one
config branch here -- zero changes to report/web_data_v2.py or anything
above it.

Cache contract:
- original_title/original_summary are NEVER overwritten -- a translation
  is always an ADDITIVE ko_title/ko_summary pair alongside the untouched
  original, so an unavailable/failed translation never hides the real
  source text (report/web_render_v2.py keeps rendering item["title"]
  exactly as before; this module adds fields, it never repurposes one).
- cache_key is a stable hash of (provider_name, model, prompt_version,
  target_lang, normalized_text) -- versioned so a provider/model/prompt
  change can NEVER silently reuse a translation produced under different
  conditions; the SAME real text under the SAME provider/model/prompt
  always resolves to the SAME cache row. This intentionally does NOT
  include any credential/secret VALUE -- only provider_name (a class
  name) and model_name (a public model identifier) ever enter the hash.

Failure-state model (Phase 3A.1) -- FAILED no longer hides two different
meanings under one word:
- STATUS_TRANSLATED: real translation succeeded. Long-lived cache hit --
  provider is never called again for the same (provider, model,
  prompt_version, text) tuple.
- STATUS_NOT_REQUIRED: source text is already sufficiently Korean. NEVER
  written to the DB at all -- zero API cost, and the deterministic check
  itself is cheaper than a cache lookup.
- STATUS_UNAVAILABLE: the PROVIDER (not this specific text) is not
  configured -- e.g. TRANSLATION_PROVIDER=none, or TRANSLATION_PROVIDER=
  anthropic with no ANTHROPIC_API_KEY set. Detected deterministically via
  provider.is_configured() BEFORE any cache lookup, DB write, or network
  attempt. This is intentionally NEVER persisted per-text: a config/
  credential gap is a provider-wide condition, not "this headline failed
  to translate", so it can never accumulate a per-text failure cache and
  can never block a real attempt once the provider becomes configured --
  there is nothing cached to short-circuit on, by construction.
- STATUS_FAILED + failure_kind=TRANSIENT: a genuine runtime failure of a
  CONFIGURED provider that is plausibly retryable (network timeout/
  connection error/429/5xx -- see report/translation_anthropic.py's
  TransientTranslationError mapping). Cached WITH bounded exponential
  backoff (retry_after) so it is retried on a later run, never on every
  single render, and never immediately re-hammered within the same run
  (see AnthropicTranslationProvider's own per-instance circuit breaker).
  A retry that succeeds UPDATES the existing row to TRANSLATED (UPSERT,
  never INSERT OR IGNORE -- see the CRITICAL RETRY UPDATE CONTRACT note
  on translate_and_cache below).
- STATUS_FAILED + failure_kind=PERMANENT: a genuine runtime failure that
  is NOT plausibly retryable (e.g. the provider's response is
  deterministically empty/malformed/unsafe for this exact text). Cached
  as a normal, long-lived cache hit -- like TRANSLATED, never retried --
  so a single permanently-broken text can never trigger an unbounded
  retry loop. Never trips the provider-wide circuit breaker (a single
  text's permanent validation failure is not evidence the provider itself
  is down).
"""

import hashlib
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from report.translation_validation import validate_translation_facts

STATUS_TRANSLATED = "TRANSLATED"
STATUS_UNAVAILABLE = "TRANSLATION_UNAVAILABLE"
STATUS_FAILED = "FAILED"
STATUS_NOT_REQUIRED = "NOT_REQUIRED"

FAILURE_KIND_TRANSIENT = "TRANSIENT"
FAILURE_KIND_PERMANENT = "PERMANENT"

DEFAULT_TARGET_LANG = "ko"

# Bumping this invalidates every cached translation cache_key the next time
# translate_and_cache runs (a different prompt_version hashes differently),
# without needing an ALTER TABLE -- see module docstring.
TRANSLATION_PROMPT_VERSION = "v1"

# Transient-retry backoff policy -- the single source of truth for these
# numbers (never hardcoded elsewhere). Exponential, attempt 1 waits
# BASE seconds, attempt 2 waits 2x, attempt 3 4x, ... capped at MAX so a
# long-running outage still gets retried at least once a day, not less and
# not more (bounded retry-storm guard).
TRANSIENT_RETRY_BASE_SECONDS = 600  # 10 minutes
TRANSIENT_RETRY_MAX_SECONDS = 86400  # 24 hours


class TranslationUnavailableError(RuntimeError):
    """Raised by a provider that has no real way to translate right now
    (e.g. NullTranslationProvider, always -- or a real provider whose
    credential is missing). Distinct from an ordinary Exception (recorded
    as STATUS_FAILED, a genuine runtime failure of a configured
    provider) -- this is the "not configured at all" case. Callers should
    prefer provider.is_configured() to avoid this case entirely before
    ever calling translate(); this exception remains a defensive fallback
    for a provider whose is_configured() under- reports."""


class TransientTranslationError(RuntimeError):
    """Raised by a CONFIGURED provider for a plausibly-retryable runtime
    failure (network timeout/connection error/429 rate limit/5xx server
    error) -- distinct from a permanent, deterministic failure (any other
    Exception). translate_and_cache maps this to STATUS_FAILED with
    failure_kind=TRANSIENT and a bounded backoff retry_after, never an
    immediate re-call."""


class TranslationProvider(ABC):
    @abstractmethod
    def translate(self, text, target_lang):
        """Returns the translated text, or raises (TranslationUnavailableError
        if not configured; TransientTranslationError for a retryable runtime
        failure; any other Exception for a permanent runtime failure) --
        implementations never invent a fallback translation string."""

    @property
    def model_name(self):
        """Optional model identifier, folded into the cache key so a model
        change can never silently reuse a translation produced under a
        different model. None for a provider with no real model concept
        (e.g. NullTranslationProvider)."""
        return None

    def is_configured(self):
        """True if this provider has everything it needs (e.g. a
        credential) to attempt a real translate() call, decided
        deterministically without a network round-trip. Default True for
        providers with no separate config gate; override to return False
        deterministically -- translate_and_cache uses this to short-circuit
        BEFORE any cache lookup, DB write, or network attempt, so a
        missing-credential state never accumulates a per-text failure
        cache (see module docstring)."""
        return True


class NullTranslationProvider(TranslationProvider):
    """The only provider available without a real translation/LLM
    credential in this environment. Never calls out to any network
    service, never fabricates a translation -- always reports itself as
    unconfigured so translate_and_cache short-circuits before ever calling
    translate(), and the real, untouched original text keeps being shown.

    cache_lookup_hint (PERMANENT ZERO-PAYG SAFETY pass): an optional
    (provider_name, model) string pair identifying which REAL provider's
    EXISTING cache entries translate_and_cache may still read (never
    write) while this Null instance is active -- e.g. under
    SUPER_NEWS_NO_PAID_API=1, build_translation_provider() below passes
    ("AnthropicTranslationProvider", <model>) so an already-paid-for
    cached translation can still be served for zero cost, without this
    class ever importing report.translation_anthropic, the anthropic SDK,
    or constructing anything that could reach api.anthropic.com. Purely a
    pair of strings used as a read-only cache SELECT key; None (default)
    means no hint, matching every prior caller's behavior unchanged."""

    def __init__(self, cache_lookup_hint=None):
        self.cache_lookup_hint = cache_lookup_hint

    def is_configured(self):
        return False

    def translate(self, text, target_lang):
        raise TranslationUnavailableError(
            "No translation provider is configured in this environment (TRANSLATION_PROVIDER unset/'none')."
        )


def build_translation_provider():
    """Factory, same shape as report.llm_interface.build_llm(): TRANSLATION_
    PROVIDER (env, default 'none') selects the implementation. 'none'/'null'
    -> NullTranslationProvider. 'anthropic' -> report.translation_anthropic.
    AnthropicTranslationProvider -- constructed even with no ANTHROPIC_API_KEY
    set (the missing-credential case degrades to is_configured()==False, not
    a construction-time crash here, so a misconfigured/not-yet-credentialed
    environment never fails page generation). Any other value is a loud
    configuration error, never a silent fallback to a default provider.

    SUPER_NEWS_NO_PAID_API (env, unrelated to TRANSLATION_PROVIDER) is a
    PERMANENT, process-level cost-gate override: when truthy, this ALWAYS
    returns a NullTranslationProvider() -- report.translation_anthropic.
    AnthropicTranslationProvider is NEVER imported or constructed on this
    path, so api.anthropic.com is structurally unreachable from here,
    regardless of TRANSLATION_PROVIDER/ANTHROPIC_API_KEY. When
    TRANSLATION_PROVIDER=anthropic is ALSO configured, the returned
    NullTranslationProvider carries a cache_lookup_hint so translate_and_
    cache can still serve an EXISTING, already-paid-for cached translation
    (a pure read, zero cost) -- a cache MISS degrades to STATUS_UNAVAILABLE,
    never a live call. Checked first, before the TRANSLATION_PROVIDER
    branch below."""
    from config import get_optional_env

    if (get_optional_env("SUPER_NEWS_NO_PAID_API", "") or "").strip().lower() in ("1", "true", "yes"):
        provider = (get_optional_env("TRANSLATION_PROVIDER", "none") or "none").strip().lower()
        if provider == "anthropic":
            # Mirrors report.translation_anthropic.AnthropicTranslationProvider's
            # own (class name, default model) cache-key identity WITHOUT
            # importing that module -- see this function's docstring.
            model = get_optional_env("ANTHROPIC_TRANSLATION_MODEL", "claude-haiku-4-5-20251001")
            return NullTranslationProvider(cache_lookup_hint=("AnthropicTranslationProvider", model))
        return NullTranslationProvider()

    provider = (get_optional_env("TRANSLATION_PROVIDER", "none") or "none").strip().lower()
    if provider in ("none", "null"):
        return NullTranslationProvider()
    if provider == "anthropic":
        from report.translation_anthropic import AnthropicTranslationProvider

        return AnthropicTranslationProvider()
    raise ValueError(f"Unsupported TRANSLATION_PROVIDER={provider!r}; only 'none'/'anthropic' are supported.")


# Conservative: only skip translation when the text is CLEARLY, mostly
# Korean already -- one stray Hangul character (e.g. a Korean artist name
# inside an otherwise-English headline) must never trigger a skip. Ambiguous
# cases (no letters at all, or a near-even mix) always fall through to a
# real translation attempt, per the "애매하면 번역 대상으로 처리" contract.
_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_KOREAN_SUFFICIENCY_THRESHOLD = 0.6


# Minimal entity-transliteration glossary (SOURCE EXPANSION + CONTENT
# QUALITY HARDENING phase, 2026-08-15 Korean-quality audit): a real
# cross-article defect was found in production -- the SAME entity
# rendered two different ways by independent translation calls ("Mark
# Zuckerberg" kept in Latin script in one article, transliterated to
# "마크 저커버그" in another; "Instagram" likewise mixed with "인스타그램").
# Deliberately tiny and seeded only from real observed inconsistencies,
# not a speculative broad dictionary -- add an entry here only when a
# real defect justifies it, per this phase's own explicit instruction.
# Applied only going forward, on a freshly successful translation; it
# never rewrites an already-cached row (see translate_and_cache).
_ENTITY_GLOSSARY = {
    "마크 저커버그": "Mark Zuckerberg",
    "저커버그": "Mark Zuckerberg",
    "인스타그램": "Instagram",
}
_ENTITY_GLOSSARY_PATTERN = re.compile("|".join(re.escape(k) for k in _ENTITY_GLOSSARY))


def _apply_entity_glossary(text):
    if not text:
        return text
    return _ENTITY_GLOSSARY_PATTERN.sub(lambda m: _ENTITY_GLOSSARY[m.group(0)], text)


def _is_already_korean(text):
    hangul = len(_HANGUL_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = hangul + latin
    if total == 0:
        return False
    return (hangul / total) >= _KOREAN_SUFFICIENCY_THRESHOLD


def _cache_key(text, target_lang, provider_name, model, prompt_version):
    # ␟ (SYMBOL FOR UNIT SEPARATOR) as a field delimiter that can't
    # realistically appear in real article text or a provider/model name --
    # avoids a same-hash collision between differently-segmented fields a
    # plain "|".join would risk. Only provider_name/model/prompt_version/
    # target_lang/text ever enter this hash -- no credential/secret VALUE.
    payload = f"{provider_name}␟{model or ''}␟{prompt_version}␟{target_lang}␟{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _next_retry_after(now, attempt_count):
    cooldown = min(
        TRANSIENT_RETRY_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)),
        TRANSIENT_RETRY_MAX_SECONDS,
    )
    return now + timedelta(seconds=cooldown)


def _result(translated_text, status, failure_kind=None, attempt_count=None, retry_after=None, last_attempt_at=None):
    return {
        "translated_text": translated_text,
        "status": status,
        "failure_kind": failure_kind,
        "attempt_count": attempt_count,
        "retry_after": retry_after,
        "last_attempt_at": last_attempt_at,
    }


def get_cached_translation(conn, text, target_lang, provider_name, model, prompt_version=TRANSLATION_PROMPT_VERSION):
    """Returns the cached row dict (see _result's shape) if this exact
    (provider, model, prompt_version, text, target_lang) tuple was already
    processed (ANY status, including a previously-recorded FAILED), or None
    if it's genuinely never been attempted under these exact conditions."""
    if not text:
        return None
    row = conn.execute(
        "SELECT translated_text, status, failure_kind, attempt_count, retry_after, last_attempt_at "
        "FROM translation_cache WHERE cache_key = ? AND target_lang = ?",
        (_cache_key(text, target_lang, provider_name, model, prompt_version), target_lang),
    ).fetchone()
    if row is None:
        return None
    return _result(
        row["translated_text"],
        row["status"],
        row["failure_kind"],
        row["attempt_count"],
        row["retry_after"],
        row["last_attempt_at"],
    )


def translate_and_cache(conn, provider, text, target_lang=DEFAULT_TARGET_LANG, now_fn=None):
    """Idempotent for TRANSLATED/PERMANENT-FAILED/UNAVAILABLE (any cache hit
    under the SAME provider/model/prompt_version is returned WITHOUT calling
    the provider again). NOT idempotent-forever for a TRANSIENT FAILED row:
    once now_fn() reaches its stored retry_after, the provider is called
    again exactly once, bounded by exponential backoff (see
    TRANSIENT_RETRY_BASE_SECONDS/TRANSIENT_RETRY_MAX_SECONDS) -- never on
    every single call within the same retry window. A provider/model/
    prompt_version CHANGE hashes to a different cache_key, so it can never
    accidentally reuse a translation (or a failure) produced under
    different conditions.

    CRITICAL RETRY UPDATE CONTRACT: a retry that succeeds UPSERTs the
    existing row to TRANSLATED (ON CONFLICT DO UPDATE) -- never INSERT OR
    IGNORE, which would leave the stale FAILED row in place forever even
    after a real, later success.

    now_fn (optional): a zero-arg callable returning the current UTC
    datetime, for deterministic retry-window tests -- never real sleep/wall
    time in a test. Defaults to real datetime.now(timezone.utc).

    Never raises -- a provider failure/unavailability is itself a real,
    cached (or deliberately uncached, for UNAVAILABLE) outcome, not an
    exception propagated to the caller (report/web_data_v2.py must never
    crash the dashboard over a translation-layer failure)."""
    if not text:
        return _result(None, STATUS_UNAVAILABLE)

    normalized = text.strip()
    if not normalized:
        return _result(None, STATUS_UNAVAILABLE)

    if target_lang == DEFAULT_TARGET_LANG and _is_already_korean(normalized):
        # Never cached/API-called at all -- the deterministic check itself
        # is the cheap part, and re-running it costs nothing. ko field uses
        # the real (already-Korean) original text, per the data-contract
        # rule that ko_* is only ever real text, never fabricated.
        return _result(normalized, STATUS_NOT_REQUIRED)

    if not provider.is_configured():
        # Provider-wide config/credential gap (or a deliberate cost-safety
        # override, e.g. SUPER_NEWS_NO_PAID_API=1) -- deterministic, no
        # network attempt, and deliberately never persisted per-text (see
        # module docstring): there is nothing cached here that could ever
        # go stale, so a credential added later is used on the very next
        # call with zero migration/cleanup needed.
        #
        # PERMANENT ZERO-PAYG SAFETY: if the provider carries a
        # cache_lookup_hint (see NullTranslationProvider), still perform
        # ONE read-only cache SELECT under that (provider_name, model)
        # identity before giving up -- an existing, already-paid-for
        # TRANSLATED row is safe to reuse (zero cost, zero network); a
        # cache MISS still degrades to STATUS_UNAVAILABLE exactly as
        # before, never a live provider call.
        hint = getattr(provider, "cache_lookup_hint", None)
        if hint:
            hint_provider_name, hint_model = hint
            hinted_cached = get_cached_translation(conn, normalized, target_lang, hint_provider_name, hint_model)
            if hinted_cached is not None and hinted_cached["status"] == STATUS_TRANSLATED:
                return hinted_cached
        return _result(None, STATUS_UNAVAILABLE)

    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    provider_name = type(provider).__name__
    model = getattr(provider, "model_name", None)

    prior_attempt_count = 0
    cached = get_cached_translation(conn, normalized, target_lang, provider_name, model)
    if cached is not None:
        if cached["status"] == STATUS_FAILED and cached["failure_kind"] == FAILURE_KIND_TRANSIENT:
            retry_after = cached["retry_after"]
            if retry_after and now_fn() < datetime.fromisoformat(retry_after):
                return cached  # still cooling down -- zero provider calls
            prior_attempt_count = cached["attempt_count"] or 0
            # else: retry window reached, fall through to a real attempt.
        else:
            # TRANSLATED / PERMANENT FAILED / defensive UNAVAILABLE row --
            # always a normal, long-lived cache hit. Never retried.
            return cached

    key = _cache_key(normalized, target_lang, provider_name, model, TRANSLATION_PROMPT_VERSION)
    now = now_fn()
    now_iso = now.isoformat()

    try:
        raw_translated = provider.translate(normalized, target_lang)
    except TranslationUnavailableError:
        # Defensive fallback: is_configured() should already have prevented
        # reaching here. Treated identically to the config-gate short-
        # circuit above -- never persisted, so it can't block a later real
        # attempt either.
        return _result(None, STATUS_UNAVAILABLE)
    except TransientTranslationError:
        translated_text = None
        status, failure_kind = STATUS_FAILED, FAILURE_KIND_TRANSIENT
        attempt_count = prior_attempt_count + 1
        retry_after = _next_retry_after(now, attempt_count).isoformat()
    except Exception:
        translated_text = None
        status, failure_kind, retry_after = STATUS_FAILED, FAILURE_KIND_PERMANENT, None
        attempt_count = prior_attempt_count + 1
    else:
        # Real, confirmed production defects (SUPER_NEWS_HANDOFF.md,
        # CONTENT INTEGRITY FINALIZATION phase): a provider call can
        # "succeed" while returning something that must never be trusted
        # as a real translation -- a numeric/date/version fact silently
        # altered, or (the LLM-refusal case) not even a translation at
        # all. A validation failure is routed through the SAME
        # STATUS_FAILED/FAILURE_KIND_TRANSIENT path as a transient
        # network failure -- never cached as TRANSLATED, the original
        # stays displayed, and a later attempt remains possible -- no new
        # status value, no schema change.
        validation = validate_translation_facts(normalized, raw_translated)
        if validation.ok:
            translated_text = _apply_entity_glossary(raw_translated)
            status, failure_kind, attempt_count, retry_after = STATUS_TRANSLATED, None, 0, None
        else:
            translated_text = None
            status, failure_kind = STATUS_FAILED, FAILURE_KIND_TRANSIENT
            attempt_count = prior_attempt_count + 1
            retry_after = _next_retry_after(now, attempt_count).isoformat()

    # UPSERT (idempotent under ux_translation_cache_key): a first-time
    # failure/success INSERTs a new row; a retry of an existing TRANSIENT
    # FAILED row UPDATEs it in place -- this is what makes a retry-success
    # actually land as TRANSLATED instead of leaving a stale FAILED row
    # behind (see CRITICAL RETRY UPDATE CONTRACT above). created_at is
    # deliberately absent from the UPDATE SET clause so it's preserved from
    # the original insert.
    conn.execute(
        """INSERT INTO translation_cache
           (cache_key, source_lang, target_lang, original_text, translated_text,
            status, failure_kind, attempt_count, retry_after, last_attempt_at,
            provider, created_at, updated_at)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cache_key, target_lang) DO UPDATE SET
             translated_text = excluded.translated_text,
             status = excluded.status,
             failure_kind = excluded.failure_kind,
             attempt_count = excluded.attempt_count,
             retry_after = excluded.retry_after,
             last_attempt_at = excluded.last_attempt_at,
             updated_at = excluded.updated_at""",
        (
            key, target_lang, normalized, translated_text, status, failure_kind,
            attempt_count, retry_after, now_iso, provider_name, now_iso, now_iso,
        ),
    )
    conn.commit()
    return _result(translated_text, status, failure_kind, attempt_count, retry_after, now_iso)
