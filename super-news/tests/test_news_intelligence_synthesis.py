"""report.news_intelligence_synthesis: structured-output validation
(non-empty/length/no-HTML/no-verbatim-copy, per-item isolation), input
hashing + category-scoped reuse (model-hint sensitive), zero-items
short-circuit, persistence. Uses a FakeLLM -- never a live network/API
call."""

import json

import pytest

from db.database import connect, init_db
from report.llm_interface import LLMResponse, StructuredLLM
from report.news_intelligence_synthesis import (
    CATEGORY,
    MAX_ITEMS_PER_CALL,
    _SCHEMA,
    compute_input_hash,
    find_reusable_interpretation,
    persist_news_intelligence,
    synthesize_news_intelligence,
    validate_news_intelligence,
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
            parsed={"items": []}, raw_text='{"items": []}',
            model_used="fake-model", input_tokens=10, output_tokens=5,
        )

    def generate_structured(self, system_prompt, user_prompt, schema):
        self.calls += 1
        return self._response


def _items():
    return [
        {"id": 1, "title": "Fed Holds Rates Steady", "snippet": "The Fed kept rates unchanged.", "source_count": 3},
        {"id": 2, "title": "New AI Model Released", "snippet": "A major lab shipped a new model.", "source_count": 2},
    ]


def _valid_entry(item_id):
    return {
        "id": item_id,
        "what_happened": "A concrete factual statement about what occurred.",
        "why_it_matters": "A grounded implication drawn from the given evidence.",
        "what_to_watch": "Whether the next data point confirms this trend.",
    }


def _valid_response(item_ids):
    parsed = {"items": [_valid_entry(i) for i in item_ids]}
    return LLMResponse(
        parsed=parsed, raw_text=json.dumps(parsed, ensure_ascii=False),
        model_used="fake-model", input_tokens=10, output_tokens=5,
    )


# ---- zero-items short-circuit ------------------------------------------


def test_no_items_no_llm_call(conn):
    llm = FakeLLM()
    result = synthesize_news_intelligence(conn, llm, [], "2026-08-14")
    assert result is None
    assert llm.calls == 0


# ---- reuse / idempotency -------------------------------------------------


