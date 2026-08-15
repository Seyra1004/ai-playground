"""music.entity_resolution: deterministic cross-source matching hierarchy
(ISRC -> exact normalized metadata -> UNRESOLVED). No fuzzy matching, no
LLM call -- UNRESOLVED is a valid, preferred outcome whenever confidence
is insufficient."""

import pytest

from db.database import connect, init_db
from music.entity_resolution import (
    METHOD_EXACT_NORMALIZED_METADATA,
    METHOD_EXTERNAL_ID,
    UNRESOLVED,
    normalize_for_matching,
    resolve_existing_entity,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_entity(conn, artist, title, source="spotify_chart"):
    conn.execute(
        """INSERT INTO music_entities
           (canonical_artist, canonical_title, variant, resolution_status, first_seen_at, first_seen_source)
           VALUES (?, ?, 'ORIGINAL', 'RESOLVED', '2026-08-12T00:00:00+00:00', ?)""",
        (artist, title, source),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_alias(conn, entity_id, alias_type, alias_value, source="spotify_chart"):
    conn.execute(
        """INSERT INTO music_entity_aliases (music_entity_id, alias_type, alias_value, source_name, confirmed_at)
           VALUES (?, ?, ?, ?, '2026-08-12T00:00:00+00:00')""",
        (entity_id, alias_type, alias_value, source),
    )
    conn.commit()


# ---- Level 1: EXTERNAL_ID (ISRC) --------------------------------------------


def test_matching_isrc_resolves_to_existing_entity(conn):
    entity_id = _insert_entity(conn, "Artist", "Title")
    _insert_alias(conn, entity_id, "ISRC", "USRC12345678")
    result = resolve_existing_entity(conn, "Different Artist Text", "Different Title Text", isrc="USRC12345678")
    assert result["entity_id"] == entity_id
    assert result["resolution_method"] == METHOD_EXTERNAL_ID


def test_isrc_takes_priority_over_metadata_mismatch(conn):
    """Even though the supplied artist/title text doesn't match the
    entity's canonical text, a real matching ISRC is stronger evidence and
    wins -- this is Level 1 dominating, per the locked hierarchy."""
    entity_id = _insert_entity(conn, "Canonical Artist", "Canonical Title")
    _insert_alias(conn, entity_id, "ISRC", "USRC00000001")
    result = resolve_existing_entity(conn, "Totally Different", "Totally Different Too", isrc="USRC00000001")
    assert result["entity_id"] == entity_id
    assert result["canonical_artist"] == "Canonical Artist"


def test_no_isrc_supplied_never_matches_by_accident(conn):
    entity_id = _insert_entity(conn, "Artist", "Title")
    _insert_alias(conn, entity_id, "ISRC", "USRC12345678")
    result = resolve_existing_entity(conn, "Unrelated Artist", "Unrelated Title", isrc=None)
    assert result["resolution_method"] == UNRESOLVED


# ---- Level 3: EXACT_NORMALIZED_METADATA -------------------------------------


def test_same_canonical_entity_across_two_sources_matches(conn):
    entity_id = _insert_entity(conn, "BTS", "Dynamite", source="apple_music")
    result = resolve_existing_entity(conn, "BTS", "Dynamite")
    assert result["entity_id"] == entity_id
    assert result["resolution_method"] == METHOD_EXACT_NORMALIZED_METADATA


def test_capitalization_difference_still_matches(conn):
    entity_id = _insert_entity(conn, "Artist Name", "Song Title")
    result = resolve_existing_entity(conn, "ARTIST NAME", "SONG TITLE")
    assert result["entity_id"] == entity_id


def test_harmless_apostrophe_variant_still_matches(conn):
    entity_id = _insert_entity(conn, "Artist", "Don’t Stop")  # curly apostrophe
    result = resolve_existing_entity(conn, "Artist", "Don't Stop")  # straight apostrophe
    assert result["entity_id"] == entity_id


def test_whitespace_difference_still_matches(conn):
    entity_id = _insert_entity(conn, "  Artist  ", "Song   Title")
    result = resolve_existing_entity(conn, "Artist", "Song Title")
    assert result["entity_id"] == entity_id


def test_remix_never_matches_original(conn):
    _insert_entity(conn, "Artist", "Song")
    result = resolve_existing_entity(conn, "Artist", "Song (Remix)")
    assert result["resolution_method"] == UNRESOLVED
    assert result["entity_id"] is None


def test_live_version_never_matches_studio(conn):
    _insert_entity(conn, "Artist", "Song")
    result = resolve_existing_entity(conn, "Artist", "Song (Live)")
    assert result["resolution_method"] == UNRESOLVED


def test_acoustic_never_matches_original(conn):
    _insert_entity(conn, "Artist", "Song")
    result = resolve_existing_entity(conn, "Artist", "Song (Acoustic)")
    assert result["resolution_method"] == UNRESOLVED


def test_instrumental_never_matches_vocal(conn):
    _insert_entity(conn, "Artist", "Song")
    result = resolve_existing_entity(conn, "Artist", "Song (Instrumental)")
    assert result["resolution_method"] == UNRESOLVED


def test_same_title_different_artist_never_matches(conn):
    _insert_entity(conn, "Artist One", "Same Song")
    result = resolve_existing_entity(conn, "Artist Two", "Same Song")
    assert result["resolution_method"] == UNRESOLVED


def test_same_artist_different_title_never_matches(conn):
    _insert_entity(conn, "Same Artist", "Song One")
    result = resolve_existing_entity(conn, "Same Artist", "Song Two")
    assert result["resolution_method"] == UNRESOLVED


def test_featured_artist_text_never_auto_matches(conn):
    _insert_entity(conn, "Artist A", "Song")
    result = resolve_existing_entity(conn, "Artist A feat. Artist B", "Song")
    assert result["resolution_method"] == UNRESOLVED


def test_no_candidates_at_all_is_unresolved(conn):
    result = resolve_existing_entity(conn, "Nobody", "Nothing")
    assert result == {"entity_id": None, "canonical_artist": "Nobody", "canonical_title": "Nothing",
                       "resolution_method": UNRESOLVED}


def test_unresolved_never_alters_the_supplied_text(conn):
    result = resolve_existing_entity(conn, "  Weird   Spacing  ", "Also Weird")
    assert result["canonical_artist"] == "  Weird   Spacing  "
    assert result["canonical_title"] == "Also Weird"


# ---- normalize_for_matching: safe-only, never strips meaningful text -------


def test_normalize_never_strips_version_markers():
    assert normalize_for_matching("Song (Remix)") != normalize_for_matching("Song")
    assert normalize_for_matching("Song - Live Version") != normalize_for_matching("Song")
    assert normalize_for_matching("Song (Sped Up)") != normalize_for_matching("Song")


def test_normalize_collapses_whitespace_and_case():
    assert normalize_for_matching("  Hello   World  ") == normalize_for_matching("hello world")


def test_normalize_empty_and_none_safe():
    assert normalize_for_matching(None) == ""
    assert normalize_for_matching("") == ""
