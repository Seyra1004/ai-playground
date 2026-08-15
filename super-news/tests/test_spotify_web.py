"""Spotify Web API enrichment layer: credentials-optional behavior, ISRC
alias write-once semantics, and a fetch/parse smoke test. Real scratch
SQLite; Spotify HTTP is mocked -- no live network call, no real
credentials used anywhere in this file."""

from unittest.mock import patch

import pytest

from db.database import connect, init_db
from music.spotify_web import (
    SpotifyWebNotConfiguredError,
    credentials_configured,
    enrich_entity,
    fetch_track_metadata,
    get_client_credentials_token,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_entity(conn):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES ('Artist', 'Title', 'ORIGINAL', 'RESOLVED', '2026-08-12T00:00:00+00:00', 'spotify_chart')"""
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class _FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


# ---- credentials-optional: never treated as an error absent config --------


def test_credentials_configured_false_when_unset(monkeypatch):
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)
    assert credentials_configured() is False


def test_credentials_configured_true_when_both_set(monkeypatch):
    values = {"SPOTIFY_CLIENT_ID": "id", "SPOTIFY_CLIENT_SECRET": "secret"}
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: values.get(name, default))
    assert credentials_configured() is True


def test_token_request_raises_not_configured_without_calling_network(monkeypatch):
    monkeypatch.setattr("music.spotify_web.get_optional_env", lambda name, default=None: None)
    with patch("music.spotify_web.request_with_retry") as mock_request:
        with pytest.raises(SpotifyWebNotConfiguredError):
            get_client_credentials_token()
    mock_request.assert_not_called()


# ---- fetch/parse smoke test (mocked HTTP, no live call) ---------------------


def test_fetch_track_metadata_parses_expected_fields():
    fake_body = {
        "artists": [{"name": "Ariana Grande"}],
        "album": {"name": "eternal sunshine", "release_date": "2024-03-08"},
        "external_ids": {"isrc": "USUM72400001"},
        "external_urls": {"spotify": "https://open.spotify.com/track/abc"},
    }
    with patch("music.spotify_web.request_with_retry", return_value=_FakeResponse(fake_body)) as mock_request:
        metadata = fetch_track_metadata("abc", "fake-token")
    assert metadata == {
        "artist": "Ariana Grande",
        "album": "eternal sunshine",
        "release_date": "2024-03-08",
        "isrc": "USUM72400001",
        "canonical_url": "https://open.spotify.com/track/abc",
    }
    assert mock_request.call_args.kwargs["headers"]["Authorization"] == "Bearer fake-token"


def test_fetch_track_metadata_never_fabricates_missing_fields():
    with patch("music.spotify_web.request_with_retry", return_value=_FakeResponse({})):
        metadata = fetch_track_metadata("abc", "fake-token")
    assert metadata == {"artist": None, "album": None, "release_date": None, "isrc": None, "canonical_url": None}


# ---- ISRC alias: write-once, never overwritten ------------------------------


def test_enrich_entity_adds_isrc_alias(conn):
    entity_id = _insert_entity(conn)
    fake_body = {"artists": [{"name": "A"}], "album": {"name": "B", "release_date": "2024-01-01"},
                 "external_ids": {"isrc": "USUM72400001"}, "external_urls": {"spotify": "https://x"}}
    with patch("music.spotify_web.request_with_retry", return_value=_FakeResponse(fake_body)):
        enrich_entity(conn, entity_id, "abc", "fake-token")

    row = conn.execute(
        "SELECT alias_value FROM music_entity_aliases WHERE music_entity_id = ? AND alias_type = 'ISRC'",
        (entity_id,),
    ).fetchone()
    assert row["alias_value"] == "USUM72400001"


def test_enrich_entity_never_overwrites_existing_isrc(conn):
    entity_id = _insert_entity(conn)
    conn.execute(
        """INSERT INTO music_entity_aliases (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, 'ISRC', 'ORIGINAL_ISRC', 'manual', '2026-08-12T00:00:00+00:00')""",
        (entity_id,),
    )
    conn.commit()

    fake_body = {"artists": [{"name": "A"}], "album": {"name": "B", "release_date": "2024-01-01"},
                 "external_ids": {"isrc": "DIFFERENT_ISRC"}, "external_urls": {"spotify": "https://x"}}
    with patch("music.spotify_web.request_with_retry", return_value=_FakeResponse(fake_body)):
        enrich_entity(conn, entity_id, "abc", "fake-token")

    rows = conn.execute(
        "SELECT alias_value FROM music_entity_aliases WHERE music_entity_id = ? AND alias_type = 'ISRC'",
        (entity_id,),
    ).fetchall()
    assert [r["alias_value"] for r in rows] == ["ORIGINAL_ISRC"]


def test_enrich_entity_returns_metadata_without_persisting_release_date(conn):
    entity_id = _insert_entity(conn)
    fake_body = {"artists": [{"name": "A"}], "album": {"name": "B", "release_date": "2024-01-01"},
                 "external_ids": {}, "external_urls": {"spotify": "https://x"}}
    with patch("music.spotify_web.request_with_retry", return_value=_FakeResponse(fake_body)):
        metadata = enrich_entity(conn, entity_id, "abc", "fake-token")
    assert metadata["release_date"] == "2024-01-01"
    # music_entities has no release_date column -- confirm nothing was written there.
    columns = [row[1] for row in conn.execute("PRAGMA table_info(music_entities)").fetchall()]
    assert "release_date" not in columns
