"""report.producer_orchestrator.run_daily_producer_intelligence: end-to-end
with an injected FakeLLM (never a live network/API call). Covers: no-
evidence short-circuit (zero LLM calls, no API key required), validation
failure on a FRESH LLM output (no persistence, no fabricated fallback),
validation failure on a REUSED output (no persistence either -- the
explicit requirement that reuse never skips validation), and the happy
path (persisted, readable back via report.web_data_v2)."""

import json

import pytest

from db.database import connect, init_db
from report.llm_interface import LLMResponse, StructuredLLM
from report.producer_orchestrator import run_daily_producer_intelligence
from report.web_data_v2 import build_dashboard_data_v2


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


class FakeLLM(StructuredLLM):
    def __init__(self, response=None):
        self.calls = 0
        self._response = response

    def generate_structured(self, system_prompt, user_prompt, schema):
        self.calls += 1
        return self._response


def _insert_spotify_riser(conn, report_date_kst="2026-08-13"):
    """Real Early Signal evidence: a track that jumped rank 10 -> 2."""
    day1 = "2026-08-12T00:00:00+00:00"
    day2 = f"{report_date_kst}T00:00:00+00:00"
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('Artist', 'Title', 'ORIGINAL', 'RESOLVED', ?, 'spotify_chart')""",
        (day1,),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_entity_aliases (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, 'SPOTIFY_ID', 'sp1', 'spotify_chart', ?)""",
        (entity_id, day1),
    )
    for rank, observed_at in ((10, day1), (2, day2)):
        conn.execute(
            """INSERT INTO music_observations
               (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
                unit, region, evidence_type, observed_at, collected_at)
               VALUES (?, NULL, 'spotify_chart', 'spotify_chart_rank', ?, 'chart_rank', 'GLOBAL',
                       'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
            (entity_id, rank, observed_at, observed_at),
        )
    conn.execute(
        """INSERT INTO derived_signals
           (music_entity_id, signal_type, value, unit, period_start, period_end, computed_at, method_version)
           VALUES (?, 'VELOCITY', 8, 'rank_positions', ?, ?, ?, 'v1')""",
        (entity_id, day1, day2, day2),
    )
    conn.commit()


# ---- no evidence: legitimate empty day, zero LLM calls ----------------------


def test_no_evidence_makes_zero_llm_calls_and_completes(conn):
    llm = FakeLLM()
    result = run_daily_producer_intelligence(conn, "run-1", "2026-08-13", llm=llm)
    assert result["status"] == "completed_no_evidence"
    assert llm.calls == 0


def test_no_evidence_persists_nothing_readable(conn):
    llm = FakeLLM()
    run_daily_producer_intelligence(conn, "run-1", "2026-08-13", llm=llm)
    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"] == {"state": "UNAVAILABLE", "insights": []}


# ---- validation failure on a FRESH output: no persistence, no fallback -----


def test_fresh_output_hallucinated_ref_fails_and_persists_nothing(conn):
    _insert_spotify_riser(conn)
    bad_insight = {
        "what_is_moving": "test something", "why_it_matters": "because",
        "what_to_watch": "x", "what_could_i_make_now": "y",
        "evidence_refs": ["E99"], "confidence": "LOW",
    }
    bad_response = LLMResponse(
        parsed={"insights": [bad_insight]},
        raw_text=json.dumps({"insights": [bad_insight]}),
        model_used="fake-model", input_tokens=1, output_tokens=1,
    )
    llm = FakeLLM(response=bad_response)
    result = run_daily_producer_intelligence(conn, "run-1", "2026-08-13", llm=llm)
    assert result["status"] == "failed"
    assert "E99" in result["reason"]

    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"] == {"state": "UNAVAILABLE", "insights": []}


# ---- happy path: fresh, valid, grounded insight gets persisted -------------


def _good_response(ref="E1"):
    insight = {
        "what_is_moving": "Multiple rising signals show fast entries",
        "why_it_matters": "multiple rising signals show fast entries",
        "what_to_watch": "whether the trend continues past the next observation",
        "what_could_i_make_now": "test a short hook-first intro next demo",
        "evidence_refs": [ref], "confidence": "MEDIUM",
    }
    text = json.dumps({"insights": [insight]})
    return LLMResponse(parsed=json.loads(text), raw_text=text, model_used="fake-model",
                        input_tokens=1, output_tokens=1)


def test_valid_fresh_output_is_persisted_and_readable(conn):
    _insert_spotify_riser(conn)
    llm = FakeLLM(response=_good_response())
    result = run_daily_producer_intelligence(conn, "run-1", "2026-08-13", llm=llm)
    assert result["status"] == "completed_with_insights"
    assert llm.calls == 1

    data = build_dashboard_data_v2(conn, "2026-08-13")
    assert data["producer_intelligence"]["state"] == "NORMAL"
    assert len(data["producer_intelligence"]["insights"]) == 1
    assert "hook-first" in data["producer_intelligence"]["insights"][0]["what_could_i_make_now"]


def test_persisted_output_carries_resolvable_evidence_summary_not_bare_ref(conn):
    """The orchestrator must persist the evidence catalog alongside the
    insights so a reader sees what 'E1' actually refers to, never a raw
    internal code."""
    _insert_spotify_riser(conn)
    llm = FakeLLM(response=_good_response())
    run_daily_producer_intelligence(conn, "run-1", "2026-08-13", llm=llm)

    data = build_dashboard_data_v2(conn, "2026-08-13")
    evidence = data["producer_intelligence"]["insights"][0]["evidence"]
    assert evidence[0]["ref"] == "E1"
    assert "Artist - Title" in evidence[0]["summary"]
    assert evidence[0]["summary"] != "E1"  # not a bare-ref fallback


# ---- validation ALSO runs on a REUSED output, not just a fresh one ---------


def test_reused_output_is_also_validated_and_persisted(conn):
    """Day 1: fresh valid call, persisted. Day 2: identical evidence -> the
    synthesis layer reuses day 1's output with zero new LLM calls, but the
    orchestrator must STILL run validate_producer_insights on it before
    persisting today's row -- this proves that path actually executes
    (not just documented) by checking day 2 also ends up readable."""
    _insert_spotify_riser(conn, report_date_kst="2026-08-13")
    llm = FakeLLM(response=_good_response())
    result_day1 = run_daily_producer_intelligence(conn, "run-day1", "2026-08-13", llm=llm)
    assert result_day1["status"] == "completed_with_insights"
    assert llm.calls == 1

    # Day 2: same evidence pattern (same entity, same rank_delta) recreated
    # for a later date so build_dashboard_data_v2 resolves a run for it.
    _insert_spotify_riser_day2_identical_evidence(conn)

    result_day2 = run_daily_producer_intelligence(conn, "run-day2", "2026-08-14", llm=llm)
    assert result_day2["status"] == "completed_reused"
    assert llm.calls == 1  # no new LLM call

    data_day2 = build_dashboard_data_v2(conn, "2026-08-14")
    assert data_day2["producer_intelligence"]["state"] == "NORMAL"


def _insert_spotify_riser_day2_identical_evidence(conn):
    """A second, independently-tracked entity whose Early Signal evidence
    summary is byte-identical to the day-1 entity's ('Artist' / 'Title' /
    +8), so build_evidence_catalog produces the SAME catalog on 2026-08-14
    -- the scenario compute_input_hash's date-independence is meant for."""
    day1 = "2026-08-13T00:00:00+00:00"
    day2 = "2026-08-14T00:00:00+00:00"
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('Artist', 'Title', 'ORIGINAL', 'RESOLVED', ?, 'spotify_chart')""",
        (day1,),
    )
    entity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO music_entity_aliases (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, 'SPOTIFY_ID', 'sp2', 'spotify_chart', ?)""",
        (entity_id, day1),
    )
    for rank, observed_at in ((10, day1), (2, day2)):
        conn.execute(
            """INSERT INTO music_observations
               (music_entity_id, raw_item_id, source_name, metric_name, metric_value,
                unit, region, evidence_type, observed_at, collected_at)
               VALUES (?, NULL, 'spotify_chart', 'spotify_chart_rank', ?, 'chart_rank', 'GLOBAL',
                       'MEASURED_PLATFORM_SIGNAL', ?, ?)""",
            (entity_id, rank, observed_at, observed_at),
        )
    conn.execute(
        """INSERT INTO derived_signals
           (music_entity_id, signal_type, value, unit, period_start, period_end, computed_at, method_version)
           VALUES (?, 'VELOCITY', 8, 'rank_positions', ?, ?, ?, 'v1')""",
        (entity_id, day1, day2, day2),
    )
    conn.commit()


