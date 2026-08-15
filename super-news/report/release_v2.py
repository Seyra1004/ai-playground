"""SAME-DATE V2 release gate: local pre-publish verification, exact-file
git publication, real external public-page verification, and the required
post-send publication-consistency check -- the REQUIRED DAILY V2 WEB FLOW
(quality-hardening phase):

    generate SAME-DATE V2 dashboard (scripts/generate_daily_web_report_v2.py,
    unchanged, already real)
    -> verify_local_v2_dashboard (THIS module -- blocks publish on failure)
    -> publish_v2_dashboard (THIS module -- git commit+push, exact files only)
    -> verify_external_v2_dashboard (THIS module -- real HTTP GET, blocks
       Kakao on failure)
    -> exactly ONE Kakao send (report_delivery_v2.deliver_daily_summary_v2)
    -> check_publication_consistency (report/publication_consistency.py,
       already built -- now REQUIRED and reported in the final status)

Every gate below is fail-closed: "unknown"/"pending"/an exception is always
treated as NOT passing, never as a default pass. HTTP 200 alone, git push
succeeding alone, and a local file existing alone are each explicitly
insufficient on their own -- see the real 2026-08-15 stale-dashboard
incident this project's own SUPER_NEWS_HANDOFF.md records, which is
exactly the failure mode every check here exists to make impossible again.
"""

import re
import subprocess
from pathlib import Path

from report.publication_consistency import _extract_page_date

# Same MUSIC-is-the-primary-domain contract report/web_render_v2.py's own
# _render_music_domain_header() renders unconditionally (static text, no
# data dependency) on every real generation -- its absence means either a
# stale pre-MUSIC-domain page or a broken render, never an acceptable
# publish target.
_MUSIC_INTELLIGENCE_MARKER = ">MUSIC INTELLIGENCE<"

# Deliberately conservative: only high-confidence secret SHAPES, never a
# broad word-based guess that could false-positive on ordinary content.
# Matched values are NEVER included in a returned reason string (only the
# pattern's own label) -- the no-secret-value-in-any-log/report discipline
# this project holds everywhere else (see db/backup.py's own docstring)
# applies here too.
_SECRET_PATTERNS = (
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{20,}")),
    ("kakao_access_token_assignment", re.compile(r"(?i)kakao[_-]?(access|refresh)[_-]?token\s*[:=]\s*['\"]?[A-Za-z0-9._-]{10,}")),
)


def find_secret_exposure(text):
    """Returns a list of matched pattern LABELS only (never the matched
    value itself) -- empty list means clean. Used identically on local
    file content and real external page content."""
    return [label for label, pattern in _SECRET_PATTERNS if pattern.search(text)]


def has_music_intelligence_marker(html_text):
    return _MUSIC_INTELLIGENCE_MARKER in (html_text or "")


class ReleaseCheckStatus:
    OK = "OK"
    INDEX_MISSING_OR_UNPARSEABLE = "INDEX_MISSING_OR_UNPARSEABLE"
    DATED_REPORT_MISSING_OR_UNPARSEABLE = "DATED_REPORT_MISSING_OR_UNPARSEABLE"
    INDEX_DATE_MISMATCH = "INDEX_DATE_MISMATCH"
    DATED_REPORT_DATE_MISMATCH = "DATED_REPORT_DATE_MISMATCH"
    MUSIC_INTELLIGENCE_MISSING = "MUSIC_INTELLIGENCE_MISSING"
    SECRET_EXPOSURE = "SECRET_EXPOSURE"
    HTTP_ERROR = "HTTP_ERROR"


