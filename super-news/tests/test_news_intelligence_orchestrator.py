"""report.news_intelligence_orchestrator.run_daily_news_intelligence:
no-eligible-items short-circuit (zero LLM calls, no API key required),
synthesis-failure isolation (failed run, never raises), zero-items-passed-
validation isolation, and the happy path (persisted, readable back via
report.web_data_v2). Mocks report.news_intelligence_orchestrator.
build_dashboard_data_v2 (allowed in tests only, per project test policy) to
control eligible items directly rather than building full realistic V2
ingestion fixtures -- this module's own dashboard-read boundary is already
covered elsewhere; this file is scoped to orchestration/failure-isolation
behavior."""

import json

import pytest

import report.news_intelligence_orchestrator as nio
from db.database import connect, init_db
from report.llm_interface import LLMResponse, StructuredLLM
from report.news_intelligence_orchestrator import run_daily_news_intelligence


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


class FakeLLM(StructuredLLM):
    def __init__(self, response=None, raise_exc=None):
        self.calls = 0
        self._response = response
        self._raise_exc = raise_exc

    def generate_structured(self, system_prompt, user_prompt, schema):
        self.calls += 1
        if self._raise_exc:
            raise self._raise_exc
        return self._response


EMPTY_DASHBOARD = {"news": {"AI": {"items": []}, "ECONOMY": {"items": []}, "SOCIETY": {"items": []}}}


def _dashboard_with_items(items_by_category):
    news = {"AI": {"items": []}, "ECONOMY": {"items": []}, "SOCIETY": {"items": []}}
    news.update(items_by_category)
    return {"news": news}


def _item(item_id, tier="LEAD", title="Some Title", snippet="Some snippet."):
    return {"id": item_id, "tier": tier, "title": title, "snippet": snippet, "source_count": 2}


def _valid_entry(item_id):
    return {
        "id": item_id,
        "what_happened": "실제로 있었던 일에 대한 구체적인 사실 진술이다.",
        "why_it_matters": "주어진 근거로부터 도출된 합리적인 함의다.",
        "what_to_watch": "다음 데이터가 이 흐름을 확인해줄지 지켜볼 필요가 있다.",
    }


def _valid_response(item_ids):
    parsed = {"items": [_valid_entry(i) for i in item_ids]}
    return LLMResponse(
        parsed=parsed, raw_text=json.dumps(parsed, ensure_ascii=False),
        model_used="fake-model", input_tokens=10, output_tokens=5,
    )


# ---- no eligible items: legitimate empty day, zero LLM calls --------------


