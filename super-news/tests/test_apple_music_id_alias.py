"""Apple Identity Schema Fix: music_entity_aliases.alias_type now permits
'APPLE_MUSIC_ID'. Four targeted, high-value behaviors — real SQLite
constraint enforcement only, no string/grep checks against schema.sql."""

import sqlite3

import pytest

from db.database import connect, init_db


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
           VALUES ('Artist', 'Title', 'ORIGINAL', 'RESOLVED', '2026-08-12T00:00:00+00:00', 'apple_music')""",
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_alias(conn, entity_id, alias_type, alias_value):
    conn.execute(
        """INSERT INTO music_entity_aliases
           (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, ?, ?, 'apple_music', '2026-08-12T00:00:00+00:00')""",
        (entity_id, alias_type, alias_value),
    )


# ---- TEST 1: new value accepted ---------------------------------------------


def test_apple_music_id_alias_type_accepted(conn):
    entity_id = _insert_entity(conn)
    _insert_alias(conn, entity_id, "APPLE_MUSIC_ID", "1440857781")
    conn.commit()
    row = conn.execute(
        "SELECT alias_value FROM music_entity_aliases WHERE alias_type='APPLE_MUSIC_ID'"
    ).fetchone()
    assert row["alias_value"] == "1440857781"


# ---- TEST 2: representative old values still succeed ------------------------


def test_existing_alias_types_still_accepted(conn):
    entity_id = _insert_entity(conn)
    _insert_alias(conn, entity_id, "SPOTIFY_ID", "spotify123")
    _insert_alias(conn, entity_id, "ISRC", "USQX92600464")
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM music_entity_aliases WHERE music_entity_id=?", (entity_id,)
    ).fetchone()[0]
    assert count == 2


# ---- TEST 3: unknown alias_type still rejected -------------------------------


def test_unknown_alias_type_rejected(conn):
    entity_id = _insert_entity(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_alias(conn, entity_id, "NOT_A_REAL_TYPE", "whatever")


# ---- TEST 4: same Apple ID cannot bind to two different entities ------------


def test_same_apple_music_id_cannot_bind_two_entities(conn):
    entity_a = _insert_entity(conn)
    entity_b = _insert_entity(conn)
    _insert_alias(conn, entity_a, "APPLE_MUSIC_ID", "1440857781")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_alias(conn, entity_b, "APPLE_MUSIC_ID", "1440857781")
