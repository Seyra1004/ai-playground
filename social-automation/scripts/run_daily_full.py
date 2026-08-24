from __future__ import annotations

"""ONE unattended entrypoint chaining the already-working SWIPE_INFO stages:
generate_daily_research -> run_daily (verification -> semantic Claude CLI on
cache miss -> render -> QA) -> [only if COMPLETE] build_review_page ->
publish dated/latest page to GitHub Pages -> send ONE Kakao review message.

No new content/scoring/render logic here -- purely orchestrates the
existing scripts as subprocesses, matching what was already run manually.
A non-COMPLETE run_daily result stops here (stale-content guard): nothing
is published, nothing is sent, the existing failure_report.json stands.
A Kakao delivery failure is caught and reported but never un-does the
already-COMPLETE content package or its published review page.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_PLAYGROUND_ROOT = os.path.dirname(REPO_ROOT)
SUPER_NEWS_VENV_PY = os.path.join(AI_PLAYGROUND_ROOT, "super-news", ".venv", "bin", "python3")
PAGES_BASE_URL = "https://seyra1004.github.io/ai-playground/v2/reports/swipe-info"


def _run(cmd, cwd):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=False)


def _review_page_is_live(url: str, run_date: str) -> bool:
    """Do not send a review link until GitHub Pages serves today's page."""
    for attempt in range(9):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 -- fixed HTTPS URL
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200 and f"SWIPE_INFO {run_date} 리뷰" in body:
                    return True
        except (urllib.error.URLError, TimeoutError):
            pass
        if attempt < 8:
            time.sleep(10)
    return False


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="swipe_info")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    run_date = args.date or date.today().isoformat()
    py = sys.executable

    # 1. live research (idempotent: reuses excerpt cache for unchanged sources)
    _run([py, "scripts/generate_daily_research.py", "--date", run_date], cwd=REPO_ROOT)

    # 2. verification -> semantic (Claude CLI only on cache miss) -> render -> QA
    #    (run_daily.py has its own COMPLETE-date idempotent short-circuit)
    _run([py, "scripts/run_daily.py", "--account", args.account, "--date", run_date], cwd=REPO_ROOT)

    out_dir = os.path.join(REPO_ROOT, "output", args.account, run_date)
    summary_path = os.path.join(out_dir, "run_summary.json")
    if not os.path.isfile(summary_path):
        print("FINAL_STATUS=FAILED (no run_summary.json -- run_daily did not complete a package)")
        return 1

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    status = summary.get("status", "UNKNOWN")
    topic = summary.get("selected_topic", "")
    print(f"FINAL_STATUS={status}")

    if status != "COMPLETE":
        print("Not COMPLETE -- stale-content guard: skipping review-page publish and Kakao delivery.")
        return 1

    # 3. review page (dated + latest, regenerated from this run's own output/)
    review_build = _run(
        [py, "scripts/build_review_page.py", "--account", args.account, "--date", run_date], cwd=REPO_ROOT
    )
    if review_build.returncode != 0:
        print("PUBLISH_FAILED (review page generation failed; Kakao delivery blocked)")
        return 1

    dated_rel = f"docs/v2/reports/swipe-info/{run_date}"
    latest_rel = "docs/v2/reports/swipe-info/latest"
    kakao_marker = os.path.join(out_dir, "kakao_sent.json")
    if not all(
        os.path.isfile(os.path.join(AI_PLAYGROUND_ROOT, rel, "index.html"))
        for rel in (dated_rel, latest_rel)
    ):
        print("PUBLISH_FAILED (review page output missing; Kakao delivery blocked)")
        return 1

    # 4. publish to the existing GitHub Pages tree (commit only if changed)
    staged = _run(["git", "add", dated_rel, latest_rel], cwd=AI_PLAYGROUND_ROOT)
    if staged.returncode != 0:
        print("PUBLISH_FAILED (git add failed; Kakao delivery blocked to avoid a broken review link)")
        return 1
    commit = _run(
        ["git", "commit", "-m", f"SWIPE_INFO daily review page {run_date}", "-q"],
        cwd=AI_PLAYGROUND_ROOT,
    )
    if commit.returncode == 0:
        push = _run(["git", "push", "origin", "main"], cwd=AI_PLAYGROUND_ROOT)
        print(f"PUSHED={push.returncode == 0}")
        if push.returncode != 0:
            print("PUBLISH_FAILED (git push failed; Kakao delivery blocked to avoid a broken review link)")
            return 1
    elif commit.returncode == 1:
        # Git uses exit 1 when there is nothing to commit. It is safe to send
        # only when the two review-page paths are actually clean; any pending
        # change means a failed commit or unpublished package.
        pending = subprocess.run(
            ["git", "status", "--porcelain", "--", dated_rel, latest_rel],
            cwd=AI_PLAYGROUND_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if pending.returncode != 0 or pending.stdout.strip():
            print("PUBLISH_FAILED (review page still has uncommitted changes; Kakao delivery blocked)")
            return 1
        print("PUBLISH_ALREADY_CURRENT=true")
    else:
        print("PUBLISH_FAILED (git commit failed; Kakao delivery blocked to avoid a broken review link)")
        return 1

    # 5. Kakao -- once per date; failure never invalidates the COMPLETE package
    if os.path.isfile(kakao_marker):
        print("KAKAO_ALREADY_SENT -- skipping duplicate delivery for this date.")
        return 0

    dated_url = f"{PAGES_BASE_URL}/{run_date}/"
    latest_url = f"{PAGES_BASE_URL}/latest/"
    if not _review_page_is_live(dated_url, run_date):
        print("PUBLISH_FAILED (today's review page is not live; Kakao delivery blocked)")
        return 1
    try:
        kakao = _run(
            [
                SUPER_NEWS_VENV_PY, os.path.join(REPO_ROOT, "scripts", "send_kakao_review.py"),
                "--topic", topic, "--status", status, "--dated-url", dated_url, "--latest-url", latest_url,
            ],
            cwd=REPO_ROOT,
        )
        if kakao.returncode == 0:
            with open(kakao_marker, "w", encoding="utf-8") as f:
                json.dump({"sent": True, "dated_url": dated_url}, f)
        else:
            print("KAKAO_DELIVERY_FAILED")
            return 1
    except Exception as exc:  # noqa: BLE001 -- delivery failure must never look like a content failure
        print(f"KAKAO_DELIVERY_FAILED: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