def test_no_eligible_items_zero_llm_calls(conn, monkeypatch):
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: EMPTY_DASHBOARD)
    llm = FakeLLM()
    result = run_daily_news_intelligence(conn, "run-1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_no_evidence"
    assert llm.calls == 0


def test_brief_tier_items_are_not_eligible(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1, tier="BRIEF")]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM()
    result = run_daily_news_intelligence(conn, "run-1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_no_evidence"
    assert llm.calls == 0


def test_tiktok_spotify_excluded_even_if_present(conn, monkeypatch):
    dashboard = {
        "news": {
            "AI": {"items": []}, "ECONOMY": {"items": []}, "SOCIETY": {"items": []},
            "TIKTOK": {"items": [_item(1)]}, "SPOTIFY": {"items": [_item(2)]},
        }
    }
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM()
    result = run_daily_news_intelligence(conn, "run-1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_no_evidence"
    assert llm.calls == 0


# ---- synthesis failure: isolated, never crashes, never persists -----------


def test_synthesis_exception_is_isolated_failed_status(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(raise_exc=RuntimeError("network down"))
    result = run_daily_news_intelligence(conn, "run-2", "2026-08-14", llm=llm)
    assert result["status"] == "failed"
    row = conn.execute("SELECT COUNT(*) AS c FROM llm_interpretations").fetchone()
    assert row["c"] == 0


# ---- zero items passing validation: failed, nothing persisted -------------


def test_zero_validated_items_fails_and_persists_nothing(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    bad_parsed = {"items": [{"id": 1, "what_happened": "", "why_it_matters": "x", "what_to_watch": "y"}]}
    bad_response = LLMResponse(
        parsed=bad_parsed, raw_text=json.dumps(bad_parsed), model_used="fake-model",
        input_tokens=1, output_tokens=1,
    )
    llm = FakeLLM(response=bad_response)
    result = run_daily_news_intelligence(conn, "run-3", "2026-08-14", llm=llm)
    assert result["status"] == "failed"
    row = conn.execute("SELECT COUNT(*) AS c FROM llm_interpretations").fetchone()
    assert row["c"] == 0


# ---- happy path: persisted, readable back --------------------------------


def test_happy_path_persists_and_readable(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))
    result = run_daily_news_intelligence(conn, "run-4", "2026-08-14", llm=llm)
    assert result["status"] == "completed_with_insights"
    row = conn.execute(
        "SELECT output_text FROM llm_interpretations WHERE category = 'NEWS_INTELLIGENCE_V2'"
    ).fetchone()
    assert row is not None
    parsed = json.loads(row["output_text"])
    assert len(parsed["items"]) == 1


# ---- Phase 3C: LEAD-only production-pilot policy --------------------------


def test_standard_tier_items_are_not_eligible(conn, monkeypatch):
    """STANDARD used to be eligible (pre-Phase-3C); the production pilot
    narrows this to LEAD only -- a STANDARD-only day is a legitimate
    no-evidence day, zero LLM calls, same as an empty/BRIEF-only day."""
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1, tier="STANDARD")]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM()
    result = run_daily_news_intelligence(conn, "run-5", "2026-08-14", llm=llm)
    assert result["status"] == "completed_no_evidence"
    assert llm.calls == 0


def test_lead_and_standard_mixed_only_lead_synthesized(conn, monkeypatch):
    dashboard = _dashboard_with_items(
        {"AI": {"items": [_item(1, tier="LEAD"), _item(2, tier="STANDARD")]}}
    )
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))
    result = run_daily_news_intelligence(conn, "run-6", "2026-08-14", llm=llm)
    assert result["status"] == "completed_with_insights"
    row = conn.execute(
        "SELECT output_text FROM llm_interpretations WHERE category = 'NEWS_INTELLIGENCE_V2'"
    ).fetchone()
    parsed = json.loads(row["output_text"])
    assert [entry["id"] for entry in parsed["items"]] == [1]


def test_max_synthesis_items_per_run_truncates_deterministically():
    from report.news_intelligence_orchestrator import MAX_SYNTHESIS_ITEMS_PER_RUN, _collect_eligible_items

    news = {
        category: {"items": [_item(i, tier="LEAD") for i in range(MAX_SYNTHESIS_ITEMS_PER_RUN)]}
        for category in ("AI", "ECONOMY", "SOCIETY")
    }
    result = _collect_eligible_items({"news": news})
    assert len(result) == MAX_SYNTHESIS_ITEMS_PER_RUN


# ---- Phase 3C.1: REUSE = NO LLM + NO DUPLICATE PERSISTENCE -----------------


