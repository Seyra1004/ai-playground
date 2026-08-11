"""Apple Daily Collection Integration: the 7 required invariants. Real
scratch SQLite; Apple HTTP is mocked at music.orchestrator's import of
fetch_kr_most_played -- no live network call."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from music.orchestrator import run_apple_kr_collection


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _song(apple_id, name, artist="Some Artist"):
    return {"id": apple_id, "name": name, "artistName": artist}


def _observations(conn):
    return conn.execute("SELECT * FROM music_observations ORDER BY metric_value").fetchall()


# ---- 1: one fetched response -> one shared observed_at ----------------------


def test_one_response_uses_one_shared_observed_at(conn):
    songs = [_song("1", "First"), _song("2", "Second"), _song("3", "Third")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        run_apple_kr_collection(conn, "run-1")
    rows = _observations(conn)
    assert len(rows) == 3
    assert len({r["observed_at"] for r in rows}) == 1


# ---- 2: returned order -> correct chart positions ----------------------------


def test_returned_order_maps_to_chart_positions(conn):
    songs = [_song("1", "First"), _song("2", "Second"), _song("3", "Third")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        run_apple_kr_collection(conn, "run-1")
    rows = _observations(conn)
    assert [r["metric_value"] for r in rows] == [1, 2, 3]


# ---- 3: same Apple ID -> no duplicate music_entity across separate runs -----


def test_same_apple_id_no_duplicate_entity_across_runs(conn):
    songs = [_song("111", "Track One")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        run_apple_kr_collection(conn, "run-1")
        run_apple_kr_collection(conn, "run-2")
    count = conn.execute(
        "SELECT COUNT(*) FROM music_entity_aliases WHERE alias_type='APPLE_MUSIC_ID' AND alias_value='111'"
    ).fetchone()[0]
    assert count == 1


# ---- 4: same-observed_at retry -> no duplicate observation ------------------


def test_same_observed_at_retry_does_not_duplicate_observation(conn):
    songs = [_song("111", "Track One")]
    fixed_now = datetime(2026, 8, 12, 6, 0, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs), \
         patch("music.orchestrator.datetime", _FixedDateTime):
        result_1 = run_apple_kr_collection(conn, "run-1")
        result_2 = run_apple_kr_collection(conn, "run-2")

    assert result_1["source_result"]["status"] == "SUCCESS"
    assert result_1["source_result"]["items_collected"] == 1
    assert result_2["source_result"]["status"] == "SUCCESS"
    assert result_2["source_result"]["items_collected"] == 0  # fully duplicate, nothing new

    count = conn.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
    assert count == 1


# ---- 5: later observed_at -> new historical observation allowed -------------


def test_later_observed_at_creates_new_observation(conn):
    songs = [_song("111", "Track One")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        run_apple_kr_collection(conn, "run-1")
        run_apple_kr_collection(conn, "run-2")  # real clock, later timestamp
    count = conn.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
    assert count == 2


# ---- 6: fetch/invalid-response failure -> no false successful snapshot -----


def test_fetch_failure_records_failed_status_and_no_observations(conn):
    from ingestion.http import HttpTransientError

    with patch("music.orchestrator.fetch_kr_most_played", side_effect=HttpTransientError("boom")):
        result = run_apple_kr_collection(conn, "run-1")

    assert result["source_result"]["status"] == "FAILED"
    assert result["status"] == "failed"
    count = conn.execute("SELECT COUNT(*) FROM music_observations").fetchone()[0]
    assert count == 0


def test_empty_response_records_failed_status_not_a_fake_success(conn):
    with patch("music.orchestrator.fetch_kr_most_played", return_value=[]):
        result = run_apple_kr_collection(conn, "run-1")
    assert result["source_result"]["status"] == "FAILED"
    assert "empty" in result["source_result"]["failure_reason"].lower()


# ---- 7: daily integration invokes Apple collection exactly once -------------


def test_daily_collection_invokes_fetch_exactly_once(conn):
    songs = [_song("1", "First")]
    with patch("music.orchestrator.fetch_kr_most_played", return_value=songs) as mock_fetch:
        run_apple_kr_collection(conn, "run-1")
    assert mock_fetch.call_count == 1
