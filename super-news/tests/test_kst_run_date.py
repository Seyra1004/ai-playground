"""KST run_date fix: automatically generated daily run_date must reflect
the Korean (Asia/Seoul) calendar day, not the UTC calendar day. This is
the only semantic that changes -- observed_at/collected_at and an
explicitly supplied run_date are unaffected."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from ingestion.orchestrator import run_daily_ingestion
from ingestion.records import AdapterOutcome
from ingestion.registry import RetryPolicy, SourceConfig
from music.orchestrator import run_apple_kr_collection


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _fixed_datetime(fixed_utc):
    """A datetime subclass whose now(tz) always represents the same real
    instant (fixed_utc), converted to whatever tz is requested -- mirrors
    real datetime.now(tz) semantics (one instant, many representations)."""

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    return _Fixed


def _news_registry():
    cfg = SourceConfig(
        source_name="a", enabled=True, source_type="rss", category="AI_NEWS",
        region="GLOBAL", endpoint="https://example.com", timeout_seconds=10,
        retry=RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_jitter_seconds=0.0),
        auth_mode="none",
    )
    return {"a": cfg}


def _run_date_of(conn, run_id):
    return conn.execute("SELECT run_date FROM runs WHERE run_id=?", (run_id,)).fetchone()["run_date"]


# ---- TEST 1: KST day boundary (news) -----------------------------------------


def test_news_run_date_crosses_kst_day_boundary(conn):
    fixed_utc = datetime(2026, 8, 11, 20, 0, 0, tzinfo=timezone.utc)  # 05:00 KST next day
    with patch("ingestion.orchestrator.datetime", _fixed_datetime(fixed_utc)), \
         patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        run_daily_ingestion(conn, _news_registry(), "run-news-1")
    assert _run_date_of(conn, "run-news-1") == "2026-08-12"


# ---- TEST 2: ordinary non-boundary case (news) -------------------------------


def test_news_run_date_ordinary_case(conn):
    fixed_utc = datetime(2026, 8, 12, 3, 0, 0, tzinfo=timezone.utc)  # 12:00 KST, same day
    with patch("ingestion.orchestrator.datetime", _fixed_datetime(fixed_utc)), \
         patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        run_daily_ingestion(conn, _news_registry(), "run-news-2")
    assert _run_date_of(conn, "run-news-2") == "2026-08-12"


# ---- TEST 3: explicit run_date override is preserved (news) -----------------


def test_news_explicit_run_date_override_preserved(conn):
    fixed_utc = datetime(2026, 8, 11, 20, 0, 0, tzinfo=timezone.utc)
    with patch("ingestion.orchestrator.datetime", _fixed_datetime(fixed_utc)), \
         patch("ingestion.pipeline.get_adapter", return_value=lambda sc, sleep=None: AdapterOutcome(records=[])):
        run_daily_ingestion(conn, _news_registry(), "run-news-3", run_date="2020-01-01")
    assert _run_date_of(conn, "run-news-3") == "2020-01-01"


# ---- TEST 4: both daily domains follow the same KST rule (music) ------------


def test_music_run_date_crosses_kst_day_boundary(conn):
    fixed_utc = datetime(2026, 8, 11, 20, 0, 0, tzinfo=timezone.utc)
    songs = [{"id": "1", "name": "Track", "artistName": "Artist"}]
    with patch("music.orchestrator.datetime", _fixed_datetime(fixed_utc)), \
         patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        run_apple_kr_collection(conn, "run-music-1")
    assert _run_date_of(conn, "run-music-1") == "2026-08-12"


def test_music_explicit_run_date_override_preserved(conn):
    fixed_utc = datetime(2026, 8, 11, 20, 0, 0, tzinfo=timezone.utc)
    songs = [{"id": "1", "name": "Track", "artistName": "Artist"}]
    with patch("music.orchestrator.datetime", _fixed_datetime(fixed_utc)), \
         patch("music.orchestrator.fetch_kr_most_played", return_value=songs):
        run_apple_kr_collection(conn, "run-music-2", run_date="2020-01-01")
    assert _run_date_of(conn, "run-music-2") == "2020-01-01"
