"""TEST A-X from the Phase 2C test matrix: RAW -> NORMALIZED FACT
normalization. Every test uses a real scratch SQLite DB (tmp_path) and
never a real network call."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.normalize import (
    clean_html_text,
    compute_title_fingerprint,
    determine_entity,
    determine_language,
    normalize_batch,
    normalize_raw_item,
    normalize_title,
    resolve_event_key,
)
from ingestion.registry import RetryPolicy, SourceConfig


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _source_config(name, source_type, category):
    return SourceConfig(
        source_name=name, enabled=True, source_type=source_type, category=category,
        region="GLOBAL", endpoint="https://example.com", timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )


@pytest.fixture
def registry():
    return {
        "naver_news": _source_config("naver_news", "naver_news_api", "SOCIETY_NEWS"),
        "ai_rss": _source_config("ai_rss", "rss", "AI_NEWS"),
        "econ_rss": _source_config("econ_rss", "rss", "ECONOMY_NEWS"),
    }


def _insert_raw_item(conn, source_name, key, source_type="rss", url=None, title="Title", snippet=None, extra_json=None):
    conn.execute(
        """INSERT INTO raw_items
           (source_name, source_item_key, source_type, source_url, title, snippet, collected_at, extra_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_name, key, source_type, url or f"https://example.com/{key}", title, snippet,
         "2026-08-12T00:00:00+00:00", extra_json),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _fetch_raw_row(conn, raw_item_id):
    return conn.execute("SELECT * FROM raw_items WHERE id=?", (raw_item_id,)).fetchone()


# ---- TEST A: valid raw item -> normalized row created -----------------------


