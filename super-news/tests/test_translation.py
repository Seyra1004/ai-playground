"""report.translation: provider factory (none/anthropic/unsupported),
cache versioning (provider/model/prompt_version isolation), NOT_REQUIRED
Korean-sufficiency detection, idempotency, original-text preservation,
secret non-exposure, and (Phase 3A.1) the failure-cache/retry state model:
missing-credential never becomes a per-text cache row, transient failures
retry on a bounded backoff, permanent failures never retry, a retry success
UPSERTs the row to TRANSLATED, and the Anthropic provider's per-run circuit
breaker. Uses fake providers/injected clocks -- never a live network/API
call, never real sleep."""

from datetime import datetime, timedelta, timezone

import anthropic
import httpx
import pytest

from db.database import connect, init_db
from report.translation import (
    FAILURE_KIND_PERMANENT,
    FAILURE_KIND_TRANSIENT,
    STATUS_FAILED,
    STATUS_NOT_REQUIRED,
    STATUS_TRANSLATED,
    STATUS_UNAVAILABLE,
    TRANSIENT_RETRY_BASE_SECONDS,
    TransientTranslationError,
    TranslationProvider,
    TranslationUnavailableError,
    build_translation_provider,
    get_cached_translation,
    translate_and_cache,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


class FakeProvider(TranslationProvider):
    """configured/translations/model as before; fail_ids raise a permanent
    ValueError; transient_fail_ids raise TransientTranslationError as long
    as the text stays in that (mutable) set -- tests remove a text from it
    to simulate "the outage ended, next attempt succeeds"."""

    def __init__(self, translations=None, model=None, fail_ids=None, transient_fail_ids=None, configured=True):
        self.calls = []
        self._translations = translations or {}
        self._model = model
        self._fail_ids = fail_ids or set()
        self._transient_fail_ids = transient_fail_ids if transient_fail_ids is not None else set()
        self._configured = configured

    @property
    def model_name(self):
        return self._model

    def is_configured(self):
        return self._configured

    def translate(self, text, target_lang):
        self.calls.append(text)
        if text in self._transient_fail_ids:
            raise TransientTranslationError("boom-transient")
        if text in self._fail_ids:
            raise ValueError("boom-permanent")
        # A plausible-looking Korean placeholder, not the literal source
        # text -- report.translation_validation's real-Korean-output check
        # (CONTENT INTEGRITY FINALIZATION phase) would otherwise correctly
        # reject a mostly-Latin fallback exactly the way it must reject a
        # real non-translation (e.g. an LLM refusal) in production.
        return self._translations.get(text, f"[가짜 번역 결과 {len(text)}자]")


class UnavailableProvider(TranslationProvider):
    """A provider that does NOT override is_configured() (defaults True)
    but still raises TranslationUnavailableError from translate() itself --
    exercises the defensive fallback path."""

    def translate(self, text, target_lang):
        raise TranslationUnavailableError("no credential")


def _clock(start):
    """Returns a mutable now_fn: clock.now() reads the current instant,
    clock.advance(seconds) moves it forward. No real sleep anywhere."""
    state = {"now": start}

    def now_fn():
        return state["now"]

    def advance(seconds):
        state["now"] = state["now"] + timedelta(seconds=seconds)

    now_fn.advance = advance
    return now_fn


# ---- provider factory -------------------------------------------------


def test_build_translation_provider_defaults_to_null(monkeypatch):
    monkeypatch.delenv("TRANSLATION_PROVIDER", raising=False)
    from report.translation import NullTranslationProvider
    assert isinstance(build_translation_provider(), NullTranslationProvider)


def test_build_translation_provider_unsupported_value_raises(monkeypatch):
    # Defensive delenv: a sibling script module (e.g. scripts/generate_
    # daily_web_report_v2.py) may have already forced this into the REAL
    # os.environ at its own import time earlier in this pytest session --
    # that mutation isn't monkeypatch-scoped, so this test must clear it
    # itself to test normal (non-cost-gated) provider-selection behavior.
    monkeypatch.delenv("SUPER_NEWS_NO_PAID_API", raising=False)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "deepl")
    with pytest.raises(ValueError):
        build_translation_provider()


def test_build_translation_provider_anthropic_constructs_without_key(monkeypatch):
    """Missing ANTHROPIC_API_KEY must not crash construction -- it degrades
    to is_configured()==False, never a construction-time crash that would
    take down page generation."""
    monkeypatch.delenv("SUPER_NEWS_NO_PAID_API", raising=False)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from report.translation_anthropic import AnthropicTranslationProvider
    provider = build_translation_provider()
    assert isinstance(provider, AnthropicTranslationProvider)
    assert provider.is_configured() is False


# ---- SUPER_NEWS_NO_PAID_API cost-gate override -------------------------


def test_no_paid_api_override_forces_null_even_with_anthropic_configured(monkeypatch):
    """The cost-gate override must win even when TRANSLATION_PROVIDER=
    anthropic AND a real ANTHROPIC_API_KEY is set -- this is the guard a
    dry run relies on to guarantee zero outbound API calls regardless of
    the environment's normal production translation config."""
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: False)
    from report.translation import NullTranslationProvider
    provider = build_translation_provider()
    assert isinstance(provider, NullTranslationProvider)
    assert provider.is_configured() is False