def verify_local_v2_dashboard(report_date_kst, docs_v2_dir):
    """Reads the LOCAL, already-generated docs/v2/index.html and
    docs/v2/reports/<report_date_kst>.html (never regenerates them --
    that's scripts/generate_daily_web_report_v2.py's own job, upstream of
    this gate). Returns {"ok": bool, "status": one of ReleaseCheckStatus
    (only the FIRST failing check when ok=False -- fail-fast, matching
    report.publication_consistency's own one-status contract), "reasons":
    [str, ...]}. `ok=True` requires ALL of: both files exist and parse a
    real <title> date, both dates equal report_date_kst exactly (this IS
    the "no stale previous dashboard" check -- a stale file's title date
    would differ), the MUSIC INTELLIGENCE domain marker is present in the
    index page, and neither file contains a real secret-shaped pattern."""
    docs_v2_dir = Path(docs_v2_dir)
    index_path = docs_v2_dir / "index.html"
    dated_path = docs_v2_dir / "reports" / f"{report_date_kst}.html"

    index_date = _extract_page_date(index_path)
    if index_date is None:
        return {"ok": False, "status": ReleaseCheckStatus.INDEX_MISSING_OR_UNPARSEABLE,
                "reasons": [f"docs/v2/index.html missing or its <title> date is unparseable"]}
    if index_date != report_date_kst:
        return {"ok": False, "status": ReleaseCheckStatus.INDEX_DATE_MISMATCH,
                "reasons": [f"index.html date {index_date!r} != REPORT_DATE {report_date_kst!r} (stale dashboard)"]}

    dated_date = _extract_page_date(dated_path)
    if dated_date is None:
        return {"ok": False, "status": ReleaseCheckStatus.DATED_REPORT_MISSING_OR_UNPARSEABLE,
                "reasons": [f"docs/v2/reports/{report_date_kst}.html missing or its <title> date is unparseable"]}
    if dated_date != report_date_kst:
        return {"ok": False, "status": ReleaseCheckStatus.DATED_REPORT_DATE_MISMATCH,
                "reasons": [f"dated report date {dated_date!r} != REPORT_DATE {report_date_kst!r}"]}

    index_text = index_path.read_text(encoding="utf-8")
    if not has_music_intelligence_marker(index_text):
        return {"ok": False, "status": ReleaseCheckStatus.MUSIC_INTELLIGENCE_MISSING,
                "reasons": ["MUSIC INTELLIGENCE domain header missing from index.html"]}

    dated_text = dated_path.read_text(encoding="utf-8")
    secrets_found = find_secret_exposure(index_text) + find_secret_exposure(dated_text)
    if secrets_found:
        return {"ok": False, "status": ReleaseCheckStatus.SECRET_EXPOSURE,
                "reasons": [f"secret-shaped pattern(s) found: {sorted(set(secrets_found))}"]}

    return {"ok": True, "status": ReleaseCheckStatus.OK, "reasons": []}


DEFAULT_PUBLIC_BASE_URL = "https://seyra1004.github.io/ai-playground"