def test_A_valid_raw_item_creates_normalized_row(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", title="Hello World")
    raw_row = _fetch_raw_row(conn, raw_id)
    outcome = normalize_raw_item(conn, raw_row, registry)
    assert outcome.status == "normalized"
    row = conn.execute("SELECT * FROM normalized_items WHERE id=?", (outcome.normalized_item_id,)).fetchone()
    assert row["raw_item_id"] == raw_id
    assert row["category"] == "AI_NEWS"
    assert row["normalized_title"] == "Hello World"


# ---- TEST B: same raw item second run -> no duplicate -----------------------


def test_B_second_normalization_is_idempotent(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", title="Hello World")
    raw_row = _fetch_raw_row(conn, raw_id)
    first = normalize_raw_item(conn, raw_row, registry)
    second = normalize_raw_item(conn, raw_row, registry)
    assert first.status == "normalized"
    assert second.status == "already_normalized"
    assert second.normalized_item_id == first.normalized_item_id
    count = conn.execute("SELECT COUNT(*) FROM normalized_items WHERE raw_item_id=?", (raw_id,)).fetchone()[0]
    assert count == 1


# ---- TEST C: raw FK provenance preserved -------------------------------------


def test_C_provenance_fk_points_back_to_original_raw_item(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", url="https://example.com/original", title="T")
    raw_row = _fetch_raw_row(conn, raw_id)
    outcome = normalize_raw_item(conn, raw_row, registry)
    joined = conn.execute(
        """SELECT raw_items.source_url AS raw_url, normalized_items.normalized_title
           FROM normalized_items JOIN raw_items ON normalized_items.raw_item_id = raw_items.id
           WHERE normalized_items.id = ?""",
        (outcome.normalized_item_id,),
    ).fetchone()
    assert joined["raw_url"] == "https://example.com/original"


# ---- TEST D: raw row is never modified ---------------------------------------


def test_D_raw_item_row_unmodified_after_normalization(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", title="<b>Raw</b> &amp; Title", snippet="snip")
    before = dict(_fetch_raw_row(conn, raw_id))
    normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), registry)
    after = dict(_fetch_raw_row(conn, raw_id))
    assert before == after


# ---- TEST E: HTML title cleanup is deterministic -----------------------------


def test_E_html_cleanup_strips_tags_and_unescapes_entities():
    result = clean_html_text('<b>AI</b> &amp; "Robots" &lt;now&gt;')
    assert result == 'AI & "Robots" <now>'
    assert clean_html_text('<b>AI</b> &amp; "Robots" &lt;now&gt;') == result  # deterministic


# ---- TEST F: unicode/whitespace cleanup is deterministic ---------------------


def test_F_whitespace_collapse_and_unicode_normalization():
    result = clean_html_text("Title  with   \n\t weird    spacing")
    assert result == "Title with weird spacing"
    assert "\n" not in result and "\t" not in result


def test_F_empty_and_whitespace_only_text_yields_none():
    assert clean_html_text("") is None
    assert clean_html_text("   \n\t  ") is None
    assert clean_html_text(None) is None


# ---- TEST G: same canonical URL -> same event_key ----------------------------


def test_G_same_canonical_url_yields_same_event_key():
    key_1 = resolve_event_key("https://example.com/a?utm_source=x", "Title A")
    key_2 = resolve_event_key("https://Example.com/a", "Title A (slightly different wording)")
    assert key_1 == key_2  # URL identity wins regardless of minor title differences


# ---- TEST H: same exact normalized title -> same deterministic event_key ----


def test_H_same_title_fingerprint_when_no_url_available():
    key_1 = resolve_event_key(None, "Breaking: Something Happened")
    key_2 = resolve_event_key(None, "breaking:   something happened")  # case/whitespace-insensitive match
    assert key_1 == key_2
    assert key_1.startswith("title:")


# ---- TEST I: different ambiguous titles -> no forced merge ------------------


def test_I_different_titles_and_urls_are_not_merged():
    key_1 = resolve_event_key("https://example.com/a", "Title A")
    key_2 = resolve_event_key("https://example.com/b", "Title B")
    assert key_1 != key_2


# ---- TEST J: event_key is stable across repeat calls -------------------------


def test_J_event_key_stable_across_repeated_calls():
    assert resolve_event_key("https://example.com/a", "T") == resolve_event_key("https://example.com/a", "T")
    assert compute_title_fingerprint("Same Title") == compute_title_fingerprint("Same Title")


# ---- TEST K: no random/hash() usage ------------------------------------------


def test_K_event_key_never_uses_builtin_hash_or_random():
    import hashlib

    from ingestion.identity import canonicalize_url

    canonical = canonicalize_url("https://example.com/a")
    expected = "url:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert resolve_event_key("https://example.com/a", "irrelevant title") == expected


# ---- TEST L + M: cross-source same event -> both rows exist, key shared ----


def test_L_and_M_cross_source_same_url_both_normalized_and_share_event_key(conn, registry):
    shared_url = "https://wire-service.example.com/story/123"
    raw_1 = _insert_raw_item(conn, "naver_news", "n1", source_type="naver_news_api", url=shared_url, title="속보: 사건 발생")
    raw_2 = _insert_raw_item(conn, "ai_rss", "r1", source_type="rss", url=shared_url, title="Breaking: Event Occurred")

    outcome_1 = normalize_raw_item(conn, _fetch_raw_row(conn, raw_1), registry)
    outcome_2 = normalize_raw_item(conn, _fetch_raw_row(conn, raw_2), registry)

    assert outcome_1.status == "normalized"
    assert outcome_2.status == "normalized"
    row_1 = conn.execute("SELECT event_key FROM normalized_items WHERE id=?", (outcome_1.normalized_item_id,)).fetchone()
    row_2 = conn.execute("SELECT event_key FROM normalized_items WHERE id=?", (outcome_2.normalized_item_id,)).fetchone()
    assert row_1["event_key"] == row_2["event_key"]  # cross-source corroboration is possible
    count = conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]
    assert count == 2  # both preserved, neither dropped as a "duplicate"


# ---- TEST N: same source, unrelated items -> separate event_key -------------


def test_N_same_source_unrelated_items_get_different_event_keys(conn, registry):
    raw_1 = _insert_raw_item(conn, "ai_rss", "u1", url="https://example.com/u1", title="Story One")
    raw_2 = _insert_raw_item(conn, "ai_rss", "u2", url="https://example.com/u2", title="Story Two")
    outcome_1 = normalize_raw_item(conn, _fetch_raw_row(conn, raw_1), registry)
    outcome_2 = normalize_raw_item(conn, _fetch_raw_row(conn, raw_2), registry)
    row_1 = conn.execute("SELECT event_key FROM normalized_items WHERE id=?", (outcome_1.normalized_item_id,)).fetchone()
    row_2 = conn.execute("SELECT event_key FROM normalized_items WHERE id=?", (outcome_2.normalized_item_id,)).fetchone()
    assert row_1["event_key"] != row_2["event_key"]


# ---- TEST O: unknown language -> NULL; known source -> deterministic value --


def test_O_language_rule_is_deterministic_and_conservative():
    assert determine_language("rss") is None
    assert determine_language("naver_news_api") == "ko"
    assert determine_language("some_future_source_type") is None


# ---- TEST P: no confident entity -> entity_type/name NULL -------------------


def test_P_no_current_source_yields_confident_entity(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", title="T")
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), registry)
    row = conn.execute("SELECT entity_type, entity_name FROM normalized_items WHERE id=?", (outcome.normalized_item_id,)).fetchone()
    assert row["entity_type"] is None
    assert row["entity_name"] is None
    assert determine_entity(None) == (None, None)
    assert determine_entity('{"some": "structured data"}') == (None, None)


