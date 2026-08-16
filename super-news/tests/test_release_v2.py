"""report.release_v2: local pre-publish verification, exact-file git
publication, and real external public-page verification -- the REQUIRED
DAILY V2 WEB FLOW gates (quality-hardening phase). Git and HTTP are always
faked here -- no real repo mutation, no real network call under pytest."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from db.database import connect, init_db
from report.release_v2 import (
    ReleaseCheckStatus,
    ReleaseStatus,
    find_secret_exposure,
    has_music_intelligence_marker,
    publish_v2_dashboard,
    run_daily_v2_release,
    verify_external_v2_dashboard,
    verify_local_v2_dashboard,
)

_GOOD_INDEX_HTML = (
    "<html><head><title>SUPER NEWS V2 — 2026.08.15</title></head>"
    "<body><div class=\"music-domain-header\"><h2>MUSIC INTELLIGENCE</h2></div></body></html>"
)
_GOOD_DATED_HTML = (
    "<html><head><title>SUPER NEWS V2 — 2026.08.15</title></head>"
    "<body>real content</body></html>"
)


def _write_dashboard(tmp_path, index_html=_GOOD_INDEX_HTML, dated_html=_GOOD_DATED_HTML, report_date="2026-08-15"):
    docs_v2 = tmp_path / "docs" / "v2"
    (docs_v2 / "reports").mkdir(parents=True)
    (docs_v2 / "index.html").write_text(index_html, encoding="utf-8")
    (docs_v2 / "reports" / f"{report_date}.html").write_text(dated_html, encoding="utf-8")
    return docs_v2


# ---- find_secret_exposure / has_music_intelligence_marker -------------------


def test_find_secret_exposure_clean_text():
    assert find_secret_exposure("real news content, nothing sensitive here") == []


def test_find_secret_exposure_anthropic_key():
    assert "anthropic_api_key" in find_secret_exposure("key=sk-ant-abcdefghijklmnopqrstuvwxyz012345")


def test_find_secret_exposure_never_leaks_the_matched_value():
    reasons = find_secret_exposure("key=sk-ant-abcdefghijklmnopqrstuvwxyz012345")
    assert "sk-ant-" not in str(reasons)


def test_has_music_intelligence_marker_true_for_real_marker():
    assert has_music_intelligence_marker(_GOOD_INDEX_HTML)


def test_has_music_intelligence_marker_false_when_absent():
    assert not has_music_intelligence_marker("<html><body>no music section</body></html>")


# ---- verify_local_v2_dashboard -----------------------------------------------


def test_local_verify_passes_for_correct_same_date_dashboard(tmp_path):
    docs_v2 = _write_dashboard(tmp_path)
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is True
    assert result["status"] == ReleaseCheckStatus.OK


def test_local_verify_fails_when_index_missing(tmp_path):
    docs_v2 = tmp_path / "docs" / "v2"
    (docs_v2 / "reports").mkdir(parents=True)
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.INDEX_MISSING_OR_UNPARSEABLE


def test_local_verify_fails_on_stale_index_date(tmp_path):
    stale_index = _GOOD_INDEX_HTML.replace("2026.08.15", "2026.08.14")
    docs_v2 = _write_dashboard(tmp_path, index_html=stale_index)
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.INDEX_DATE_MISMATCH


def test_local_verify_fails_on_dated_report_date_mismatch(tmp_path):
    stale_dated = _GOOD_DATED_HTML.replace("2026.08.15", "2026.08.14")
    docs_v2 = _write_dashboard(tmp_path, dated_html=stale_dated)
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.DATED_REPORT_DATE_MISMATCH


def test_local_verify_fails_when_dated_report_missing(tmp_path):
    docs_v2 = tmp_path / "docs" / "v2"
    (docs_v2 / "reports").mkdir(parents=True)
    (docs_v2 / "index.html").write_text(_GOOD_INDEX_HTML, encoding="utf-8")
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.DATED_REPORT_MISSING_OR_UNPARSEABLE


def test_local_verify_fails_when_music_intelligence_marker_missing(tmp_path):
    no_music_index = (
        "<html><head><title>SUPER NEWS V2 — 2026.08.15</title></head><body>no music section</body></html>"
    )
    docs_v2 = _write_dashboard(tmp_path, index_html=no_music_index)
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.MUSIC_INTELLIGENCE_MISSING


def test_local_verify_fails_on_secret_exposure(tmp_path):
    leaky_index = _GOOD_INDEX_HTML.replace(
        "</body>", "<!-- key=sk-ant-abcdefghijklmnopqrstuvwxyz012345 --></body>"
    )
    docs_v2 = _write_dashboard(tmp_path, index_html=leaky_index)
    result = verify_local_v2_dashboard("2026-08-15", docs_v2)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.SECRET_EXPOSURE


# ---- verify_external_v2_dashboard (real HTTP GET, always faked here) --------


def _fake_response(status_code, text):
    return SimpleNamespace(status_code=status_code, text=text)


def test_external_verify_passes_for_correct_live_pages():
    def fake_get(url, timeout):
        return _fake_response(200, _GOOD_INDEX_HTML if url.endswith("/v2/") else _GOOD_DATED_HTML)

    result = verify_external_v2_dashboard("2026-08-15", http_get=fake_get)
    assert result["ok"] is True


def test_external_verify_http_200_alone_is_never_pass_when_date_is_stale():
    stale_index = _GOOD_INDEX_HTML.replace("2026.08.15", "2026.08.14")

    def fake_get(url, timeout):
        return _fake_response(200, stale_index if url.endswith("/v2/") else _GOOD_DATED_HTML)

    result = verify_external_v2_dashboard("2026-08-15", http_get=fake_get)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.INDEX_DATE_MISMATCH


def test_external_verify_non_200_fails():
    def fake_get(url, timeout):
        return _fake_response(404, "")

    result = verify_external_v2_dashboard("2026-08-15", http_get=fake_get)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.HTTP_ERROR


def test_external_verify_network_exception_fails_not_raises():
    def fake_get(url, timeout):
        raise ConnectionError("network down")

    result = verify_external_v2_dashboard("2026-08-15", http_get=fake_get)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.HTTP_ERROR


def test_external_verify_dated_report_date_mismatch_fails():
    stale_dated = _GOOD_DATED_HTML.replace("2026.08.15", "2026.08.14")

    def fake_get(url, timeout):
        return _fake_response(200, _GOOD_INDEX_HTML if url.endswith("/v2/") else stale_dated)

    result = verify_external_v2_dashboard("2026-08-15", http_get=fake_get)
    assert result["ok"] is False
    assert result["status"] == ReleaseCheckStatus.DATED_REPORT_DATE_MISMATCH


def test_external_verify_uses_the_real_expected_urls():
    seen_urls = []

    def fake_get(url, timeout):
        seen_urls.append(url)
        return _fake_response(200, _GOOD_INDEX_HTML if url.endswith("/v2/") else _GOOD_DATED_HTML)

    verify_external_v2_dashboard("2026-08-15", http_get=fake_get)
    assert seen_urls == [
        "https://seyra1004.github.io/ai-playground/v2/",
        "https://seyra1004.github.io/ai-playground/v2/reports/2026-08-15.html",
    ]


# ---- publish_v2_dashboard (git, always faked here) ---------------------------


class _FakeGit:
    """Records every call; simulates a real git index closely enough for
    this module's own logic: `add` actually stages the given paths,
    `diff --cached --name-only` reflects whatever is currently staged,
    `reset` clears it."""

    def __init__(self, pre_staged=""):
        self.calls = []
        self._staged = [line for line in pre_staged.splitlines() if line.strip()]

    def __call__(self, args, cwd):
        self.calls.append(list(args))
        if args[:2] == ["diff", "--cached"]:
            return SimpleNamespace(returncode=0, stdout="\n".join(self._staged), stderr="")
        if args[0] == "add":
            self._staged = list(args[2:])  # args = ["add", "--", *paths]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "reset":
            self._staged = []
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "commit":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "push":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected git call: {args}")


def _repo_with_dashboard(tmp_path):
    repo_root = tmp_path
    docs_v2 = _write_dashboard(repo_root)
    return repo_root, docs_v2


def test_publish_stages_exactly_the_two_expected_files(tmp_path):
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()
    result = publish_v2_dashboard("2026-08-15", docs_v2, repo_root, push=True, git_runner=git)
    assert result == {"published": True, "pushed": True, "reason": None}
    add_call = next(c for c in git.calls if c[0] == "add")
    assert add_call == ["add", "--", "docs/v2/index.html", "docs/v2/reports/2026-08-15.html"]
    assert ["push", "origin", "main"] in git.calls


def test_publish_never_uses_broad_add(tmp_path):
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()
    publish_v2_dashboard("2026-08-15", docs_v2, repo_root, push=True, git_runner=git)
    add_call = next(c for c in git.calls if c[0] == "add")
    assert "." not in add_call
    assert "-A" not in add_call


def test_publish_aborts_when_unrelated_file_already_staged(tmp_path):
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit(pre_staged="some/unrelated/file.py\n")
    result = publish_v2_dashboard("2026-08-15", docs_v2, repo_root, push=True, git_runner=git)
    assert result["published"] is False
    assert "already staged" in result["reason"]
    assert not any(c[0] == "add" for c in git.calls)  # never even attempted to stage


def test_publish_does_not_push_when_push_false(tmp_path):
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()
    result = publish_v2_dashboard("2026-08-15", docs_v2, repo_root, push=False, git_runner=git)
    assert result == {"published": True, "pushed": False, "reason": None}
    assert not any(c[0] == "push" for c in git.calls)


def test_publish_reports_failure_when_push_fails(tmp_path):
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)

    class _FailPushGit(_FakeGit):
        def __call__(self, args, cwd):
            if args[0] == "push":
                self.calls.append(list(args))
                return SimpleNamespace(returncode=1, stdout="", stderr="remote rejected")
            return super().__call__(args, cwd)

    git = _FailPushGit()
    result = publish_v2_dashboard("2026-08-15", docs_v2, repo_root, push=True, git_runner=git)
    assert result["published"] is True  # commit succeeded locally
    assert result["pushed"] is False
    assert "push failed" in result["reason"]


# =============================================================================
# run_daily_v2_release: the full REQUIRED DAILY V2 WEB FLOW orchestration.
# Real sqlite DB (schema only); git and HTTP always faked; Kakao's own
# send_memo is always mocked -- no real network/repo mutation under pytest.
# =============================================================================


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    c = connect(db_path=db_path)
    yield c
    c.close()


def _insert_run(conn, run_id, run_date="2026-08-15"):
    conn.execute(
        "INSERT INTO runs (run_id, run_date, started_at, status) VALUES (?, ?, 'x', 'completed')",
        (run_id, run_date),
    )
    conn.commit()
    return conn.execute("SELECT id FROM runs WHERE run_id=?", (run_id,)).fetchone()["id"]


def _insert_producer_intelligence(conn, run_row_id):
    output = {"insights": [{
        "what_is_moving": "관측된 신호", "why_it_matters": "근거가 되는 합리적인 해석",
        "what_to_watch": "다음 관찰 포인트", "what_could_i_make_now": "구체적인 아이디어",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }]}
    conn.execute(
        """INSERT INTO llm_interpretations
           (run_id, category, model_used, prompt_version, input_hash, output_text, confidence, created_at)
           VALUES (?, 'MUSIC_PRODUCER_INTELLIGENCE', 'm', 'v1', 'h', ?, 'MEDIUM', 'x')""",
        (run_row_id, json.dumps(output, ensure_ascii=False)),
    )
    conn.commit()


def _fake_http_get_for(index_html, dated_html):
    def fake_get(url, timeout):
        return _fake_response(200, index_html if url.endswith("/v2/") else dated_html)
    return fake_get


def test_release_pass_end_to_end(conn, tmp_path):
    report_row_id = _insert_run(conn, "run-report")
    _insert_producer_intelligence(conn, report_row_id)
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()

    with patch("report_delivery_v2.send_memo") as mock_send:
        result = run_daily_v2_release(
            conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
            git_runner=git, http_get=_fake_http_get_for(_GOOD_INDEX_HTML, _GOOD_DATED_HTML),
        )

    assert result["status"] == ReleaseStatus.PASS_
    assert result["reason"] is None
    assert mock_send.call_count == 1
    assert result["consistency"]["consistent"] is True


def test_release_publish_blocked_never_touches_git_http_or_kakao(conn, tmp_path):
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root = tmp_path
    docs_v2 = _write_dashboard(repo_root, index_html=_GOOD_INDEX_HTML.replace("2026.08.15", "2026.08.14"))
    git = _FakeGit()

    with patch("report_delivery_v2.send_memo") as mock_send:
        result = run_daily_v2_release(
            conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
            git_runner=git, http_get=_fake_http_get_for(_GOOD_INDEX_HTML, _GOOD_DATED_HTML),
        )

    assert result["status"] == ReleaseStatus.PUBLISH_BLOCKED
    assert not git.calls  # publish never even attempted
    mock_send.assert_not_called()


def test_release_publish_push_failure_blocks_kakao(conn, tmp_path):
    report_row_id = _insert_run(conn, "run-report")
    _insert_producer_intelligence(conn, report_row_id)
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)

    class _FailPushGit(_FakeGit):
        def __call__(self, args, cwd):
            if args[0] == "push":
                self.calls.append(list(args))
                return SimpleNamespace(returncode=1, stdout="", stderr="remote rejected")
            return super().__call__(args, cwd)

    git = _FailPushGit()
    with patch("report_delivery_v2.send_memo") as mock_send:
        result = run_daily_v2_release(
            conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
            git_runner=git, http_get=_fake_http_get_for(_GOOD_INDEX_HTML, _GOOD_DATED_HTML),
        )

    assert result["status"] == ReleaseStatus.PUBLISH_FAILED
    mock_send.assert_not_called()  # publish failure blocks Kakao


def test_release_stale_external_page_blocks_kakao(conn, tmp_path):
    report_row_id = _insert_run(conn, "run-report")
    _insert_producer_intelligence(conn, report_row_id)
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()
    stale_public_index = _GOOD_INDEX_HTML.replace("2026.08.15", "2026.08.14")

    with patch("report_delivery_v2.send_memo") as mock_send:
        result = run_daily_v2_release(
            conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
            git_runner=git, http_get=_fake_http_get_for(stale_public_index, _GOOD_DATED_HTML),
        )

    assert result["status"] == ReleaseStatus.EXTERNAL_VERIFICATION_FAILED
    assert result["external_check"]["status"] == ReleaseCheckStatus.INDEX_DATE_MISMATCH
    mock_send.assert_not_called()  # stale external page blocks Kakao


def test_release_external_http_200_with_wrong_date_is_fail(conn, tmp_path):
    """HTTP 200 alone is never PASS -- a real 200 with the wrong report
    date must still fail the release."""
    report_row_id = _insert_run(conn, "run-report")
    _insert_producer_intelligence(conn, report_row_id)
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()
    wrong_date_index = _GOOD_INDEX_HTML.replace("2026.08.15", "2026.08.10")

    result = run_daily_v2_release(
        conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
        git_runner=git, http_get=_fake_http_get_for(wrong_date_index, _GOOD_DATED_HTML),
    )
    assert result["status"] == ReleaseStatus.EXTERNAL_VERIFICATION_FAILED
    assert result["external_check"]["ok"] is False


def test_release_post_send_mismatch_is_overall_fail_even_though_kakao_sent(conn, tmp_path):
    """Kakao send success alone is never PASS -- if the REQUIRED post-send
    report.publication_consistency check ever comes back non-consistent
    (by construction, every earlier gate in this orchestrator already
    guarantees local/external date agreement, so this exercises the
    orchestrator's own wiring/gating logic directly rather than trying to
    contrive an inconsistent real state past gates designed to prevent
    exactly that), the overall release must still report FAIL, never
    PASS, even though the real Kakao send itself already succeeded."""
    report_row_id = _insert_run(conn, "run-report")
    _insert_producer_intelligence(conn, report_row_id)
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()

    fake_mismatch = {
        "status": "MISMATCH", "kakao_report_date": "2026-08-15",
        "public_index_date": "2026-08-14", "dated_report_date": "2026-08-15",
        "consistent": False,
    }
    with patch("report_delivery_v2.send_memo") as mock_send, \
         patch("report.publication_consistency.check_publication_consistency", return_value=fake_mismatch):
        result = run_daily_v2_release(
            conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
            git_runner=git, http_get=_fake_http_get_for(_GOOD_INDEX_HTML, _GOOD_DATED_HTML),
        )

    mock_send.assert_called_once()  # the real send DID succeed...
    assert result["status"] == ReleaseStatus.POST_SEND_CONSISTENCY_FAILED  # ...but overall status is still FAIL
    assert result["consistency"]["consistent"] is False


def test_release_unknown_consistency_state_is_fail(conn, tmp_path):
    """An UNKNOWN/PENDING-shaped consistency result (consistent=False but
    status != MISMATCH) must also be treated as overall FAIL, never PASS
    -- there is no default/fallback branch that reads an unrecognized
    status as fine."""
    report_row_id = _insert_run(conn, "run-report")
    _insert_producer_intelligence(conn, report_row_id)
    delivery_row_id = _insert_run(conn, "run-delivery")
    repo_root, docs_v2 = _repo_with_dashboard(tmp_path)
    git = _FakeGit()

    fake_pending = {
        "status": "NO_KAKAO_SEND_YET", "kakao_report_date": None,
        "public_index_date": None, "dated_report_date": None, "consistent": False,
    }
    with patch("report_delivery_v2.send_memo"), \
         patch("report.publication_consistency.check_publication_consistency", return_value=fake_pending):
        result = run_daily_v2_release(
            conn, "2026-08-15", docs_v2, repo_root, delivery_row_id,
            git_runner=git, http_get=_fake_http_get_for(_GOOD_INDEX_HTML, _GOOD_DATED_HTML),
        )

    assert result["status"] == ReleaseStatus.POST_SEND_CONSISTENCY_FAILED
    assert result["status"] != ReleaseStatus.PASS_


def test_check_publication_consistency_no_send_yet_is_never_consistent(conn, tmp_path):
    """Direct contract check: report.publication_consistency itself must
    report consistent=False (never a default pass) when no real Kakao
    send has ever happened yet, regardless of how correct the local files
    look."""
    from report.publication_consistency import PublicationConsistencyStatus, check_publication_consistency

    docs_v2 = _write_dashboard(tmp_path)
    result = check_publication_consistency(conn, docs_v2)
    assert result["status"] == PublicationConsistencyStatus.NO_KAKAO_SEND_YET
    assert result["consistent"] is False