def verify_external_v2_dashboard(report_date_kst, base_url=DEFAULT_PUBLIC_BASE_URL, http_get=None, timeout_seconds=15):
    """Real, external, read-only HTTP GET against the actual live public
    pages -- never a local-file substitute. `http_get` (signature
    (url, timeout) -> object with .status_code/.text) defaults to
    `requests.get`; tests inject a fake so this module never makes a real
    network call under pytest. HTTP 200 ALONE IS NEVER PASS, LOCAL SUCCESS
    ALONE IS NEVER PASS, GIT PUSH SUCCESS ALONE IS NEVER PASS -- this
    re-derives the SAME date/marker/secret checks verify_local_v2_dashboard
    already applies locally, but against the real, currently-live page
    content, which is the only thing an actual reader ever sees."""
    if http_get is None:
        import requests
        http_get = lambda url, timeout: requests.get(url, timeout=timeout)

    index_url = base_url.rstrip("/") + "/v2/"
    dated_url = base_url.rstrip("/") + f"/v2/reports/{report_date_kst}.html"

    try:
        index_resp = http_get(index_url, timeout_seconds)
    except Exception as exc:
        return {"ok": False, "status": ReleaseCheckStatus.HTTP_ERROR,
                "reasons": [f"index GET failed: {type(exc).__name__}: {exc}"]}
    if index_resp.status_code != 200:
        return {"ok": False, "status": ReleaseCheckStatus.HTTP_ERROR,
                "reasons": [f"index HTTP {index_resp.status_code} (expected 200)"]}

    index_text = index_resp.text
    index_date_match = re.search(r"SUPER NEWS V2 — (\d{4}\.\d{2}\.\d{2})", index_text)
    index_date = index_date_match.group(1).replace(".", "-") if index_date_match else None
    if index_date is None:
        return {"ok": False, "status": ReleaseCheckStatus.INDEX_MISSING_OR_UNPARSEABLE,
                "reasons": ["public index page <title> date is unparseable"]}
    if index_date != report_date_kst:
        return {"ok": False, "status": ReleaseCheckStatus.INDEX_DATE_MISMATCH,
                "reasons": [f"public index date {index_date!r} != REPORT_DATE {report_date_kst!r} (stale public page)"]}
    if not has_music_intelligence_marker(index_text):
        return {"ok": False, "status": ReleaseCheckStatus.MUSIC_INTELLIGENCE_MISSING,
                "reasons": ["MUSIC INTELLIGENCE domain header missing from the real public index page"]}
    secrets_found = find_secret_exposure(index_text)
    if secrets_found:
        return {"ok": False, "status": ReleaseCheckStatus.SECRET_EXPOSURE,
                "reasons": [f"secret-shaped pattern(s) found on the public index page: {sorted(set(secrets_found))}"]}

    try:
        dated_resp = http_get(dated_url, timeout_seconds)
    except Exception as exc:
        return {"ok": False, "status": ReleaseCheckStatus.HTTP_ERROR,
                "reasons": [f"dated-report GET failed: {type(exc).__name__}: {exc}"]}
    if dated_resp.status_code != 200:
        return {"ok": False, "status": ReleaseCheckStatus.HTTP_ERROR,
                "reasons": [f"dated report HTTP {dated_resp.status_code} (expected 200)"]}

    dated_text = dated_resp.text
    dated_date_match = re.search(r"SUPER NEWS V2 — (\d{4}\.\d{2}\.\d{2})", dated_text)
    dated_date = dated_date_match.group(1).replace(".", "-") if dated_date_match else None
    if dated_date is None:
        return {"ok": False, "status": ReleaseCheckStatus.DATED_REPORT_MISSING_OR_UNPARSEABLE,
                "reasons": ["public dated-report page <title> date is unparseable"]}
    if dated_date != report_date_kst:
        return {"ok": False, "status": ReleaseCheckStatus.DATED_REPORT_DATE_MISMATCH,
                "reasons": [f"public dated report date {dated_date!r} != REPORT_DATE {report_date_kst!r}"]}
    secrets_found = find_secret_exposure(dated_text)
    if secrets_found:
        return {"ok": False, "status": ReleaseCheckStatus.SECRET_EXPOSURE,
                "reasons": [f"secret-shaped pattern(s) found on the public dated report page: {sorted(set(secrets_found))}"]}

    return {"ok": True, "status": ReleaseCheckStatus.OK, "reasons": []}


def _default_git_runner(args, cwd):
    return subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True, timeout=30)


def publish_v2_dashboard(report_date_kst, docs_v2_dir, repo_root, push=True, git_runner=None):
    """Stages and commits EXACTLY docs/v2/index.html and
    docs/v2/reports/<report_date_kst>.html -- never `git add .`/`git add
    -A`. Aborts (never commits) if anything else is already staged before
    this call (an unrelated pending change must never ride along with a
    publication commit), and aborts (unstaging first) if, after staging
    the two intended files, the staged set is not EXACTLY those two files.
    Returns {"published": bool, "pushed": bool, "reason": str|None}.
    `git_runner(args, cwd) -> CompletedProcess`-shaped callable, defaults
    to a real `git` subprocess -- tests inject a fake so no real repo
    mutation happens under pytest."""
    git_runner = git_runner or _default_git_runner
    docs_v2_dir = Path(docs_v2_dir)
    repo_root = Path(repo_root)
    index_path = docs_v2_dir / "index.html"
    dated_path = docs_v2_dir / "reports" / f"{report_date_kst}.html"
    rel_index = index_path.resolve().relative_to(repo_root.resolve()).as_posix()
    rel_dated = dated_path.resolve().relative_to(repo_root.resolve()).as_posix()

    pre_status = git_runner(["diff", "--cached", "--name-only"], repo_root)
    if pre_status.returncode != 0:
        return {"published": False, "pushed": False, "reason": f"git status check failed: {pre_status.stderr}"}
    already_staged = [line for line in pre_status.stdout.splitlines() if line.strip()]
    if already_staged:
        return {"published": False, "pushed": False,
                "reason": f"refusing to publish: unrelated file(s) already staged: {already_staged}"}

    add_result = git_runner(["add", "--", rel_index, rel_dated], repo_root)
    if add_result.returncode != 0:
        return {"published": False, "pushed": False, "reason": f"git add failed: {add_result.stderr}"}

    staged_status = git_runner(["diff", "--cached", "--name-only"], repo_root)
    staged_files = sorted(line for line in staged_status.stdout.splitlines() if line.strip())
    expected_files = sorted([rel_index, rel_dated])
    if staged_files != expected_files:
        git_runner(["reset"], repo_root)
        return {"published": False, "pushed": False,
                "reason": f"staged scope mismatch: expected {expected_files}, got {staged_files} (aborted, unstaged)"}

    commit_result = git_runner(
        ["commit", "-m", f"Publish SUPER NEWS V2 dashboard ({report_date_kst}) to docs/v2/"], repo_root,
    )
    if commit_result.returncode != 0:
        return {"published": False, "pushed": False, "reason": f"git commit failed: {commit_result.stderr}"}

    if not push:
        return {"published": True, "pushed": False, "reason": None}

    push_result = git_runner(["push", "origin", "main"], repo_root)
    if push_result.returncode != 0:
        return {"published": True, "pushed": False, "reason": f"git push failed: {push_result.stderr}"}
    return {"published": True, "pushed": True, "reason": None}