# ---- TEST Q: invalid/minimal raw -> REJECTED, no crash -----------------------


def test_Q_no_usable_title_or_snippet_is_rejected_not_failed(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", title=None, snippet=None)
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), registry)
    assert outcome.status == "rejected"
    count = conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]
    assert count == 0


def test_Q_unknown_source_name_is_rejected(conn, registry):
    raw_id = _insert_raw_item(conn, "removed_source", "k1", title="T")
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), registry)
    assert outcome.status == "rejected"
    assert "removed_source" in outcome.reason


def test_Q_snippet_used_when_title_unusable(conn, registry):
    raw_id = _insert_raw_item(conn, "ai_rss", "k1", title="   ", snippet="Usable snippet text")
    outcome = normalize_raw_item(conn, _fetch_raw_row(conn, raw_id), registry)
    assert outcome.status == "normalized"
    row = conn.execute("SELECT normalized_title FROM normalized_items WHERE id=?", (outcome.normalized_item_id,)).fetchone()
    assert row["normalized_title"] == "Usable snippet text"


# ---- TEST R: one item's unexpected failure doesn't affect siblings ---------


def test_R_one_unexpected_failure_does_not_affect_other_items(conn, registry):
    raw_1 = _insert_raw_item(conn, "ai_rss", "good1", title="Good One")
    raw_2 = _insert_raw_item(conn, "ai_rss", "bad", title="TRIGGER_FAILURE")
    raw_3 = _insert_raw_item(conn, "ai_rss", "good2", title="Good Two")

    real_normalize_title = normalize_title

    def flaky_normalize_title(title, snippet):
        if title == "TRIGGER_FAILURE":
            raise RuntimeError("simulated unexpected bug")
        return real_normalize_title(title, snippet)

    with patch("ingestion.normalize.normalize_title", side_effect=flaky_normalize_title):
        outcomes = normalize_batch(conn, registry, raw_item_ids=[raw_1, raw_2, raw_3])

    statuses = {o.raw_item_id: o.status for o in outcomes}
    assert statuses[raw_1] == "normalized"
    assert statuses[raw_2] == "failed"
    assert statuses[raw_3] == "normalized"
    count = conn.execute("SELECT COUNT(*) FROM normalized_items").fetchone()[0]
    assert count == 2


# ---- TEST S: deterministic processing order ----------------------------------


def test_S_batch_processes_in_raw_items_id_ascending_order(conn, registry):
    raw_1 = _insert_raw_item(conn, "ai_rss", "k1", title="First")
    raw_2 = _insert_raw_item(conn, "ai_rss", "k2", title="Second")
    raw_3 = _insert_raw_item(conn, "ai_rss", "k3", title="Third")

    processed_order = []
    real_normalize_raw_item = normalize_raw_item

    def tracking(conn_, raw_item, registry_):
        processed_order.append(raw_item["id"])
        return real_normalize_raw_item(conn_, raw_item, registry_)

    with patch("ingestion.normalize.normalize_raw_item", side_effect=tracking):
        normalize_batch(conn, registry, raw_item_ids=[raw_3, raw_1, raw_2])

    assert processed_order == [raw_1, raw_2, raw_3]


def test_S_normalize_all_defaults_to_id_ascending_order(conn, registry):
    raw_1 = _insert_raw_item(conn, "ai_rss", "k1", title="First")
    raw_2 = _insert_raw_item(conn, "ai_rss", "k2", title="Second")
    outcomes = normalize_batch(conn, registry)
    assert [o.raw_item_id for o in outcomes] == [raw_1, raw_2]


# ---- TEST T: Phase 2A canonicalize_url is reused, not reimplemented ---------


def test_T_event_key_uses_phase_2a_url_canonicalization_rules():
    # utm_ tracking params are stripped by ingestion.identity.canonicalize_url
    # — if resolve_event_key used its own separate logic, this equivalence
    # would not hold.
    key_with_tracking = resolve_event_key("https://example.com/a?utm_source=newsletter", "T")
    key_without_tracking = resolve_event_key("https://example.com/a", "T")
    assert key_with_tracking == key_without_tracking


# ---- TEST U: no external network call during normalization ------------------


def test_U_normalize_batch_makes_no_network_call(conn, registry):
    _insert_raw_item(conn, "ai_rss", "k1", title="T")
    with patch("requests.request") as mock_request:
        normalize_batch(conn, registry)
    mock_request.assert_not_called()


# ---- TEST V: no LLM call (no LLM client exists anywhere in this module) ----


def test_V_normalize_module_never_imports_an_llm_client():
    import ast
    import pathlib

    source = pathlib.Path(__import__("ingestion.normalize", fromlist=["__file__"]).__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    assert imported_names.isdisjoint({"anthropic", "openai"})