def test_A_reused_second_run_zero_llm_calls_and_row_count_stays_one(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))

    first = run_daily_news_intelligence(conn, "run-a1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_with_insights"
    assert llm.calls == 1
    row_count_after_first = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count_after_first == 1

    second = run_daily_news_intelligence(conn, "run-a2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_reused"
    assert llm.calls == 1  # no second LLM call
    row_count_after_second = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count_after_second == 1  # still exactly one -- no duplicate persisted

    # execution history (runs table) still records the second invocation --
    # execution history != intelligence content duplication.
    runs_count = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
    assert runs_count == 2
    second_run_row = conn.execute("SELECT status FROM runs WHERE run_id='run-a2'").fetchone()
    assert second_run_row["status"] == "completed"


def test_B_changed_input_hash_generates_real_new_synthesis(conn, monkeypatch):
    dashboard1 = _dashboard_with_items({"AI": {"items": [_item(1, title="Original Title")]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard1)
    llm = FakeLLM(response=_valid_response([1]))
    first = run_daily_news_intelligence(conn, "run-b1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_with_insights"
    assert llm.calls == 1

    dashboard2 = _dashboard_with_items({"AI": {"items": [_item(1, title="Materially Different Title")]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard2)
    second = run_daily_news_intelligence(conn, "run-b2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_with_insights"  # real new synthesis, not reused
    assert llm.calls == 2

    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2  # a genuinely different input is real new content, correctly persisted


def test_C_changed_model_hint_does_not_wrongly_reuse(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))

    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    first = run_daily_news_intelligence(conn, "run-c1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_with_insights"
    assert llm.calls == 1

    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    second = run_daily_news_intelligence(conn, "run-c2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_with_insights"  # a real new call, not a wrong reuse
    assert llm.calls == 2

    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2


def test_D_reused_output_still_passes_validation_and_is_readable(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))
    run_daily_news_intelligence(conn, "run-d1", "2026-08-14", llm=llm)

    second = run_daily_news_intelligence(conn, "run-d2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_reused"  # implies validate_news_intelligence passed on reuse too

    row = conn.execute(
        "SELECT output_text FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()
    parsed = json.loads(row["output_text"])
    assert [entry["id"] for entry in parsed["items"]] == [1]


def test_E_dashboard_read_back_correct_after_skipped_persist_on_reuse(conn, monkeypatch):
    """report.web_data_v2._attach_news_intelligence must still resolve the
    ORIGINAL (first-run) row correctly, even though the second (reused) run
    never persisted anything of its own -- it reads by report_date_kst via
    a runs.run_date JOIN, not by "latest run_id", so this must hold."""
    from report.web_data_v2 import _attach_news_intelligence

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))
    run_daily_news_intelligence(conn, "run-e1", "2026-08-14", llm=llm)
    run_daily_news_intelligence(conn, "run-e2", "2026-08-14", llm=llm)  # reused, skips persist

    items = [{"id": 1, "title": "Some Title", "snippet": "Some snippet."}]
    result = _attach_news_intelligence(conn, "2026-08-14", items)
    assert result[0]["ai_intelligence_status"] == "AVAILABLE"
    assert result[0]["what_happened"] == "실제로 있었던 일에 대한 구체적인 사실 진술이다."


def test_F_preexisting_malformed_row_is_not_silently_trusted(conn, monkeypatch):
    """A row that happens to share an input_hash but whose content is
    malformed (e.g. from a hypothetical future validation-rule change, or
    corruption) must never be silently displayed as a successful reuse.

    UPDATED (Phase 3C.2, poisoned-cache recovery): a malformed row must no
    longer permanently poison this input_hash -- validate_news_intelligence
    now runs INSIDE the reuse search itself (report.news_intelligence_
    synthesis._find_valid_reusable_interpretation), so a malformed
    candidate is skipped (not "found but distrusted after the fact" the
    way the pre-3C.2 orchestrator-only check worked), and a real fresh
    synthesis attempt is allowed. Superseded by
    tests/test_news_intelligence_synthesis.py's own Phase 3C.2 B/D/E tests
    for the full poisoned-cache contract; kept here (updated in place, not
    deleted) as the original regression coverage for "never silently
    trusted," now proven via a real recovery instead of a permanent
    failure."""
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)

    items = [_item(1)]
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", items)
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('run-f0', '2026-08-14', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    malformed = {"items": [{"id": 1, "what_happened": "", "why_it_matters": "x", "what_to_watch": "y"}]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, 'NEWS_INTELLIGENCE_V2', 'claude-opus-5', 'v1', ?, ?, 'MEDIUM', 'x')""",
        (run_row_id, input_hash, json.dumps(malformed)),
    )
    conn.commit()

    llm = FakeLLM(response=_valid_response([1]))
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    result = run_daily_news_intelligence(conn, "run-f1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_with_insights"  # real recovery, not a permanent failure
    assert llm.calls == 1  # the malformed row was correctly never trusted -> one real fresh attempt
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2  # the untouched original malformed row + one new valid row (never deleted/updated)


# ---- Phase 3C.2: poisoned-cache recovery -----------------------------------


def _insert_llm_interpretation_row(conn, input_hash, output_dict, run_id, model_used="claude-opus-5",
                                    prompt_version="v1", created_at="x"):
    """Inserts one raw llm_interpretations row directly -- simulates a real
    historical row (valid or malformed) already sitting in the table,
    without going through synthesis/persist_news_intelligence. Never
    deletes/updates an existing row -- every test in this section that
    seeds multiple rows this way is exercising the real production
    constraint that historical rows are immutable (Phase 3C.2 section 3)."""
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, '2026-08-14', 'x', 'completed')",
        (run_id,),
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, 'NEWS_INTELLIGENCE_V2', ?, ?, ?, ?, 'MEDIUM', ?)""",
        (run_row_id, model_used, prompt_version, input_hash, json.dumps(output_dict), created_at),
    )
    conn.commit()
    return run_row_id


_MALFORMED_OUTPUT = {"items": [{"id": 1, "what_happened": "", "why_it_matters": "x", "what_to_watch": "y"}]}


def _valid_output(item_id, marker=""):
    return {"items": [dict(_valid_entry(item_id), what_happened=f"실제로 있었던 일이다{marker}.")]}


def test_3C2_A_valid_existing_row_reused_zero_llm_row_count_unchanged(conn, monkeypatch):
    """Required test A -- unchanged from Phase 3C.1's own test_A, re-proven
    under the new validity-aware reuse search: a genuinely VALID existing
    row is still a normal, zero-cost cache hit."""
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", [_item(1)])
    _insert_llm_interpretation_row(conn, input_hash, _valid_output(1), "run-3c2a0")

    llm = FakeLLM(response=_valid_response([1]))
    result = run_daily_news_intelligence(conn, "run-3c2a1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_reused"
    assert llm.calls == 0
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 1  # unchanged -- the pre-seeded valid row, no duplicate


def test_3C2_C_newer_malformed_older_valid_finds_older_row(conn, monkeypatch):
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", [_item(1)])
    # Insert order matters: the OLDER row (lower id) is inserted first and
    # is VALID; the NEWER row (higher id) is inserted second and is
    # MALFORMED -- proves the search doesn't just stop at whichever row it
    # happens to see first if that's not actually the newest by id.
    _insert_llm_interpretation_row(conn, input_hash, _valid_output(1, " (older, valid)"), "run-3c2c-old")
    _insert_llm_interpretation_row(conn, input_hash, _MALFORMED_OUTPUT, "run-3c2c-new")

    llm = FakeLLM(response=_valid_response([1]))
    result = run_daily_news_intelligence(conn, "run-3c2c1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_reused"
    assert llm.calls == 0  # the older valid row was found -- the newer malformed row never blocked it
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2  # both historical rows untouched, no new row added


def test_3C2_D_all_matching_historical_rows_malformed_fresh_synthesis_exactly_once(conn, monkeypatch):
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", [_item(1)])
    _insert_llm_interpretation_row(conn, input_hash, _MALFORMED_OUTPUT, "run-3c2d-old")
    _insert_llm_interpretation_row(conn, input_hash, _MALFORMED_OUTPUT, "run-3c2d-new")

    llm = FakeLLM(response=_valid_response([1]))
    result = run_daily_news_intelligence(conn, "run-3c2d1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_with_insights"
    assert llm.calls == 1  # exactly one fresh attempt, not one per malformed row found
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 3  # 2 untouched malformed rows + 1 new valid row


def test_3C2_E_fresh_synthesis_also_malformed_fails_safely_and_remains_retryable(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    bad_response = LLMResponse(
        parsed=_MALFORMED_OUTPUT, raw_text=json.dumps(_MALFORMED_OUTPUT),
        model_used="fake-model", input_tokens=1, output_tokens=1,
    )
    llm = FakeLLM(response=bad_response)

    first = run_daily_news_intelligence(conn, "run-3c2e1", "2026-08-14", llm=llm)
    assert first["status"] == "failed"
    assert llm.calls == 1
    row_count_after_first = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count_after_first == 0  # malformed fresh output never persisted as reusable poison

    # A later run (e.g. once real evidence/conditions change, or simply
    # retried) must still be allowed to attempt synthesis again -- not
    # permanently blocked by the first failure.
    llm.calls = 0
    good_response = _valid_response([1])
    llm._response = good_response
    second = run_daily_news_intelligence(conn, "run-3c2e2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_with_insights"
    assert llm.calls == 1
    row_count_after_second = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count_after_second == 1


def test_3C2_F_third_run_after_recovery_reuses_new_valid_row_zero_llm(conn, monkeypatch):
    """After a malformed-row recovery (Phase 3C.2's test_F-updated
    scenario above), a THIRD run for the same unchanged input must reuse
    the NEW valid row with zero LLM calls and zero further duplication --
    the recovery itself must not become a fresh poisoned/duplicating
    state."""
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", [_item(1)])
    _insert_llm_interpretation_row(conn, input_hash, _MALFORMED_OUTPUT, "run-3c2f-old")

    llm = FakeLLM(response=_valid_response([1]))
    recovery = run_daily_news_intelligence(conn, "run-3c2f1", "2026-08-14", llm=llm)
    assert recovery["status"] == "completed_with_insights"
    assert llm.calls == 1

    third = run_daily_news_intelligence(conn, "run-3c2f2", "2026-08-14", llm=llm)
    assert third["status"] == "completed_reused"
    assert llm.calls == 1  # no additional call
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2  # original malformed row + the one recovered valid row -- no further duplicate


# ---- Phase 3C.3: completeness contract (PARTIAL != COMPLETE cache hit) ----


def test_3C3_A_complete_output_success_persisted_and_reused(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1), _item(2), _item(3)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1, 2, 3]))

    first = run_daily_news_intelligence(conn, "run-3c3a1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_with_insights"
    assert llm.calls == 1
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 1

    second = run_daily_news_intelligence(conn, "run-3c3a2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_reused"
    assert llm.calls == 1  # zero additional calls
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 1


def test_3C3_B_partial_output_not_terminal_next_run_retries(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1), _item(2), _item(3)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1, 2]))  # id 3 missing -- partial

    first = run_daily_news_intelligence(conn, "run-3c3b1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_partial"
    assert llm.calls == 1
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 1  # still persisted/displayable today, just not cache-terminal

    # Real news preservation: item 3's real title/source/snippet are never
    # hidden by the partial AI layer -- re-verified via the real read-back.
    from report.web_data_v2 import _attach_news_intelligence
    items = [{"id": 1, "title": "T1"}, {"id": 2, "title": "T2"}, {"id": 3, "title": "T3"}]
    attached = _attach_news_intelligence(conn, "2026-08-14", items)
    by_id = {i["id"]: i for i in attached}
    assert by_id[1]["ai_intelligence_status"] == "AVAILABLE"
    assert by_id[2]["ai_intelligence_status"] == "AVAILABLE"
    assert by_id[3]["ai_intelligence_status"] == "UNAVAILABLE"
    assert by_id[3]["title"] == "T3"  # real news, never hidden

    # A partial row must NOT be a permanently reusable terminal success --
    # the next run for the SAME unchanged input gets a real fresh retry.
    second = run_daily_news_intelligence(conn, "run-3c3b2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_partial"  # still partial (same FakeLLM response)
    assert llm.calls == 2  # a real second attempt was made -- never silently reused


def test_3C3_C_newer_partial_older_complete_reuses_older(conn, monkeypatch):
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1), _item(2), _item(3)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    items = [_item(1), _item(2), _item(3)]
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", items)
    complete_output = {"items": [_valid_entry(1), _valid_entry(2), _valid_entry(3)]}
    partial_output = {"items": [_valid_entry(1), _valid_entry(2)]}
    _insert_llm_interpretation_row(conn, input_hash, complete_output, "run-3c3c-old")
    _insert_llm_interpretation_row(conn, input_hash, partial_output, "run-3c3c-new")

    llm = FakeLLM(response=_valid_response([1, 2, 3]))
    result = run_daily_news_intelligence(conn, "run-3c3c1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_reused"
    assert llm.calls == 0  # the older COMPLETE row was found -- the newer partial one never blocked it
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2  # both historical rows untouched, no new row added


def test_3C3_D_all_historical_rows_partial_or_malformed_fresh_synthesis_once(conn, monkeypatch):
    from report.news_intelligence_synthesis import compute_input_hash

    dashboard = _dashboard_with_items({"AI": {"items": [_item(1), _item(2), _item(3)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    items = [_item(1), _item(2), _item(3)]
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", items)
    partial_output = {"items": [_valid_entry(1), _valid_entry(2)]}
    _insert_llm_interpretation_row(conn, input_hash, partial_output, "run-3c3d-partial")
    _insert_llm_interpretation_row(conn, input_hash, _MALFORMED_OUTPUT, "run-3c3d-malformed")

    llm = FakeLLM(response=_valid_response([1, 2, 3]))
    result = run_daily_news_intelligence(conn, "run-3c3d1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_with_insights"
    assert llm.calls == 1  # exactly one fresh attempt
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 3  # 2 untouched historical rows + 1 new complete row


def test_3C3_E_partial_then_complete_then_reused(conn, monkeypatch):
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1), _item(2), _item(3)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1, 2]))  # partial

    first = run_daily_news_intelligence(conn, "run-3c3e1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_partial"
    assert llm.calls == 1

    llm._response = _valid_response([1, 2, 3])  # the "model" now completes the set
    second = run_daily_news_intelligence(conn, "run-3c3e2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_with_insights"
    assert llm.calls == 2

    third = run_daily_news_intelligence(conn, "run-3c3e3", "2026-08-14", llm=llm)
    assert third["status"] == "completed_reused"
    assert llm.calls == 2  # zero additional calls -- the complete row is now the reusable one
    row_count = conn.execute(
        "SELECT COUNT(*) AS c FROM llm_interpretations WHERE category='NEWS_INTELLIGENCE_V2'"
    ).fetchone()["c"]
    assert row_count == 2  # the original partial row (untouched) + the one complete row


def test_3C3_F_duplicate_id_in_output_correctly_counted_as_incomplete(conn, monkeypatch):
    """A duplicate id in the raw output is already rejected by
    validate_news_intelligence (Phase 3C's own contract, unchanged) -- this
    proves that rejection correctly interacts with the NEW completeness
    check: a duplicate for id 1 plus valid entries for 2/3 must NOT be
    miscounted as complete just because 3 entries were returned."""
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1), _item(2), _item(3)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    dup_output = {"items": [_valid_entry(1), _valid_entry(1), _valid_entry(2), _valid_entry(3)]}
    llm = FakeLLM(response=LLMResponse(
        parsed=dup_output, raw_text=json.dumps(dup_output),
        model_used="fake-model", input_tokens=1, output_tokens=1,
    ))
    result = run_daily_news_intelligence(conn, "run-3c3f1", "2026-08-14", llm=llm)
    assert result["status"] == "completed_partial"  # id 1 excluded (duplicate) -> only {2,3} validated, not {1,2,3}


def test_3C3_G_single_item_day_still_complete_and_reusable(conn, monkeypatch):
    """A single-LEAD-item day (today's real production shape for a
    category with exactly one LEAD item) must not be treated as
    structurally "partial" just because it's a small set."""
    dashboard = _dashboard_with_items({"AI": {"items": [_item(1)]}})
    monkeypatch.setattr(nio, "build_dashboard_data_v2", lambda c, d: dashboard)
    llm = FakeLLM(response=_valid_response([1]))

    first = run_daily_news_intelligence(conn, "run-3c3g1", "2026-08-14", llm=llm)
    assert first["status"] == "completed_with_insights"
    assert llm.calls == 1

    second = run_daily_news_intelligence(conn, "run-3c3g2", "2026-08-14", llm=llm)
    assert second["status"] == "completed_reused"
    assert llm.calls == 1
