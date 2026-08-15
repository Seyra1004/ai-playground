"""Small text-composition helpers shared across report modules -- kept in
one place so the same de-duplication rule can't silently drift between
where it's used for LLM grounding (report/producer_synthesis.py) and where
it's used for display (report/web_data_v2.py)."""


def dedupe_join(*parts, sep=" — "):
    """Joins non-empty text fragments, dropping any fragment that's
    already (case-insensitively, trimmed) a substring of a fragment
    already kept -- so text that just repeats an earlier fragment (e.g. a
    snippet restating the title, or a reason restating the snippet)
    doesn't appear twice. Order-preserving: earlier args win when a later
    one is redundant."""
    kept = []
    for part in parts:
        if not part:
            continue
        text = part.strip()
        if not text:
            continue
        if any(text.lower() in existing.lower() for existing in kept):
            continue
        kept.append(text)
    return sep.join(kept)


def is_redundant(candidate, reference):
    """True if `candidate` adds no text beyond what `reference` already
    says -- either is empty/None, or one is a case-insensitive substring
    of the other. Used to decide whether a second field (e.g. a snippet)
    is worth displaying alongside a first (e.g. a reason) at all."""
    if not candidate or not reference:
        return not candidate
    c, r = candidate.strip().lower(), reference.strip().lower()
    if not c:
        return True
    return c in r or r in c
