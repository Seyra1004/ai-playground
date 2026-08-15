"""report.producer_synthesis: evidence catalog construction (dedup,
determinism), date-independent input hashing, category-scoped reuse,
zero-evidence short-circuit. Uses a FakeLLM -- never a live network/API
call."""

import pytest

from db.database import connect, init_db
from report.llm_interface import LLMResponse, StructuredLLM
from report.producer_synthesis import (
    CATEGORY,
    MAX_INSIGHTS,
    build_evidence_catalog,
    compute_input_hash,
    find_reusable_interpretation,
    synthesize_producer_intelligence,
)


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
        self._response = response or LLMResponse(
            parsed={"insights": []}, raw_text='{"insights": []}',
            model_used="fake-model", input_tokens=10, output_tokens=5,
        )

    def generate_structured(self, system_prompt, user_prompt, schema):
        self.calls += 1
        return self._response


EMPTY_INTELLIGENCE = {"early_signal": {}, "catalog_revival": {}, "cross_platform": [], "outlook": {}}
UNAVAILABLE_SPOTIFY = {"state": "UNAVAILABLE", "top10": [], "new_entries": [], "trend": None}


def _early_signal_intelligence():
    return {
        "early_signal": {"spotify_chart": [
            {"source_name": "spotify_chart", "music_entity_id": 1, "canonical_artist": "Artist",
             "canonical_title": "Title", "rank_delta": 8.0}
        ]},
        "catalog_revival": {}, "cross_platform": [], "outlook": {},
    }


# ---- build_evidence_catalog: never fabricates, dedupes industry news -------


def test_empty_evidence_produces_empty_catalog():
    assert build_evidence_catalog(EMPTY_INTELLIGENCE, UNAVAILABLE_SPOTIFY, []) == []


def test_early_signal_produces_one_evidence_entry():
    catalog = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    assert len(catalog) == 1
    assert catalog[0]["ref"] == "E1"
    assert catalog[0]["type"] == "EARLY_SIGNAL"
    assert "Artist - Title" in catalog[0]["summary"]


def test_industry_news_cites_headline_only_never_reason_or_snippet():
    """Fact-ownership rule: reason/snippet are the Music Industry section's
    OWN fact (context / why-it-matters) -- Producer Intelligence cites the
    identifying headline only, never re-explains that fact itself."""
    industry_news = [{"title": "Spotify launches new tool", "reason": "affects producers",
                       "snippet": "The tool lets artists see regional breakdowns"}]
    catalog = build_evidence_catalog(EMPTY_INTELLIGENCE, UNAVAILABLE_SPOTIFY, industry_news)
    assert len(catalog) == 1
    assert catalog[0]["summary"] == "Spotify launches new tool"
    assert "regional breakdowns" not in catalog[0]["summary"]
    assert "affects producers" not in catalog[0]["summary"]


def test_industry_news_without_title_is_skipped():
    industry_news = [{"title": None, "reason": "x", "snippet": "y"}]
    catalog = build_evidence_catalog(EMPTY_INTELLIGENCE, UNAVAILABLE_SPOTIFY, industry_news)
    assert catalog == []


def test_tiktok_never_fabricated_no_tiktok_evidence_type():
    catalog = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    assert all("TIKTOK" not in e["type"] for e in catalog)


def test_catalog_ref_assignment_is_deterministic():
    c1 = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    c2 = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    assert c1 == c2


# ---- compute_input_hash: date-independent -----------------------------------


def test_hash_deterministic_across_repeated_calls():
    catalog = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    assert compute_input_hash("v1", catalog) == compute_input_hash("v1", catalog)


def test_hash_sensitive_to_catalog_content_change():
    c1 = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    intelligence2 = _early_signal_intelligence()
    intelligence2["early_signal"]["spotify_chart"][0]["rank_delta"] = 20.0
    c2 = build_evidence_catalog(intelligence2, UNAVAILABLE_SPOTIFY, [])
    assert compute_input_hash("v1", c1) != compute_input_hash("v1", c2)


def test_hash_sensitive_to_prompt_version():
    catalog = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    assert compute_input_hash("v1", catalog) != compute_input_hash("v2", catalog)


def test_hash_is_date_independent(conn):
    """The core V2.1 cost-control fix: an identical evidence catalog on a
    LATER calendar day must hash identically -- report_date_kst plays no
    part in synthesis identity."""
    catalog = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    h_day1 = compute_input_hash("v1", catalog)
    # compute_input_hash doesn't take a date param at all -- this is the
    # structural guarantee, not just a coincidental equal value.
    import inspect
    assert list(inspect.signature(compute_input_hash).parameters) == ["prompt_version", "catalog"]


# ---- synthesize_producer_intelligence: zero-call gate + reuse ---------------


def test_zero_evidence_makes_no_llm_call(conn):
    llm = FakeLLM()
    result = synthesize_producer_intelligence(conn, llm, EMPTY_INTELLIGENCE, UNAVAILABLE_SPOTIFY, [], "2026-08-13")
    assert result is None
    assert llm.calls == 0


def test_fresh_call_hits_llm_and_is_marked_not_reused(conn):
    llm = FakeLLM()
    result = synthesize_producer_intelligence(
        conn, llm, _early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [], "2026-08-13"
    )
    assert llm.calls == 1
    assert result["reused"] is False
    assert result["parsed"] == {"insights": []}


def test_reuse_scoped_to_producer_intelligence_category_only(conn):
    """A NEWS_COMBINED row with the SAME input_hash string must never be
    reused as Producer Intelligence output -- find_reusable_interpretation
    filters by category, not just input_hash."""
    catalog = build_evidence_catalog(_early_signal_intelligence(), UNAVAILABLE_SPOTIFY, [])
    fake_hash = compute_input_hash("v1", catalog)
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('r1', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, 'NEWS_COMBINED', 'm', 'v1', ?, '{"AI":[]}', 'MEDIUM', 'x')""",
        (run_row_id, fake_hash),
    )
    conn.commit()
    assert find_reusable_interpretation(conn, fake_hash) is None
    assert CATEGORY == "MUSIC_PRODUCER_INTELLIGENCE"


def test_identical_catalog_on_later_day_reuses_with_zero_new_llm_calls(conn):
    llm = FakeLLM()
    intelligence = _early_signal_intelligence()

    result_day1 = synthesize_producer_intelligence(conn, llm, intelligence, UNAVAILABLE_SPOTIFY, [], "2026-08-13")
    assert llm.calls == 1
    assert result_day1["reused"] is False

    # Simulate persistence exactly as report.persistence.persist_producer_intelligence would.
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('r-day1', '2026-08-13', 'x', 'completed')"
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'MEDIUM', 'x')""",
        (run_row_id, CATEGORY, result_day1["model_used"], result_day1["prompt_version"],
         result_day1["input_hash"], result_day1["output_text"]),
    )
    conn.commit()

    # SAME evidence catalog, but a LATER date -- must reuse, zero new calls.
    result_day2 = synthesize_producer_intelligence(conn, llm, intelligence, UNAVAILABLE_SPOTIFY, [], "2026-08-14")
    assert llm.calls == 1  # unchanged
    assert result_day2["reused"] is True
    assert result_day2["output_text"] == result_day1["output_text"]


def test_max_insights_constant_is_five():
    assert MAX_INSIGHTS == 5
