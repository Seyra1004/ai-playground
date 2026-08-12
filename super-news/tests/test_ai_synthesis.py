"""report.ai_synthesis: canonical input-hashing contract (4 required
determinism tests) + idempotency reuse + revision-on-change + zero-news
skip. Uses a FakeLLM -- never a live network/API call."""

import json

import pytest

from db.database import connect, init_db
from report.ai_synthesis import (
    PROMPT_VERSION,
    canonical_json,
    compute_input_hash,
    synthesize_news,
)
from report.llm_interface import LLMResponse, StructuredLLM


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
            parsed={"AI": [], "ECONOMY": [], "SOCIETY": []},
            raw_text='{"AI": [], "ECONOMY": [], "SOCIETY": []}',
            model_used="fake-model",
            input_tokens=10,
            output_tokens=5,
        )

    def generate_structured(self, system_prompt, user_prompt, schema):
        self.calls += 1
        return self._response


CANDIDATES = {
    "AI": [{"id": 1, "entity_name": "OpenAI", "normalized_title": "AI news", "source_count": 2}],
    "ECONOMY": [],
    "SOCIETY": [],
}


# ---- canonical input-hashing contract: 4 required determinism tests -------


def test_hash_deterministic_across_repeated_calls():
    h1 = compute_input_hash("2026-08-12", PROMPT_VERSION, CANDIDATES)
    h2 = compute_input_hash("2026-08-12", PROMPT_VERSION, CANDIDATES)
    assert h1 == h2


def test_hash_sensitive_to_candidate_content_change():
    changed = {
        "AI": [{"id": 1, "entity_name": "OpenAI", "normalized_title": "DIFFERENT TITLE", "source_count": 2}],
        "ECONOMY": [],
        "SOCIETY": [],
    }
    h1 = compute_input_hash("2026-08-12", PROMPT_VERSION, CANDIDATES)
    h2 = compute_input_hash("2026-08-12", PROMPT_VERSION, changed)
    assert h1 != h2


def test_hash_insensitive_to_dict_key_insertion_order():
    payload_a = {"b": 2, "a": 1}
    payload_b = {"a": 1, "b": 2}
    assert canonical_json(payload_a) == canonical_json(payload_b)


def test_hash_uses_utf8_and_fixed_separators_stable_for_non_ascii():
    payload = {"title": "한국어 제목", "id": 1}
    rendered = canonical_json(payload)
    # Fixed separators: no space after ':' or ','.
    assert ", " not in rendered
    assert ": " not in rendered
    # Non-ASCII preserved literally (UTF-8), not \uXXXX-escaped.
    assert "한국어" in rendered
    # Encoding to UTF-8 bytes must round-trip without loss.
    assert rendered.encode("utf-8").decode("utf-8") == rendered


def test_hash_changes_when_prompt_version_changes():
    h1 = compute_input_hash("2026-08-12", "v1", CANDIDATES)
    h2 = compute_input_hash("2026-08-12", "v2", CANDIDATES)
    assert h1 != h2


def test_hash_changes_when_report_date_changes():
    h1 = compute_input_hash("2026-08-12", PROMPT_VERSION, CANDIDATES)
    h2 = compute_input_hash("2026-08-13", PROMPT_VERSION, CANDIDATES)
    assert h1 != h2


# ---- zero-news behavior: skip the LLM call entirely ------------------------


def test_zero_candidates_skips_llm_call(conn):
    llm = FakeLLM()
    empty = {"AI": [], "ECONOMY": [], "SOCIETY": []}
    result = synthesize_news(conn, llm, empty, "2026-08-12")
    assert result is None
    assert llm.calls == 0


# ---- identical-input idempotency: no second LLM call -----------------------


def test_identical_input_reuses_without_second_llm_call(conn):
    llm = FakeLLM()
    first = synthesize_news(conn, llm, CANDIDATES, "2026-08-12")
    assert first["reused"] is False
    assert llm.calls == 1

    # Persist the interpretation row a real run would write, so the reuse
    # lookup has something to find.
    conn.execute(
        """INSERT INTO runs (run_id, run_date, started_at, status) VALUES ('run-1', '2026-08-12', 'x', 'completed')"""
    )
    run_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, input_tokens,
            output_tokens, estimated_cost, output_text, confidence, created_at)
           VALUES (?, 'NEWS_COMBINED', ?, ?, ?, ?, ?, NULL, ?, 'MEDIUM', 'x')""",
        (run_row_id, first["model_used"], first["prompt_version"], first["input_hash"],
         first["input_tokens"], first["output_tokens"], first["output_text"]),
    )
    conn.commit()

    second = synthesize_news(conn, llm, CANDIDATES, "2026-08-12")
    assert second["reused"] is True
    assert llm.calls == 1  # no second call
    assert second["input_hash"] == first["input_hash"]
    assert second["parsed"] == first["parsed"]


# ---- same-day-candidate-change / prompt_version-change creates a revision --


def test_candidate_change_same_day_creates_new_call(conn):
    llm = FakeLLM()
    first = synthesize_news(conn, llm, CANDIDATES, "2026-08-12")
    changed = {
        "AI": [{"id": 2, "entity_name": "Other", "normalized_title": "Different", "source_count": 1}],
        "ECONOMY": [],
        "SOCIETY": [],
    }
    second = synthesize_news(conn, llm, changed, "2026-08-12")
    assert llm.calls == 2
    assert first["input_hash"] != second["input_hash"]


def test_prompt_version_change_creates_new_call(conn):
    llm = FakeLLM()
    first = synthesize_news(conn, llm, CANDIDATES, "2026-08-12", prompt_version="v1")
    second = synthesize_news(conn, llm, CANDIDATES, "2026-08-12", prompt_version="v2")
    assert llm.calls == 2
    assert first["input_hash"] != second["input_hash"]