def test_no_paid_api_null_provider_carries_anthropic_cache_lookup_hint(monkeypatch):
    """PERMANENT ZERO-PAYG SAFETY: the NullTranslationProvider returned
    under NO_PAID_API=1 + TRANSLATION_PROVIDER=anthropic carries a
    (provider_name, model) hint matching exactly what a real
    AnthropicTranslationProvider would use as its own cache-key identity
    -- so an existing, already-paid-for cache entry can still be read,
    without ever importing/constructing that class."""
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_TRANSLATION_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: False)
    provider = build_translation_provider()
    assert provider.cache_lookup_hint == ("AnthropicTranslationProvider", "claude-haiku-4-5-20251001")


def test_no_paid_api_null_provider_no_hint_when_translation_provider_not_anthropic(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "none")
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: False)
    provider = build_translation_provider()
    assert provider.cache_lookup_hint is None


# ---- FIX ONLY: ENGLISH TITLES + IMAGES pass (2026-08-17): Claude CLI
# translation selected under the no-paid-API cost gate ------------------


def test_no_paid_api_selects_claude_cli_provider_when_cli_available(monkeypatch):
    """The real fix this pass adds: a fresh, never-cached headline now
    gets a real (free, subscription-CLI-backed) translation attempt
    instead of unconditionally degrading to NullTranslationProvider."""
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.delenv("TRANSLATION_PROVIDER", raising=False)
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: True)
    provider = build_translation_provider()
    assert isinstance(provider, ClaudeCLITranslationProvider)


def test_no_paid_api_falls_back_to_null_when_claude_cli_unavailable(monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.delenv("TRANSLATION_PROVIDER", raising=False)
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    from report.translation import NullTranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: False)
    provider = build_translation_provider()
    assert isinstance(provider, NullTranslationProvider)


