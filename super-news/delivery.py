"""Delivery idempotency: decide whether to send, and record the outcome.

Sits above kakao/client.py (which only knows how to send ONE message unit)
and below the future report-assembly/orchestration layer. Long-form report
splitting/sequencing into multiple Kakao messages is NOT implemented here —
Phase 1A has no report content to split yet; this module only owns the
decide/record contract duplicate-prevention depends on.

Responsibility boundary (important — read before wiring this into an
orchestrator): a `sent` row here means ONLY "this one logical delivery
(report_date + report_type + destination) succeeded." It says NOTHING about
whether the rest of that day's required reports succeeded. The top-level
requirement is that EVERY scheduled required report for the day is
eventually sent — a future orchestration layer is responsible for checking
that `sent` exists for every entry in its own REQUIRED_REPORT_TYPES list
before declaring the whole daily run successful. delivery.py has no concept
of "the whole day succeeded" and must not be treated as if it did.

Resend policy (decided here, since it directly affects whether a required
report can silently go missing):
- If NO `sent` row exists yet for a given idempotency_key, decide_delivery_action
  always returns "send" — regardless of any prior `failed` attempts. Missing
  a required report is worse than a redundant attempt, so nothing here ever
  blocks a first successful delivery.
- If a `sent` row already exists for that key, it is NEVER sent again for
  that key — even if content_hash of a new attempt would differ from what
  was recorded. content_hash is stored for audit/debugging (what did we
  actually send), not as a gate: re-sending an already-delivered report
  just because its content changed would push a duplicate message to the
  user, which is explicitly out of bounds. A future "corrected report" use
  case should mint a distinct idempotency_key component (e.g. a different
  report_type/destination suffix), not silently resend under the same key.

Known limitation, accepted for Phase 1A (not solved with distributed
transactions here): the Kakao API call and the local `sent` record are two
separate operations with a real time gap between them. If the process
crashes AFTER Kakao confirms the send but BEFORE record_delivery(..., 'sent')
commits, the local DB will not know the send happened, and a later re-run
will see no `sent` row and will legitimately try again — which can produce
an actual duplicate message to the user in that specific crash window. This
is a fundamental at-least-once-vs-exactly-once tradeoff of any
external-side-effect + local-record pattern without two-phase commit across
Kakao and this DB; Phase 1A documents it rather than attempting to eliminate
it. Similarly, decide_delivery_action()'s SELECT and the eventual
record_delivery() INSERT are not one atomic operation — the database's
partial unique index (ux_delivery_sent_once on delivery_history) is the
actual backstop against two committed `sent` rows for the same key; a
concurrent duplicate INSERT attempt raises sqlite3.IntegrityError rather
than silently succeeding. Phase 1A runs as a single scheduled process, not
concurrently, so this is a documented edge case rather than something
actively guarded against with locking.
"""

from datetime import datetime, timezone

from db.database import connect

VALID_STATUSES = ("sent", "failed", "skipped_duplicate")

# Used as the separator between idempotency_key components. Rejecting it in
# every component (see build_idempotency_key) is what keeps the resulting
# key unambiguous without needing a hash.
_KEY_SEPARATOR = ":"


def build_idempotency_key(report_date, report_type, destination):
    """Deterministic key: report_date:report_type:destination.

    Each component must be non-empty and must not itself contain the ':'
    separator — this is what keeps the concatenation unambiguous (e.g.
    "a:b" + "c" could otherwise collide with "a" + "b:c") without
    introducing a hash for a 3-field key this small."""
    parts = {
        "report_date": report_date,
        "report_type": report_type,
        "destination": destination,
    }
    for name, value in parts.items():
        if not value:
            raise ValueError(f"{name} must be non-empty.")
        if _KEY_SEPARATOR in value:
            raise ValueError(
                f"{name} must not contain {_KEY_SEPARATOR!r} "
                "(reserved as the idempotency_key separator)."
            )
    return _KEY_SEPARATOR.join((report_date, report_type, destination))


def decide_delivery_action(idempotency_key, conn=None):
    """Returns "send" or "skip_duplicate".

    Duplicate protection keys off an explicit `sent` row existing for this
    idempotency_key — not merely a row existing. A `failed` row for the same
    key does NOT block a retry: this returns "send" whenever no `sent` row
    is found, even if earlier `failed` attempts exist. See the module
    docstring for the full resend policy and its rationale."""
    if not idempotency_key:
        raise ValueError("idempotency_key must be non-empty.")

    owns_conn = conn is None
    active_conn = conn if conn is not None else connect()
    try:
        row = active_conn.execute(
            "SELECT 1 FROM delivery_history WHERE idempotency_key = ? AND status = 'sent' LIMIT 1",
            (idempotency_key,),
        ).fetchone()
    finally:
        if owns_conn:
            active_conn.close()
    return "skip_duplicate" if row else "send"


def record_delivery(
    runs_row_id, report_date, report_type, destination, content_hash, status, conn=None, report_id=None
):
    """Insert a delivery_history row recording the outcome of a send attempt
    (or a decided skip). Does not call the Kakao API — the caller has
    already attempted (or decided not to attempt) the send.

    `runs_row_id` is the internal `runs.id` integer (the delivery_history.run_id
    FK target) — NOT the external `runs.run_id` uuid string; named
    differently here specifically to avoid that mix-up at call sites.

    `status` must be one of 'sent' | 'failed' | 'skipped_duplicate'.

    `report_id` is optional (defaults to None/NULL) and, once callers have a
    `reports.id` to pass, identifies exactly which generated report content
    this delivery corresponds to — needed once a report_date+report_type can
    have more than one generated report across distinct runs.

    Transaction semantics: if `conn` is provided by the caller, this function
    only executes the INSERT — it never commits or closes a connection it
    doesn't own, so it can't cut a wider caller-managed transaction short.
    If `conn` is omitted, this function opens its own connection and is
    fully self-contained (commits and closes it before returning)."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid delivery status: {status!r}")
    if not isinstance(runs_row_id, int) or isinstance(runs_row_id, bool) or runs_row_id <= 0:
        raise ValueError(f"runs_row_id must be a positive int, got {runs_row_id!r}")
    if not content_hash:
        raise ValueError("content_hash must be non-empty.")

    idempotency_key = build_idempotency_key(report_date, report_type, destination)
    delivered_at = datetime.now(timezone.utc).isoformat() if status == "sent" else None

    insert_sql = """
        INSERT INTO delivery_history
            (run_id, report_date, report_type, destination, idempotency_key,
             content_hash, delivered_at, status, report_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        runs_row_id,
        report_date,
        report_type,
        destination,
        idempotency_key,
        content_hash,
        delivered_at,
        status,
        report_id,
    )

    if conn is not None:
        conn.execute(insert_sql, params)
        return

    owned_conn = connect()
    try:
        owned_conn.execute(insert_sql, params)
        owned_conn.commit()
    finally:
        owned_conn.close()