class ReleaseStatus:
    PASS_ = "PASS"
    PUBLISH_BLOCKED = "PUBLISH_BLOCKED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    EXTERNAL_VERIFICATION_FAILED = "EXTERNAL_VERIFICATION_FAILED"
    KAKAO_SEND_FAILED = "KAKAO_SEND_FAILED"
    POST_SEND_CONSISTENCY_FAILED = "POST_SEND_CONSISTENCY_FAILED"


def run_daily_v2_release(conn, report_date_kst, docs_v2_dir, repo_root, runs_row_id,
                          base_url=DEFAULT_PUBLIC_BASE_URL, push=True,
                          http_get=None, git_runner=None):
    """The REQUIRED DAILY V2 WEB FLOW, end to end: local verify (blocks
    publish) -> publish (git, blocks external verify/Kakao on failure) ->
    external verify (blocks Kakao) -> exactly ONE Kakao send
    (report_delivery_v2.deliver_daily_summary_v2, itself idempotent) ->
    REQUIRED post-send report.publication_consistency check. Returns
    {"status": one of ReleaseStatus, "reason": ..., "local_check":,
    "publish":, "external_check":, "delivery":, "consistency":} -- every
    stage's own raw result is preserved for inspection, never collapsed
    into just the final status. `status` is only ever "PASS" when EVERY
    gate passed, including the post-send consistency check -- a
    successful Kakao send is explicitly NOT sufficient for PASS on its
    own (see this module's own docstring for the real incident this
    guards against)."""
    result = {
        "local_check": None, "publish": None, "external_check": None,
        "delivery": None, "consistency": None,
    }

    local_check = verify_local_v2_dashboard(report_date_kst, docs_v2_dir)
    result["local_check"] = local_check
    if not local_check["ok"]:
        result["status"], result["reason"] = ReleaseStatus.PUBLISH_BLOCKED, local_check["reasons"]
        return result

    publish_result = publish_v2_dashboard(report_date_kst, docs_v2_dir, repo_root, push=push, git_runner=git_runner)
    result["publish"] = publish_result
    if not publish_result["published"] or (push and not publish_result["pushed"]):
        result["status"], result["reason"] = ReleaseStatus.PUBLISH_FAILED, publish_result["reason"]
        return result

    external_check = verify_external_v2_dashboard(report_date_kst, base_url=base_url, http_get=http_get)
    result["external_check"] = external_check
    if not external_check["ok"]:
        result["status"], result["reason"] = ReleaseStatus.EXTERNAL_VERIFICATION_FAILED, external_check["reasons"]
        return result

    from report_delivery_v2 import NoDashboardDataError, deliver_daily_summary_v2
    try:
        delivery_result = deliver_daily_summary_v2(report_date_kst, runs_row_id, conn=conn)
    except NoDashboardDataError as exc:
        result["status"], result["reason"] = ReleaseStatus.KAKAO_SEND_FAILED, str(exc)
        return result
    result["delivery"] = delivery_result
    if delivery_result["status"] == "failed":
        result["status"], result["reason"] = ReleaseStatus.KAKAO_SEND_FAILED, delivery_result["reason"]
        return result

    from report.publication_consistency import check_publication_consistency
    consistency = check_publication_consistency(conn, docs_v2_dir)
    result["consistency"] = consistency
    if not consistency["consistent"]:
        result["status"], result["reason"] = ReleaseStatus.POST_SEND_CONSISTENCY_FAILED, consistency["status"]
        return result

    result["status"], result["reason"] = ReleaseStatus.PASS_, None
    return result
