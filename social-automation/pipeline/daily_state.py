from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date as _date

_NON_WORD_RE = re.compile(r"[^\w가-힣]")
_WHITESPACE_RE = re.compile(r"\s+")


def compute_topic_fingerprint(topic: str) -> str:
    """Deterministic normalized fingerprint used both to dedupe same-day
    candidates and to detect a topic that ran too recently."""
    normalized = _WHITESPACE_RE.sub("", topic.strip().lower())
    normalized = _NON_WORD_RE.sub("", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def dedupe_candidates(candidates: list) -> list:
    seen = set()
    deduped = []
    for c in candidates:
        fp = compute_topic_fingerprint(c.topic)
        if fp in seen:
            continue
        seen.add(fp)
        deduped.append(c)
    return deduped


def make_run_id(account_id: str, run_date: str) -> str:
    return f"{account_id}:{run_date}"


def get_run(conn: sqlite3.Connection, account_id: str, run_date: str):
    return conn.execute(
        "SELECT * FROM runs WHERE run_id = ?", (make_run_id(account_id, run_date),)
    ).fetchone()


def upsert_run(
    conn: sqlite3.Connection,
    account_id: str,
    run_date: str,
    status: str,
    content_id: str = None,
    topic_fingerprint: str = None,
    retry_count: int = 0,
    started_at: str = None,
    finished_at: str = None,
) -> None:
    run_id = make_run_id(account_id, run_date)
    existing = get_run(conn, account_id, run_date)
    if existing is None:
        conn.execute(
            "INSERT INTO runs (run_id, account_id, run_date, content_id, topic_fingerprint, status, "
            "retry_count, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, account_id, run_date, content_id, topic_fingerprint, status, retry_count, started_at, finished_at),
        )
    else:
        conn.execute(
            "UPDATE runs SET "
            "content_id=COALESCE(?, content_id), "
            "topic_fingerprint=COALESCE(?, topic_fingerprint), "
            "status=?, retry_count=?, "
            "started_at=COALESCE(?, started_at), "
            "finished_at=COALESCE(?, finished_at) "
            "WHERE run_id=?",
            (content_id, topic_fingerprint, status, retry_count, started_at, finished_at, run_id),
        )
    conn.commit()


def recent_topic_fingerprints(conn: sqlite3.Connection, account_id: str, before_date: str, window_days: int) -> set:
    """Fingerprints of topics this account already published/reviewed within
    `window_days` before `before_date`, used to penalize picking the same
    topic again too soon."""
    rows = conn.execute(
        "SELECT run_date, topic_fingerprint FROM runs WHERE account_id=? AND topic_fingerprint IS NOT NULL "
        "AND status IN ('COMPLETE', 'NEEDS_REVIEW') AND run_date < ?",
        (account_id, before_date),
    ).fetchall()

    cutoff = _date.fromisoformat(before_date).toordinal() - window_days
    result = set()
    for r in rows:
        try:
            d = _date.fromisoformat(r["run_date"]).toordinal()
        except ValueError:
            continue
        if d >= cutoff:
            result.add(r["topic_fingerprint"])
    return result


def apply_recency_penalty(candidates: list, recent_fingerprints: set) -> None:
    """Mutates candidates in place: any candidate whose topic fingerprint
    matches recent history gets its duplication_penalty_signal pushed above
    core.scoring's DUPLICATION_REJECT_THRESHOLD, so the existing scoring gate
    (not a second copy of that logic) rejects it."""
    for c in candidates:
        fp = compute_topic_fingerprint(c.topic)
        if fp in recent_fingerprints:
            c.duplication_penalty_signal = max(c.duplication_penalty_signal, 0.9)


# A run happens every day forever, so "recent" (RECENCY_WINDOW_DAYS) isn't
# enough to guarantee a topic is never repeated -- pass this as
# recent_topic_fingerprints' window_days to get an effectively permanent,
# all-time set instead (reuses that function unchanged rather than adding a
# parallel "all time" query).
PERMANENT_WINDOW_DAYS = 36500

# A same-story follow-up article (different headline, same underlying
# event/policy) shares most of its substantive keywords even when titles
# differ -- Jaccard overlap at/above this is treated as the same story.
TOPIC_SIMILARITY_THRESHOLD = 0.5

_TOPIC_STOPWORDS = {"관련", "실시", "위한", "대한", "발표", "안내", "추진", "개최", "실태", "제도개선", "등"}
_TOPIC_SPLIT_RE = re.compile(r"[\s,·.!?():\[\]{}\-]+")


def _topic_keyword_set(topic: str) -> set:
    """Deterministic keyword set for near-duplicate detection -- splits on
    whitespace/punctuation, stems common Korean particles (reuses qa.
    content_qa's existing stemmer instead of a second copy of that logic),
    and drops short/boilerplate press-release words. No LLM call."""
    from qa.content_qa import _korean_stem

    tokens = (t for t in _TOPIC_SPLIT_RE.split(topic) if len(t) >= 2)
    stems = {_korean_stem(t) for t in tokens}
    return {s for s in stems if len(s) >= 2 and s not in _TOPIC_STOPWORDS}


def topic_similarity(a: str, b: str) -> float:
    """Jaccard similarity of two topics' keyword sets, 0.0-1.0."""
    sa, sb = _topic_keyword_set(a), _topic_keyword_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# Keyword-Jaccard alone misses a same-event follow-up whose headline is
# worded very differently from the original (e.g. a wrap-up article about
# "the 8/20 meeting" vs the original announcement of it) -- a shared
# distinctive number/date anchor (a specific date, a "2차/3rd session"
# ordinal, a specific amount) from the SAME source board is strong
# corroborating evidence they're the same specific event, even at only
# moderate keyword overlap. Two candidates from different boards, or with
# no shared distinctive number, still need the stricter fallback below.
_NUMERIC_TOKEN_RE = re.compile(r"\d[\d,.]*\s*(?:차|월|일|년|주년|개월|명|만원|억원|원|%)?")


def _numeric_tokens(topic: str) -> set:
    return {t.strip() for t in _NUMERIC_TOKEN_RE.findall(topic) if len(t.strip()) >= 2}


def _board_id_of(candidate_id: str) -> str:
    """The source-board portion of a candidate_id (e.g. "nts-press" from
    "nts-press-1354268") -- a deterministic stand-in for publisher/source
    identity without needing a schema change to store it separately."""
    return candidate_id.rsplit("-", 1)[0] if candidate_id else ""


# A near-verbatim reworded headline (no shared board/number needed) is
# still the same story at high enough raw keyword overlap.
HIGH_TEXT_SIMILARITY_THRESHOLD = 0.65


# Same board + a shared distinctive number/date anchor (a specific date,
# "2차/3rd session", a specific amount) is strong enough evidence on its
# own that a lower keyword-overlap bar still means the same specific
# event -- a heavily reworded follow-up article can otherwise fall well
# under TOPIC_SIMILARITY_THRESHOLD.
SAME_BOARD_WITH_NUMBER_THRESHOLD = 0.35


def is_same_story(a_candidate_id: str, a_topic: str, b_candidate_id: str, b_topic: str) -> bool:
    """Canonical-story identity check combining several deterministic
    signals (no LLM): same source board lowers the keyword-overlap bar
    (further still when they also share a distinctive number/date anchor,
    e.g. both mention "2차" or "8.20" -- almost certainly the same specific
    event); otherwise falls back to a stricter keyword-overlap-only
    threshold for a near-identical headline from any source. A candidate
    with a genuinely different number/date for the same kind of event
    (e.g. "3차" replacing "2차") or only a broad category in common is
    treated as a different/new story, not a duplicate."""
    jac = topic_similarity(a_topic, b_topic)
    if _board_id_of(a_candidate_id) == _board_id_of(b_candidate_id):
        shared_numbers = _numeric_tokens(a_topic) & _numeric_tokens(b_topic)
        threshold = SAME_BOARD_WITH_NUMBER_THRESHOLD if shared_numbers else TOPIC_SIMILARITY_THRESHOLD
        if jac >= threshold:
            return True
    return jac >= HIGH_TEXT_SIMILARITY_THRESHOLD


def historical_topics(conn: sqlite3.Connection, account_id: str) -> list:
    """Every topic ever finally selected for this account, permanently --
    (candidate_id, topic) pairs from the `topics` table (already defined in
    core/database.py's schema; this is its first reader/writer)."""
    rows = conn.execute(
        "SELECT candidate_id, topic FROM topics WHERE account_id = ?", (account_id,)
    ).fetchall()
    return [(r["candidate_id"], r["topic"]) for r in rows]


def reject_previously_used_candidates(
    conn: sqlite3.Connection, account_id: str, candidates: list, all_time_fingerprints: set
) -> None:
    """Mutates candidates in place: permanently rejects (a) the exact same
    source/candidate_id ever selected before, (b) an exact/near-exact title
    fingerprint match at any time (not just a recent window), and (c) a
    near-duplicate follow-up about the same underlying story (see
    is_same_story's combined signals) -- by pushing duplication_penalty_signal
    above core.scoring's existing DUPLICATION_REJECT_THRESHOLD, so the
    existing scoring gate (not a second copy of that rejection logic) does
    the actual reject. A candidate that fails this always falls through to
    the next ranked candidate in scripts/run_daily.py's existing selection
    loop."""
    history = historical_topics(conn, account_id)
    seen_ids = {cid for cid, _ in history if cid}

    for c in candidates:
        if c.candidate_id in seen_ids:
            c.duplication_penalty_signal = max(c.duplication_penalty_signal, 0.95)
            continue
        if compute_topic_fingerprint(c.topic) in all_time_fingerprints:
            c.duplication_penalty_signal = max(c.duplication_penalty_signal, 0.95)
            continue
        if any(is_same_story(c.candidate_id, c.topic, hid, htopic) for hid, htopic in history if htopic):
            c.duplication_penalty_signal = max(c.duplication_penalty_signal, 0.9)


def record_selected_topic(
    conn: sqlite3.Connection,
    account_id: str,
    candidate_id: str,
    topic: str,
    category: str,
    score: float,
    status: str,
    created_at: str,
) -> None:
    """Persists the finally-selected topic into the `topics` table so future
    runs (including after a restart) permanently reject it -- the actual
    persistence requirement, not just an in-memory/per-run check."""
    conn.execute(
        "INSERT INTO topics (candidate_id, account_id, topic, category, score, status, urgent, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?) "
        "ON CONFLICT(candidate_id) DO UPDATE SET status=excluded.status, score=excluded.score",
        (candidate_id, account_id, topic, category, score, status, created_at),
    )
    conn.commit()
