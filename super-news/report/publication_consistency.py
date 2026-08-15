"""Read-only invariant check: does the most recent real Kakao V2 digest's
report_date match what's actually published under docs/v2/?

Built after a real incident (see SUPER_NEWS_HANDOFF.md, "REAL KAKAO LINK
E2E INCIDENT"): a real Kakao V2 send for 2026-08-15 carried a link to a
real, live, HTTP-200 /v2/ page -- but that page was still rendering
2026-08-14 content. Kakao's own "sent" result and a 200 status code each
independently looked like a pass. Neither one checks the thing that
actually matters: whether the report_date a real user's message points to
is the SAME report_date the public page actually shows.

This module makes that specific failure mode impossible to silently pass.
check_publication_consistency() has exactly one CONSISTENT status and
several distinct non-consistent ones -- there is no default/fallback
branch that returns anything resembling "probably fine". A missing file,
an unparseable date, or no Kakao send yet are each their own explicit
status, and `consistent` is False for every one of them. HTTP status is
never consulted here at all -- this only compares report dates, precisely
because HTTP 200 already proved insufficient once.
"""

import re
from pathlib import Path

_TITLE_DATE_RE = re.compile(r"SUPER NEWS V2 — (\d{4})\.(\d{2})\.(\d{2})")


class PublicationConsistencyStatus:
    CONSISTENT = "CONSISTENT"
    MISMATCH = "MISMATCH"
    NO_KAKAO_SEND_YET = "NO_KAKAO_SEND_YET"
    INDEX_MISSING_OR_UNPARSEABLE = "INDEX_MISSING_OR_UNPARSEABLE"
    DATED_REPORT_MISSING_OR_UNPARSEABLE = "DATED_REPORT_MISSING_OR_UNPARSEABLE"


def _extract_page_date(html_path):
    """Returns 'YYYY-MM-DD' parsed from the page's own <title>, or None if
    the file doesn't exist or the title doesn't match the expected format.
    Never guesses and never returns a partial/best-effort date."""
    if not html_path.exists():
        return None
    text = html_path.read_text(encoding="utf-8")
    match = _TITLE_DATE_RE.search(text)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def _latest_sent_kakao_v2_report_date(conn):
    row = conn.execute(
        "SELECT report_date FROM delivery_history "
        "WHERE report_type='DAILY_DIGEST_V2' AND status='sent' "
        "ORDER BY delivered_at DESC LIMIT 1"
    ).fetchone()
    return row["report_date"] if row else None


def check_publication_consistency(conn, docs_v2_dir):
    """docs_v2_dir: path to the repo's docs/v2/ directory (contains
    index.html and reports/<date>.html). Returns:

        {"status": one of PublicationConsistencyStatus,
         "kakao_report_date": str|None,
         "public_index_date": str|None,
         "dated_report_date": str|None,
         "consistent": bool}

    `consistent` is True ONLY when status == CONSISTENT. Every other
    status -- including "no Kakao send has ever happened" -- is explicitly
    False, so a caller can never mistake "nothing to check yet" for
    "checked and fine"."""
    docs_v2_dir = Path(docs_v2_dir)

    kakao_report_date = _latest_sent_kakao_v2_report_date(conn)
    if not kakao_report_date:
        return {
            "status": PublicationConsistencyStatus.NO_KAKAO_SEND_YET,
            "kakao_report_date": None,
            "public_index_date": None,
            "dated_report_date": None,
            "consistent": False,
        }

    index_date = _extract_page_date(docs_v2_dir / "index.html")
    if index_date is None:
        return {
            "status": PublicationConsistencyStatus.INDEX_MISSING_OR_UNPARSEABLE,
            "kakao_report_date": kakao_report_date,
            "public_index_date": None,
            "dated_report_date": None,
            "consistent": False,
        }

    dated_report_date = _extract_page_date(docs_v2_dir / "reports" / f"{kakao_report_date}.html")
    if dated_report_date is None:
        return {
            "status": PublicationConsistencyStatus.DATED_REPORT_MISSING_OR_UNPARSEABLE,
            "kakao_report_date": kakao_report_date,
            "public_index_date": index_date,
            "dated_report_date": None,
            "consistent": False,
        }

    consistent = kakao_report_date == index_date == dated_report_date
    return {
        "status": (
            PublicationConsistencyStatus.CONSISTENT
            if consistent
            else PublicationConsistencyStatus.MISMATCH
        ),
        "kakao_report_date": kakao_report_date,
        "public_index_date": index_date,
        "dated_report_date": dated_report_date,
        "consistent": consistent,
    }