def test_no_paid_api_cache_hit_reused_without_constructing_anthropic_provider(conn, monkeypatch):
    """The exact real-world scenario this pass fixes: an existing
    TRANSLATED cache row (as if produced by a real, prior, paid
    AnthropicTranslationProvider call) is served under NO_PAID_API=1
    with ZERO provider construction and ZERO network call."""
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_TRANSLATION_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: False)

    # Seed the cache exactly as a real, prior AnthropicTranslationProvider
    # call would have (same provider_name/model cache-key identity) --
    # a direct DB insert, never a live network call.
    import report.translation as translation_module
    key = translation_module._cache_key(
        "headline text", "ko", "AnthropicTranslationProvider", "claude-haiku-4-5-20251001",
        translation_module.TRANSLATION_PROMPT_VERSION,
    )
    now_iso = datetime(2026, 8, 16, tzinfo=timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO translation_cache
           (cache_key, target_lang, original_text, translated_text, status, provider, created_at, updated_at)
           VALUES (?, 'ko', ?, ?, 'TRANSLATED', 'AnthropicTranslationProvider', ?, ?)""",
        (key, "headline text", "번역된 헤드라인", now_iso, now_iso),
    )
    conn.commit()

    provider = build_translation_provider()
    from report.translation import NullTranslationProvider
    assert isinstance(provider, NullTranslationProvider)

    result = translate_and_cache(conn, provider, "headline text")
    assert result["status"] == STATUS_TRANSLATED
    assert result["translated_text"] == "번역된 헤드라인"


def test_no_paid_api_cache_miss_degrades_safely_zero_network(conn, monkeypatch):
    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "1")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_TRANSLATION_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    from report.translation_claude_cli import ClaudeCLITranslationProvider
    monkeypatch.setattr(ClaudeCLITranslationProvider, "is_configured", lambda self: False)

    provider = build_translation_provider()
    result = translate_and_cache(conn, provider, "never before seen headline text")
    assert result["status"] == STATUS_UNAVAILABLE
    # Never persisted per-text (matches the existing UNAVAILABLE contract).
    assert get_cached_translation(
        conn, "never before seen headline text", "ko", "AnthropicTranslationProvider", "claude-haiku-4-5-20251001",
    ) is None


def test_no_paid_api_override_unset_or_falsy_does_not_affect_normal_behavior(monkeypatch):
    monkeypatch.delenv("SUPER_NEWS_NO_PAID_API", raising=False)
    monkeypatch.setenv("TRANSLATION_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from report.translation_anthropic import AnthropicTranslationProvider
    assert isinstance(build_translation_provider(), AnthropicTranslationProvider)

    monkeypatch.setenv("SUPER_NEWS_NO_PAID_API", "0")
    assert isinstance(build_translation_provider(), AnthropicTranslationProvider)


def test_anthropic_provider_missing_key_raises_unavailable_not_crash(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from report.translation_anthropic import AnthropicTranslationProvider
    provider = AnthropicTranslationProvider(api_key=None)
    with pytest.raises(TranslationUnavailableError):
        provider.translate("hello", "ko")


def test_anthropic_provider_never_leaks_key_in_exception_message(monkeypatch):
    """Must isolate the real ANTHROPIC_API_KEY (present in this environment
    as of Phase 3B.1/3B.2) via delenv -- api_key=None alone is not enough,
    since AnthropicTranslationProvider falls back to the real env var, and
    without delenv this test would silently skip its own assertion (no
    exception raised) while making a real, uncounted network call instead
    of exercising the missing-credential path it's named for."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from report.translation_anthropic import AnthropicTranslationProvider
    secret = "sk-ant-super-secret-value-12345"
    provider = AnthropicTranslationProvider(api_key=None)  # missing, not the secret itself
    with pytest.raises(TranslationUnavailableError) as exc_info:
        provider.translate("hello", "ko")
    assert secret not in str(exc_info.value)


# ---- CONFIG/CREDENTIAL UNAVAILABLE: never a per-text cache row --------


def test_anthropic_missing_credential_zero_network_zero_db_rows(conn, monkeypatch):
    """Required test 1: Anthropic provider selected + credential missing ->
    network calls 0 -> no per-text failure cache row created at all.
    Must delenv the real ANTHROPIC_API_KEY (present as of Phase 3B.1/3B.2)
    -- api_key=None alone falls back to the real env var otherwise, which
    would make this test assert against a configured (not missing)
    provider."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key=None)
    assert provider.is_configured() is False

    result = translate_and_cache(conn, provider, "Breaking News Today")
    assert result["status"] == STATUS_UNAVAILABLE
    assert result["translated_text"] is None
    row = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert row["c"] == 0


def test_credential_becomes_available_immediately_usable(conn):
    """Required test 2: credential-missing state, then credential becomes
    available (simulated by is_configured() flipping True on the SAME
    provider identity/text) -> the very next call reaches the provider for
    real and can succeed as TRANSLATED, with no stale row blocking it."""
    provider = FakeProvider(configured=False)
    first = translate_and_cache(conn, provider, "Fed Signals Rate Pause")
    assert first["status"] == STATUS_UNAVAILABLE
    assert provider.calls == []

    provider._configured = True  # credential "arrives"
    second = translate_and_cache(conn, provider, "Fed Signals Rate Pause")
    assert second["status"] == STATUS_TRANSLATED
    assert provider.calls == ["Fed Signals Rate Pause"]


def test_unavailable_provider_defensive_fallback_also_zero_db_rows(conn):
    """A provider whose is_configured() defaults True but still raises
    TranslationUnavailableError from translate() itself must be treated
    identically to the config-gate short-circuit -- 0 DB rows, so it can't
    block a later real attempt either."""
    provider = UnavailableProvider()
    result = translate_and_cache(conn, provider, "Breaking News Today")
    assert result["status"] == STATUS_UNAVAILABLE
    row = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert row["c"] == 0


# ---- TRANSIENT failure: bounded retry, backoff, upsert-on-success -----


def test_transient_failure_is_status_failed_transient(conn):
    """Required test 3."""
    provider = FakeProvider(transient_fail_ids={"Explosive Growth In Chips"})
    result = translate_and_cache(conn, provider, "Explosive Growth In Chips")
    assert result["status"] == STATUS_FAILED
    assert result["failure_kind"] == FAILURE_KIND_TRANSIENT
    assert result["translated_text"] is None
    assert result["retry_after"] is not None


def test_transient_failure_not_retried_before_window(conn):
    """Required test 4: same call before retry_after -> zero additional
    provider calls."""
    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    provider = FakeProvider(transient_fail_ids={"Chip Shortage Worsens"})
    translate_and_cache(conn, provider, "Chip Shortage Worsens", now_fn=clock)
    assert len(provider.calls) == 1

    clock.advance(TRANSIENT_RETRY_BASE_SECONDS - 1)
    result = translate_and_cache(conn, provider, "Chip Shortage Worsens", now_fn=clock)
    assert len(provider.calls) == 1  # no new call
    assert result["status"] == STATUS_FAILED
    assert result["failure_kind"] == FAILURE_KIND_TRANSIENT


def test_transient_failure_retried_after_window(conn):
    """Required test 5: after retry_after -> provider called exactly once
    more."""
    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    provider = FakeProvider(transient_fail_ids={"Chip Shortage Worsens"})
    translate_and_cache(conn, provider, "Chip Shortage Worsens", now_fn=clock)
    assert len(provider.calls) == 1

    clock.advance(TRANSIENT_RETRY_BASE_SECONDS + 1)
    translate_and_cache(conn, provider, "Chip Shortage Worsens", now_fn=clock)
    assert len(provider.calls) == 2


def test_retry_success_upserts_row_to_translated(conn):
    """Required test 6 -- CRITICAL RETRY UPDATE CONTRACT: an existing
    TRANSIENT FAILED row must be UPDATED to TRANSLATED on a successful
    retry, never left stale via INSERT OR IGNORE. Also asserts exactly one
    row exists for the cache_key (no duplicate row)."""
    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    provider = FakeProvider(
        transient_fail_ids={"Market Rallies On Rate Cut Hopes"},
        translations={"Market Rallies On Rate Cut Hopes": "금리 인하 기대에 시장 상승"},
    )
    first = translate_and_cache(conn, provider, "Market Rallies On Rate Cut Hopes", now_fn=clock)
    assert first["status"] == STATUS_FAILED

    provider._transient_fail_ids.discard("Market Rallies On Rate Cut Hopes")  # outage ends
    clock.advance(TRANSIENT_RETRY_BASE_SECONDS + 1)
    second = translate_and_cache(conn, provider, "Market Rallies On Rate Cut Hopes", now_fn=clock)

    assert second["status"] == STATUS_TRANSLATED
    assert second["translated_text"] == "금리 인하 기대에 시장 상승"
    rows = conn.execute("SELECT status, translated_text FROM translation_cache").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_TRANSLATED
    assert rows[0]["translated_text"] == "금리 인하 기대에 시장 상승"


def test_repeated_transient_failure_backs_off_further(conn):
    """Required test 7: retry, fail transiently again -> attempt_count and
    retry_after both advance (exponential backoff), not reset."""
    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    provider = FakeProvider(transient_fail_ids={"Ongoing Outage Headline"})
    first = translate_and_cache(conn, provider, "Ongoing Outage Headline", now_fn=clock)
    assert first["attempt_count"] == 1

    clock.advance(TRANSIENT_RETRY_BASE_SECONDS + 1)
    second = translate_and_cache(conn, provider, "Ongoing Outage Headline", now_fn=clock)
    assert second["status"] == STATUS_FAILED
    assert second["failure_kind"] == FAILURE_KIND_TRANSIENT
    assert second["attempt_count"] == 2
    assert len(provider.calls) == 2

    gap1 = datetime.fromisoformat(first["retry_after"]) - datetime.fromisoformat(first["last_attempt_at"])
    gap2 = datetime.fromisoformat(second["retry_after"]) - datetime.fromisoformat(second["last_attempt_at"])
    assert gap2 > gap1  # backoff grew, not flat/reset


# ---- PERMANENT failure: no retry, ever ---------------------------------


def test_permanent_invalid_response_never_retried(conn):
    """Required test 8: a deterministic permanent failure never re-calls
    the provider, even long after any plausible retry window, and even
    across many repeated calls (no unbounded retry loop)."""
    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    provider = FakeProvider(fail_ids={"Malformed Forever"})
    first = translate_and_cache(conn, provider, "Malformed Forever", now_fn=clock)
    assert first["status"] == STATUS_FAILED
    assert first["failure_kind"] == FAILURE_KIND_PERMANENT

    for days in (1, 30, 365):
        clock.advance(days * 86400)
        result = translate_and_cache(conn, provider, "Malformed Forever", now_fn=clock)
        assert result["status"] == STATUS_FAILED
        assert result["failure_kind"] == FAILURE_KIND_PERMANENT

    assert len(provider.calls) == 1  # only the very first attempt


# ---- idempotency / original-text preservation (pre-existing contract) --


def test_idempotent_same_provider_model_prompt_one_call(conn):
    provider = FakeProvider(model="model-a")
    translate_and_cache(conn, provider, "Same Headline Twice")
    translate_and_cache(conn, provider, "Same Headline Twice")
    assert len(provider.calls) == 1


def test_translated_cache_never_recalls_provider(conn):
    """Required test 9."""
    provider = FakeProvider()
    translate_and_cache(conn, provider, "Steady State Headline")
    result = translate_and_cache(conn, provider, "Steady State Headline")
    assert result["status"] == STATUS_TRANSLATED
    assert len(provider.calls) == 1


def test_original_text_never_overwritten(conn):
    provider = FakeProvider(translations={"Original English Title": "번역된 한국어 제목"})
    result = translate_and_cache(conn, provider, "Original English Title")
    assert result["translated_text"] == "번역된 한국어 제목"
    row = conn.execute("SELECT original_text FROM translation_cache").fetchone()
    assert row["original_text"] == "Original English Title"


def test_title_and_snippet_are_independent_cache_entries(conn):
    provider = FakeProvider()
    translate_and_cache(conn, provider, "A Title About Chips")
    translate_and_cache(conn, provider, "A longer snippet about chips manufacturing trends.")
    assert len(provider.calls) == 2
    row = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert row["c"] == 2


# ---- cache versioning: provider/model/prompt_version isolation --------


def test_different_model_never_reuses_cache(conn):
    provider_a = FakeProvider(model="model-a")
    provider_b = FakeProvider(model="model-b")
    translate_and_cache(conn, provider_a, "Model Sensitive Headline")
    result_b = translate_and_cache(conn, provider_b, "Model Sensitive Headline")
    assert len(provider_b.calls) == 1  # not a cache hit off provider_a's row
    assert result_b["status"] == STATUS_TRANSLATED


def test_different_provider_class_never_reuses_cache(conn):
    """Required test 11 (part 1)."""
    class OtherProvider(FakeProvider):
        pass

    provider_a = FakeProvider()
    provider_b = OtherProvider()
    translate_and_cache(conn, provider_a, "Provider Sensitive Headline")
    translate_and_cache(conn, provider_b, "Provider Sensitive Headline")
    row = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert row["c"] == 2


def test_prompt_version_change_never_reuses_cache(conn):
    """Required test 11 (part 2)."""
    provider = FakeProvider(model="model-a")
    translate_and_cache(conn, provider, "Prompt Version Sensitive Headline")
    hit_same = get_cached_translation(conn, "Prompt Version Sensitive Headline", "ko", "FakeProvider", "model-a", prompt_version="v1")
    miss_new_version = get_cached_translation(conn, "Prompt Version Sensitive Headline", "ko", "FakeProvider", "model-a", prompt_version="v2")
    assert hit_same is not None
    assert miss_new_version is None


def test_get_cached_translation_scoped_to_exact_versioning_tuple(conn):
    provider = FakeProvider(model="model-a")
    translate_and_cache(conn, provider, "Scoped Lookup Headline")
    hit = get_cached_translation(conn, "Scoped Lookup Headline", "ko", "FakeProvider", "model-a")
    miss = get_cached_translation(conn, "Scoped Lookup Headline", "ko", "FakeProvider", "model-b")
    assert hit is not None and hit["status"] == STATUS_TRANSLATED
    assert miss is None


# ---- NOT_REQUIRED: conservative, deterministic Korean-sufficiency check --


# ---- Fact-preservation validation, end to end through translate_and_cache ---
# (CONTENT INTEGRITY FINALIZATION phase, 2026-08-15): a provider call
# "succeeding" is not enough on its own -- see report/translation_
# validation.py, built from the real $190B->190억 and LLM-refusal-cached-
# as-translation production defects.


def test_fact_altering_translation_is_never_cached_as_translated(conn):
    provider = FakeProvider(translations={
        "Deal closed at a $190B valuation": "190억 달러 가치 평가로 거래가 마무리됐다",
    })
    result = translate_and_cache(conn, provider, "Deal closed at a $190B valuation")
    assert result["status"] == STATUS_FAILED
    assert result["failure_kind"] == FAILURE_KIND_TRANSIENT
    assert result["translated_text"] is None
    row = conn.execute("SELECT original_text FROM translation_cache").fetchone()
    assert row["original_text"] == "Deal closed at a $190B valuation"  # never lost


def test_non_korean_provider_response_is_never_cached_as_translated(conn):
    provider = FakeProvider(translations={
        "(": 'I appreciate you setting up the translation task, but the text is just "(".',
    })
    result = translate_and_cache(conn, provider, "(")
    assert result["status"] == STATUS_FAILED
    assert result["translated_text"] is None


def test_meta_response_with_embedded_korean_is_never_cached_as_translated(conn):
    """PRODUCTION INCIDENT FIX (2026-08-22, confirmed real defect): unlike
    the pure-refusal case above, this meta-response EMBEDS the real Korean
    source verbatim at the end -- is_plausibly_korean_output's Hangul
    floor alone would pass it. The refusal-marker check must still catch
    it, so it's never cached as a real translation and never reaches
    reader-facing MUSIC output."""
    source = "애플뮤직에서는 빅뱅의 'BiiiG'가 순위 상승했다."
    meta_response = (
        "No headline text was provided to translate — the content shared is a "
        "Korean-language body paragraph, and since it's already written in Korean, "
        "I should return it unchanged. However, this appears to be a news article "
        "excerpt rather than a headline for translation. If you intended to provide "
        "an English headline for me to translate into Korean, please share it and "
        "I'll translate it accordingly. If this Korean text is itself the content "
        f"you wanted returned, here it is unchanged: {source}"
    )
    provider = FakeProvider(translations={"some non-korean input": meta_response})
    result = translate_and_cache(conn, provider, "some non-korean input")
    assert result["status"] == STATUS_FAILED
    assert result["failure_kind"] == FAILURE_KIND_TRANSIENT  # retryable, never permanent
    assert result["translated_text"] is None
    row = conn.execute("SELECT status, translated_text FROM translation_cache").fetchone()
    assert row["status"] == "FAILED"
    assert row["translated_text"] is None  # never persisted as if it were real content


def test_fact_preserving_translation_still_succeeds_normally(conn):
    provider = FakeProvider(translations={
        "Deal closed at a $190B valuation": "1,900억 달러 가치 평가로 거래가 마무리됐다",
    })
    result = translate_and_cache(conn, provider, "Deal closed at a $190B valuation")
    assert result["status"] == STATUS_TRANSLATED
    assert result["translated_text"] == "1,900억 달러 가치 평가로 거래가 마무리됐다"


def test_clearly_korean_headline_is_not_required(conn):
    provider = FakeProvider()
    result = translate_and_cache(conn, provider, "삼성전자 3분기 실적 발표, 시장 예상 상회")
    assert result["status"] == STATUS_NOT_REQUIRED
    assert result["translated_text"] == "삼성전자 3분기 실적 발표, 시장 예상 상회"
    assert provider.calls == []  # zero API calls


def test_clearly_english_headline_is_translated(conn):
    provider = FakeProvider()
    result = translate_and_cache(conn, provider, "Federal Reserve Holds Interest Rates Steady")
    assert result["status"] == STATUS_TRANSLATED
    assert provider.calls == ["Federal Reserve Holds Interest Rates Steady"]


def test_already_korean_with_quoted_english_proper_nouns_bypasses_provider(conn):
    """PRODUCTION INCIDENT FIX (2026-08-22, confirmed real defect): a real
    already-Korean Producer Insight sentence quoting several English
    artist/song names ('BiiiG', 'Self Aware', ...) defeated the plain
    Hangul-ratio check -- the whole already-Korean paragraph was sent to
    a real Claude CLI translation call (prompted for a HEADLINE, not this
    paragraph), which returned meta-commentary instead of a translation.
    Quoted proper-noun spans must not count against the surrounding
    prose's own language -- zero provider calls for this real text."""
    provider = FakeProvider()
    text = (
        "애플뮤직에서는 빅뱅의 'BiiiG', 한로로의 '0+0', 검정치마의 'Ling Ling'이 순위 상승했고, "
        "같은 시기 스포티파이 차트에서는 테임 임팔라의 'Loser', Temper City의 'Self Aware', "
        "케이티 페리의 'The One That Got Away'가 동반 상승했다. 대부분 신곡이 아닌 과거 발매곡이다."
    )
    result = translate_and_cache(conn, provider, text)
    assert result["status"] == STATUS_NOT_REQUIRED
    assert result["translated_text"] == text
    assert provider.calls == []  # zero API calls


def test_mixed_language_headline_still_falls_through_to_translation(conn):
    """The quoted-span exclusion above must stay narrow: an ordinary
    English headline that happens to quote one Korean word inline is
    still correctly sent to translation -- unquoted English content
    still counts exactly as before."""
    provider = FakeProvider()
    text = 'Company unveils new "삼성" branded product line in the US market'
    result = translate_and_cache(conn, provider, text)
    assert result["status"] == STATUS_TRANSLATED
    assert provider.calls == [text]


# ---- Entity-transliteration glossary (SOURCE EXPANSION + CONTENT QUALITY -----
# HARDENING phase, 2026-08-15): real production defect -- the same entity
# rendered two different ways ("Mark Zuckerberg" left in Latin script by
# one real translation call, transliterated to "마크 저커버그" by another;
# "Instagram" vs "인스타그램" likewise) across independent articles in the
# same report. A minimal, real-defect-seeded glossary normalizes a
# freshly successful translation before caching it.


def test_transliterated_entity_normalized_to_glossary_form(conn):
    provider = FakeProvider(translations={
        "Zuckerberg unveils new product": "마크 저커버그가 신제품을 공개했다",
    })
    result = translate_and_cache(conn, provider, "Zuckerberg unveils new product")
    assert result["translated_text"] == "Mark Zuckerberg가 신제품을 공개했다"


def test_instagram_transliteration_normalized_to_glossary_form(conn):
    provider = FakeProvider(translations={
        "Instagram redesigns its logo": "인스타그램이 로고를 새로 디자인했다",
    })
    result = translate_and_cache(conn, provider, "Instagram redesigns its logo")
    assert result["translated_text"] == "Instagram이 로고를 새로 디자인했다"


def test_glossary_never_touches_unrelated_text(conn):
    provider = FakeProvider(translations={
        "Unrelated headline": "아무 관련 없는 헤드라인입니다",
    })
    result = translate_and_cache(conn, provider, "Unrelated headline")
    assert result["translated_text"] == "아무 관련 없는 헤드라인입니다"


# ---- Entity glossary safety audit (CONTENT INTEGRITY FINALIZATION phase, --
# 2026-08-15): confirms the glossary added last session does not corrupt
# surrounding grammar, does not double-expand an entity that appears both
# in full and short form in the same text, and never touches a word that
# merely contains a glossary key as a substring in an unrelated sense.


def test_glossary_never_double_expands_full_name_then_short_form(conn):
    """Real Korean journalism style: full name on first mention, surname-
    only on a later reference in the same article -- both must normalize
    independently to the SAME glossary form, never re-matched twice or
    mangled by the first replacement bleeding into the second."""
    provider = FakeProvider(translations={
        "Zuckerberg speaks twice": "마크 저커버그는 발표했다. 이어 저커버그가 질문에 답했다.",
    })
    result = translate_and_cache(conn, provider, "Zuckerberg speaks twice")
    assert result["translated_text"] == "Mark Zuckerberg는 발표했다. 이어 Mark Zuckerberg가 질문에 답했다."


def test_glossary_preserves_attached_korean_particle_grammar(conn):
    """'인스타그램' ends in a consonant-final syllable (그램/batchim ㅁ) --
    the particle choice made against the Korean spelling ('은', not '는')
    must still read correctly once the entity itself is swapped to the
    Latin form, since the underlying pronunciation is unchanged."""
    provider = FakeProvider(translations={
        "Instagram changes something": "인스타그램은 무언가를 변경했다",
    })
    result = translate_and_cache(conn, provider, "Instagram changes something")
    assert result["translated_text"] == "Instagram은 무언가를 변경했다"


def test_glossary_does_not_corrupt_word_containing_key_as_prefix(conn):
    """A real Korean word built on top of the glossary entity (e.g. an
    adjectival '...용' suffix meaning "for Instagram") must still read
    naturally after substitution -- this is the desired behavior, not a
    corruption, and confirms the substitution is a plain substring
    replacement rather than something that garbles the surrounding text."""
    provider = FakeProvider(translations={
        "New Instagram-only feature": "새로운 인스타그램용 기능이 추가됐다",
    })
    result = translate_and_cache(conn, provider, "New Instagram-only feature")
    assert result["translated_text"] == "새로운 Instagram용 기능이 추가됐다"


def test_glossary_never_rematches_its_own_latin_replacement_value(conn):
    """If the provider already returned the Latin form directly, the
    glossary (whose keys are all Korean) must leave it untouched -- no
    risk of a second, unwanted transformation pass over its own output."""
    provider = FakeProvider(translations={
        "Instagram again": "Instagram이 새 기능을 출시했다",
    })
    result = translate_and_cache(conn, provider, "Instagram again")
    assert result["translated_text"] == "Instagram이 새 기능을 출시했다"


def test_glossary_application_is_deterministic_across_repeated_calls(conn):
    provider = FakeProvider(translations={
        "Zuckerberg and Instagram": "저커버그와 인스타그램",
    })
    first = translate_and_cache(conn, provider, "Zuckerberg and Instagram")
    second = translate_and_cache(conn, provider, "Zuckerberg and Instagram")
    assert first["translated_text"] == second["translated_text"] == "Mark Zuckerberg와 Instagram"
    assert len(provider.calls) == 1  # second call was a pure cache hit


def test_english_with_korean_brand_name_still_translated(conn):
    provider = FakeProvider()
    result = translate_and_cache(
        conn, provider, "Samsung and 카카오 Announce New Partnership For AI Development This Quarter"
    )
    assert result["status"] == STATUS_TRANSLATED
    assert len(provider.calls) == 1


def test_korean_with_english_product_name_is_not_required(conn):
    provider = FakeProvider()
    result = translate_and_cache(
        conn, provider, "애플이 새로운 iPhone 17 Pro 모델을 오늘 공식 발표했으며 국내 출시일도 함께 공개했다"
    )
    assert result["status"] == STATUS_NOT_REQUIRED
    assert provider.calls == []


def test_not_required_never_writes_to_cache_table(conn):
    """Required test 10."""
    provider = FakeProvider()
    translate_and_cache(conn, provider, "완전히 한국어로 작성된 헤드라인입니다")
    row = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert row["c"] == 0


# ---- Anthropic provider: transient classification + circuit breaker ---


class _FakeMessages:
    def __init__(self, side_effect):
        self.calls = 0
        self._side_effect = side_effect

    def create(self, **kwargs):
        self.calls += 1
        result = self._side_effect(self.calls)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeClient:
    def __init__(self, side_effect):
        self.messages = _FakeMessages(side_effect)


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


def _connection_error():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


def _status_error(status_code, message="error"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request)
    return anthropic.APIStatusError(message, response=response, body=None)


# ---- Phase 3A.2: HTTP error classification matrix ----------------------


@pytest.mark.parametrize("status_code", [401, 402, 403, 404])
def test_config_provider_status_codes_map_to_unavailable_not_permanent(status_code):
    """Required tests: 401/403 (and 402/404) -> provider/config unavailable,
    never a text-specific PERMANENT failure."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider._client = _FakeClient(lambda n: _status_error(status_code))

    with pytest.raises(TranslationUnavailableError):
        provider.translate("Some Headline", "ko")


@pytest.mark.parametrize("status_code", [409, 429, 500, 502, 503, 504, 529])
def test_transient_status_codes_map_to_transient_not_permanent(status_code):
    """Required tests: 429/500/529 (and 409/502/503/504) -> TRANSIENT, never
    a permanent failure. 529 (overloaded_error) is Anthropic-specific and
    must not be missed from the transient set."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider._client = _FakeClient(lambda n: _status_error(status_code))

    with pytest.raises(TransientTranslationError):
        provider.translate("Some Headline", "ko")


def test_529_overloaded_full_contract_via_translate_and_cache(conn):
    """Section 2 hard test: a fake 529 APIStatusError, run through the real
    translate_and_cache stack, must land as failure_kind=TRANSIENT with a
    real retry_after, never failure_kind=PERMANENT."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider._client = _FakeClient(lambda n: _status_error(529, "overloaded_error"))

    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    result = translate_and_cache(conn, provider, "Overloaded Headline", now_fn=clock)
    assert result["status"] == STATUS_FAILED
    assert result["failure_kind"] == FAILURE_KIND_TRANSIENT
    assert result["failure_kind"] != FAILURE_KIND_PERMANENT
    assert result["retry_after"] is not None


def test_400_bad_request_remains_permanent(conn):
    """A genuine 4xx client error OUTSIDE the config/provider-unavailable
    set (400 invalid_request_error) is a real text/request-specific
    permanent failure, not config-unavailable and not transient."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider._client = _FakeClient(lambda n: _status_error(400, "invalid_request_error"))

    result = translate_and_cache(conn, provider, "Malformed Request Headline")
    assert result["status"] == STATUS_FAILED
    assert result["failure_kind"] == FAILURE_KIND_PERMANENT


def test_401_zero_permanent_rows_and_zero_further_network_calls_same_run(conn):
    """Required test (section 3, run 1): configured-looking provider + API
    returns 401 -> network call exactly 1 -> provider-wide unavailable
    breaker open -> further items in the same run: network calls 0,
    text-specific permanent failure rows 0 (in fact zero rows at all)."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    fake_client = _FakeClient(lambda n: _status_error(401, "authentication_error"))
    provider._client = fake_client

    first = translate_and_cache(conn, provider, "Article One Headline")
    assert first["status"] == STATUS_UNAVAILABLE
    assert fake_client.messages.calls == 1

    second = translate_and_cache(conn, provider, "Article Two Headline")
    third = translate_and_cache(conn, provider, "Article Three Headline")
    assert second["status"] == STATUS_UNAVAILABLE
    assert third["status"] == STATUS_UNAVAILABLE
    assert fake_client.messages.calls == 1  # breaker fast-failed the rest, zero new network calls

    row = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert row["c"] == 0  # zero rows -- no text-specific PERMANENT (or any) row for any of the 3


def test_401_then_fresh_run_with_fixed_credential_succeeds_immediately(conn):
    """Required test (section 3, run 2): fresh provider instance (a real
    day-2 run) simulating a fixed credential/config -> the SAME text is
    immediately retried for real and can succeed as TRANSLATED -- no stale
    cache row from run 1 (there wasn't one) blocks it."""
    from report.translation_anthropic import AnthropicTranslationProvider

    run1_provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    run1_provider._client = _FakeClient(lambda n: _status_error(401, "authentication_error"))
    first = translate_and_cache(conn, run1_provider, "Recoverable Headline")
    assert first["status"] == STATUS_UNAVAILABLE

    run2_provider = AnthropicTranslationProvider(api_key="sk-ant-fake-fixed-key")
    run2_provider._client = _FakeClient(lambda n: _FakeResponse("복구된 번역"))
    second = translate_and_cache(conn, run2_provider, "Recoverable Headline")
    assert second["status"] == STATUS_TRANSLATED
    assert second["translated_text"] == "복구된 번역"


def test_anthropic_client_constructed_with_max_retries_zero(monkeypatch):
    """Section 4: SUPER NEWS must be the single retry-policy owner -- the
    SDK's own default (max_retries=2, confirmed via anthropic 0.121.0's
    Anthropic.__init__ signature) must be overridden to 0 so one
    translate() call is exactly one real network attempt, never up to 3
    silently retried inside the SDK. Monkeypatches the real
    anthropic.Anthropic constructor (never calling the network) to capture
    the kwargs report.translation_anthropic actually passes it."""
    import report.translation_anthropic as translation_anthropic_module

    captured_kwargs = {}

    class _RecordingClient:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            self.messages = _FakeMessages(lambda n: _FakeResponse("결과"))

    monkeypatch.setattr(translation_anthropic_module.anthropic, "Anthropic", _RecordingClient)

    provider = translation_anthropic_module.AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider.translate("Trigger Real Client Construction", "ko")

    assert captured_kwargs.get("max_retries") == 0


def test_anthropic_connection_error_maps_to_transient(monkeypatch):
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider._client = _FakeClient(lambda n: _connection_error())

    with pytest.raises(TransientTranslationError):
        provider.translate("hello", "ko")


def test_anthropic_circuit_breaker_stops_further_network_calls_same_run(monkeypatch):
    """Required test 12: after ONE provider-wide transient failure within a
    run (one provider instance), further translate() calls for OTHER texts
    must not reach the network again -- they fail fast via the breaker."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    fake_client = _FakeClient(lambda n: _connection_error())
    provider._client = fake_client

    with pytest.raises(TransientTranslationError):
        provider.translate("Article One Headline", "ko")
    assert fake_client.messages.calls == 1

    with pytest.raises(TransientTranslationError):
        provider.translate("Article Two Headline", "ko")
    with pytest.raises(TransientTranslationError):
        provider.translate("Article Three Headline", "ko")

    assert fake_client.messages.calls == 1  # breaker fast-failed the rest, no new network calls


def test_anthropic_permanent_failure_does_not_trip_circuit_breaker(monkeypatch):
    """A single text's permanent (empty-output) failure must not be
    mistaken for a provider-wide outage -- the next, unrelated text must
    still reach the network normally."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    responses = iter([_FakeResponse(""), _FakeResponse("정상 번역 결과")])
    fake_client = _FakeClient(lambda n: next(responses))
    provider._client = fake_client

    with pytest.raises(ValueError):
        provider.translate("Empty Output Headline", "ko")

    result = provider.translate("Second Headline", "ko")
    assert result == "정상 번역 결과"
    assert fake_client.messages.calls == 2


def test_anthropic_provider_end_to_end_transient_then_retry_via_translate_and_cache(conn):
    """Wires the real AnthropicTranslationProvider (fake client, no
    network) through translate_and_cache to confirm the full stack agrees:
    transient failure -> bounded retry -> success upserts to TRANSLATED.
    Uses a FRESH provider instance for the retry, matching production
    (report.translation.build_translation_provider() constructs exactly
    one instance per report-generation run -- the circuit breaker is
    correctly per-instance/per-run, not per-text, so reusing the same
    instance across two simulated runs would incorrectly keep the breaker
    open; a real day-2 run always starts from a new instance)."""
    from report.translation_anthropic import AnthropicTranslationProvider

    provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    provider._client = _FakeClient(lambda n: _connection_error())

    clock = _clock(datetime(2026, 8, 14, tzinfo=timezone.utc))
    first = translate_and_cache(conn, provider, "End To End Headline", now_fn=clock)
    assert first["status"] == STATUS_FAILED
    assert first["failure_kind"] == FAILURE_KIND_TRANSIENT

    clock.advance(TRANSIENT_RETRY_BASE_SECONDS + 1)
    retry_provider = AnthropicTranslationProvider(api_key="sk-ant-fake-not-real")
    retry_provider._client = _FakeClient(lambda n: _FakeResponse("실제 번역"))
    second = translate_and_cache(conn, retry_provider, "End To End Headline", now_fn=clock)
    assert second["status"] == STATUS_TRANSLATED
    assert second["translated_text"] == "실제 번역"
    rows = conn.execute("SELECT COUNT(*) AS c FROM translation_cache").fetchone()
    assert rows["c"] == 1


# ---- secret exposure -----------------------------------------------------


def test_secret_value_never_in_cache_key_or_row(conn):
    """Required test 14: the cache_key/row never contains the credential
    VALUE, only provider_name (a class name) and model_name (a public
    model id)."""
    secret = "sk-ant-super-secret-value-12345"
    provider = FakeProvider(translations={"Some Headline": "일부 헤드라인"})
    provider._api_key = secret  # simulate a provider that happens to hold one
    translate_and_cache(conn, provider, "Some Headline")
    rows = conn.execute("SELECT cache_key, provider, original_text, translated_text FROM translation_cache").fetchall()
    for row in rows:
        for value in row:
            assert secret not in str(value)