def test_reused_output_with_hallucinated_ref_still_fails_validation(conn):
    """If a BAD row somehow already exists under an input_hash that a later
    day's evidence would also hash to, reuse must not bypass validation --
    the orchestrator revalidates every reused parse just like a fresh one."""
    from report.producer_synthesis import build_evidence_catalog, compute_input_hash, CATEGORY

    _insert_spotify_riser(conn, report_date_kst="2026-08-13")
    intelligence = build_dashboard_data_v2(conn, "2026-08-13")["intelligence"]
    spotify_chart = build_dashboard_data_v2(conn, "2026-08-13")["spotify_chart"]
    catalog = build_evidence_catalog(intelligence, spotify_chart, [])
    bad_hash = compute_input_hash("v1", catalog)

    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('r-bad', '2026-08-12', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    bad_output_text = json.dumps({"insights": [{
        "what_is_moving": "x", "why_it_matters": "y", "what_to_watch": "z", "what_could_i_make_now": "w",
        "evidence_refs": ["E99"], "confidence": "LOW",
    }]})
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, 'm', 'v1', ?, ?, 'MEDIUM', 'x')""",
        (run_row_id, CATEGORY, bad_hash, bad_output_text),
    )
    conn.commit()

    llm = FakeLLM(response=_good_response())  # would never actually be called (reuse hits first)
    result = run_daily_producer_intelligence(conn, "run-2", "2026-08-13", llm=llm)
    assert result["status"] == "failed"
    assert llm.calls == 0  # reuse path -- confirms this exercised the REUSED branch, not fresh
