"""Cross-source music entity resolution -- the missing piece music/
cross_platform.py's own docstring already diagnoses: without this, every
collector creates its own siloed entity keyed to its own platform ID, so
no entity is ever observed by two different sources and cross-platform
detection can never fire in production.

Called by a collector's OWN _resolve_or_create_entity AFTER it has already
checked (and found nothing for) its own alias_type -- this module never
repeats that check. That per-source alias lookup IS "Level 2 / EXISTING_
CANONICAL_MAPPING" from the design; it already exists in music/
apple_music.py and music/spotify_chart.py and is intentionally not
duplicated here. This module only answers: "does this NEW-to-this-source
observation match an entity ANOTHER source already created?"

Matching hierarchy, strongest evidence first -- returns UNRESOLVED rather
than guessing whenever confidence is insufficient (a false match is worse
than a missed one):

1. EXTERNAL_ID: an ISRC alias already recorded (by ANY source) with the
   same value as the ISRC supplied for this observation. Today, only
   music/spotify_web.py's enrich_entity() ever writes an ISRC alias --
   Apple Music has no ISRC signal at all (see its own module docstring).
   So in current production reality this level is dormant for Apple Music
   <-> Spotify matching specifically, though the mechanism is real and
   would activate the moment any collector supplies a real ISRC. Not
   fabricated, not assumed -- exactly what today's data supports.

2. (Deliberately absent here -- see module docstring above: this is the
   caller's own per-source alias lookup, not this module's job.)

3. EXACT_NORMALIZED_METADATA: canonical_artist AND canonical_title both
   match, EXACTLY, after SAFE normalization only (Unicode NFKC, case,
   surrounding/repeated whitespace, straight/curly apostrophe unification)
   -- never fuzzy, never partial, never title-only or artist-only.
   Deliberately does NOT strip parenthetical/suffix version markers like
   "(Remix)"/"(Live)"/"(Acoustic)"/"(Instrumental)" or featured-artist
   text -- stripping those would make a remix match its original, exactly
   the false-positive class this module exists to prevent. variant is NOT
   used as a safety signal here: both current collectors hardcode it to
   'ORIGINAL' regardless of the actual title text (no title parsing
   happens at collection time), so it carries no real discriminating
   information today.

Known, accepted scaling tradeoff: the metadata match scans all
music_entities in Python (no fuzzy index) -- fine at this project's
current volume, same tradeoff already accepted elsewhere (e.g.
ingestion/orchestrator.py's normalize_batch full-table scan). Not
optimized here; revisit only if entity count actually becomes a problem.
"""

import re
import unicodedata

UNRESOLVED = "UNRESOLVED"
METHOD_EXTERNAL_ID = "EXTERNAL_ID"
METHOD_EXACT_NORMALIZED_METADATA = "EXACT_NORMALIZED_METADATA"


def normalize_for_matching(text):
    """SAFE normalization only: Unicode NFKC, straight/curly apostrophe
    unification, case-fold, collapsed whitespace. Never strips
    parenthetical content, "feat."/version markers, or any other
    meaningful artist/title text -- see module docstring's Version
    Safety discussion for why that line is never crossed here."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("‘", "'").replace("’", "'")
    text = text.strip().lower()
    return re.sub(r"\s+", " ", text)


def resolve_existing_entity(conn, canonical_artist, canonical_title, isrc=None):
    """Returns {"entity_id", "canonical_artist", "canonical_title",
    "resolution_method"} for an entity THIS observation should attach to,
    or {"entity_id": None, "canonical_artist": canonical_artist,
    "canonical_title": canonical_title, "resolution_method": UNRESOLVED}
    if nothing qualifies. Callers must create a new entity in the
    UNRESOLVED case -- this function never guesses, never returns a
    partial/low-confidence match dressed up as a real one. `canonical_
    artist`/`canonical_title` in the UNRESOLVED result are exactly the
    values passed in (verbatim), never altered."""
    if isrc:
        row = conn.execute(
            "SELECT music_entity_id FROM music_entity_aliases WHERE alias_type = 'ISRC' AND alias_value = ?",
            (isrc,),
        ).fetchone()
        if row is not None:
            entity = conn.execute(
                "SELECT canonical_artist, canonical_title FROM music_entities WHERE id = ?",
                (row["music_entity_id"],),
            ).fetchone()
            if entity is not None:
                return {
                    "entity_id": row["music_entity_id"],
                    "canonical_artist": entity["canonical_artist"],
                    "canonical_title": entity["canonical_title"],
                    "resolution_method": METHOD_EXTERNAL_ID,
                }

    norm_artist = normalize_for_matching(canonical_artist)
    norm_title = normalize_for_matching(canonical_title)
    if norm_artist and norm_title:
        candidates = conn.execute("SELECT id, canonical_artist, canonical_title FROM music_entities").fetchall()
        for row in candidates:
            if (normalize_for_matching(row["canonical_artist"]) == norm_artist
                    and normalize_for_matching(row["canonical_title"]) == norm_title):
                return {
                    "entity_id": row["id"],
                    "canonical_artist": row["canonical_artist"],
                    "canonical_title": row["canonical_title"],
                    "resolution_method": METHOD_EXACT_NORMALIZED_METADATA,
                }

    return {
        "entity_id": None, "canonical_artist": canonical_artist, "canonical_title": canonical_title,
        "resolution_method": UNRESOLVED,
    }