def test_identical_call_is_reused_zero_llm_calls(conn, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    conn.execute(
        """INSERT INTO runs (run_id, run_date, status, started_at) VALUES ('r1', '2026-08-14', 'RUNNING', '2026-08-14T00:00:00Z')"""
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    items = _items()
    llm = FakeLLM(_valid_response([1, 2]))
    first = synthesize_news_intelligence(conn, llm, items, "2026-08-14")
    assert first["reused"] is False
    assert llm.calls == 1
    persist_news_intelligence(conn, run_row_id, first)
    conn.commit()

    second = synthesize_news_intelligence(conn, llm, items, "2026-08-14")
    assert second["reused"] is True
    assert llm.calls == 1  # no second call


def _start_run(conn, run_id="r1"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, status, started_at) VALUES (?, '2026-08-14', 'RUNNING', '2026-08-14T00:00:00Z')",
        (run_id,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_model_hint_change_forces_new_call(conn, monkeypatch):
    run_row_id = _start_run(conn)
    items = _items()
    llm = FakeLLM(_valid_response([1, 2]))
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    first = synthesize_news_intelligence(conn, llm, items, "2026-08-14")
    persist_news_intelligence(conn, run_row_id, first)
    conn.commit()
    assert llm.calls == 1

    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    result = synthesize_news_intelligence(conn, llm, items, "2026-08-14")
    assert result["reused"] is False
    assert llm.calls == 2


def test_prompt_version_change_forces_new_call(conn, monkeypatch):
    """Phase 3C.2 requirement G: explicit prompt_version isolation, not
    just model isolation."""
    run_row_id = _start_run(conn)
    items = _items()
    llm = FakeLLM(_valid_response([1, 2]))
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    first = synthesize_news_intelligence(conn, llm, items, "2026-08-14", prompt_version="v1")
    persist_news_intelligence(conn, run_row_id, first)
    conn.commit()
    assert llm.calls == 1

    result = synthesize_news_intelligence(conn, llm, items, "2026-08-14", prompt_version="v2")
    assert result["reused"] is False
    assert llm.calls == 2


def test_output_schema_version_change_forces_new_call(conn, monkeypatch):
    """Phase 3C.2 requirement G: explicit output_schema_version isolation,
    not just model isolation."""
    run_row_id = _start_run(conn)
    items = _items()
    llm = FakeLLM(_valid_response([1, 2]))
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    first = synthesize_news_intelligence(conn, llm, items, "2026-08-14", output_schema_version="v1")
    persist_news_intelligence(conn, run_row_id, first)
    conn.commit()
    assert llm.calls == 1

    result = synthesize_news_intelligence(conn, llm, items, "2026-08-14", output_schema_version="v2")
    assert result["reused"] is False
    assert llm.calls == 2


def test_item_content_change_forces_new_call(conn):
    run_row_id = _start_run(conn)
    llm = FakeLLM(_valid_response([1, 2]))
    first = synthesize_news_intelligence(conn, llm, _items(), "2026-08-14")
    persist_news_intelligence(conn, run_row_id, first)
    conn.commit()
    assert llm.calls == 1

    changed = _items()
    changed[0]["title"] = "Fed Cuts Rates Unexpectedly"
    result = synthesize_news_intelligence(conn, llm, changed, "2026-08-14")
    assert result["reused"] is False
    assert llm.calls == 2


def test_reuse_scoped_to_own_category_only(conn):
    """find_reusable_interpretation must never accidentally reuse a row
    from a different category (e.g. Producer Intelligence or V1's
    NEWS_COMBINED)."""
    items = _items()
    input_hash = compute_input_hash("2026-08-14", "v1", "v1", "claude-opus-5", items)
    conn.execute(
        """INSERT INTO runs (run_id, run_date, status, started_at) VALUES ('r1', '2026-08-14', 'RUNNING', '2026-08-14T00:00:00Z')"""
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, input_tokens,
            output_tokens, estimated_cost, output_text, confidence, created_at)
           VALUES (?, 'MUSIC_PRODUCER_INTELLIGENCE', 'm', 'v1', ?, 1, 1, NULL, '{}', 'MEDIUM', '2026-08-14T00:00:00Z')""",
        (run_row_id, input_hash),
    )
    conn.commit()
    assert find_reusable_interpretation(conn, input_hash) is None
    assert CATEGORY != "MUSIC_PRODUCER_INTELLIGENCE"


# ---- persistence ----------------------------------------------------------


def test_persist_writes_dedicated_category_row(conn):
    conn.execute(
        """INSERT INTO runs (run_id, run_date, status, started_at) VALUES ('r1', '2026-08-14', 'RUNNING', '2026-08-14T00:00:00Z')"""
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    llm = FakeLLM(_valid_response([1, 2]))
    result = synthesize_news_intelligence(conn, llm, _items(), "2026-08-14")
    persist_news_intelligence(conn, run_row_id, result)
    conn.commit()
    row = conn.execute("SELECT category, run_id FROM llm_interpretations WHERE category = ?", (CATEGORY,)).fetchone()
    assert row is not None
    assert row["run_id"] == run_row_id


# ---- structured-output validation: isolation, safety, grounding ---------


def test_valid_items_all_pass(conn):
    items_by_id = {i["id"]: i for i in _items()}
    parsed = {"items": [_valid_entry(1), _valid_entry(2)]}
    result = validate_news_intelligence(parsed, items_by_id)
    assert set(result.keys()) == {1, 2}
    assert set(result[1].keys()) == {"what_happened", "why_it_matters", "what_to_watch"}


def test_one_bad_item_does_not_block_others(conn):
    items_by_id = {i["id"]: i for i in _items()}
    bad = _valid_entry(1)
    bad["why_it_matters"] = ""  # empty -- invalid
    parsed = {"items": [bad, _valid_entry(2)]}
    result = validate_news_intelligence(parsed, items_by_id)
    assert 1 not in result
    assert 2 in result


def test_html_tag_rejected():
    items_by_id = {1: _items()[0]}
    bad = _valid_entry(1)
    bad["what_happened"] = "<script>alert(1)</script>"
    result = validate_news_intelligence({"items": [bad]}, items_by_id)
    assert result == {}


def test_verbatim_title_copy_rejected():
    item = _items()[0]
    items_by_id = {1: item}
    bad = _valid_entry(1)
    bad["what_happened"] = item["title"]  # exact verbatim copy
    result = validate_news_intelligence({"items": [bad]}, items_by_id)
    assert 1 not in result


def test_overlong_field_rejected():
    items_by_id = {1: _items()[0]}
    bad = _valid_entry(1)
    bad["what_to_watch"] = "a" * 500
    result = validate_news_intelligence({"items": [bad]}, items_by_id)
    assert result == {}


def test_unknown_id_ignored_not_crash():
    items_by_id = {1: _items()[0]}
    parsed = {"items": [_valid_entry(999)]}
    assert validate_news_intelligence(parsed, items_by_id) == {}


def test_malformed_root_returns_empty_dict():
    items_by_id = {1: _items()[0]}
    assert validate_news_intelligence("not a dict", items_by_id) == {}
    assert validate_news_intelligence({"no_items_key": []}, items_by_id) == {}


# ---- Phase 3B.2: schema/count contract (real-API 400 fix) --------------


def test_schema_has_no_unsupported_max_items():
    """Anthropic's real Structured Outputs (output_config.format=
    json_schema) rejects "maxItems" on an array type with a real 400
    invalid_request_error -- confirmed against the live API. The items
    array in _SCHEMA must never carry it again."""
    assert "maxItems" not in _SCHEMA["properties"]["items"]


def test_duplicate_output_id_rejected_other_ids_unaffected():
    """An id appearing more than once in the raw output is unreliable for
    that id specifically -- excluded from the result -- while a different,
    unambiguous id in the same response is still validated normally."""
    items_by_id = {i["id"]: i for i in _items()}
    dup_a = _valid_entry(1)
    dup_b = _valid_entry(1)
    dup_b["what_happened"] = "A different factual statement about item 1."
    unique = _valid_entry(2)
    result = validate_news_intelligence({"items": [dup_a, dup_b, unique]}, items_by_id)
    assert 1 not in result
    assert 2 in result


def test_exceeding_max_items_per_call_raises_no_llm_call(conn):
    llm = FakeLLM()
    too_many = [
        {"id": i, "title": f"Headline {i}", "snippet": "", "source_count": 1}
        for i in range(MAX_ITEMS_PER_CALL + 1)
    ]
    with pytest.raises(ValueError):
        synthesize_news_intelligence(conn, llm, too_many, "2026-08-14")
    assert llm.calls == 0


def test_exactly_max_items_per_call_does_not_raise(conn):
    llm = FakeLLM(_valid_response(list(range(MAX_ITEMS_PER_CALL))))
    exactly_max = [
        {"id": i, "title": f"Headline {i}", "snippet": "", "source_count": 1}
        for i in range(MAX_ITEMS_PER_CALL)
    ]
    result = synthesize_news_intelligence(conn, llm, exactly_max, "2026-08-14")
    assert result is not None
    assert llm.calls == 1
