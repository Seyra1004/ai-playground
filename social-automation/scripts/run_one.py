from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import ConfigError, load_account_config, load_brand_config  # noqa: E402
from core.database import get_connection, init_db  # noqa: E402
from core.models import QAStatus  # noqa: E402
from pipeline.runner import run_pipeline  # noqa: E402
from scripts.demo_fixture import build_demo_candidates, build_demo_fact_sheet, build_demo_page_inputs  # noqa: E402


def persist_content_package(conn, package) -> None:
    now = "2026-08-22T00:00:00Z"
    conn.execute(
        "INSERT INTO contents (content_id, account_id, topic, page_count, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(content_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
        (
            package.content_id,
            package.account_id,
            package.canonical_content.fact_sheet.topic if package.canonical_content else "",
            package.canonical_content.page_count if package.canonical_content else 0,
            package.status,
            now,
            now,
        ),
    )
    if package.canonical_content:
        for page in package.canonical_content.pages:
            conn.execute(
                "INSERT INTO pages (content_id, page_number, role, headline, body, visual_ref) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(content_id, page_number) DO UPDATE SET "
                "role=excluded.role, headline=excluded.headline, body=excluded.body, visual_ref=excluded.visual_ref",
                (package.content_id, page.page_number, page.role, page.headline, page.body, page.visual_ref),
            )
    conn.commit()


def run_demo(account_id: str) -> int:
    account = load_account_config(account_id)
    brand = load_brand_config(account.brand_config_path)

    db_path = os.path.join("data", f"{account_id}.db")
    conn = get_connection(db_path)
    init_db(conn)

    candidates = build_demo_candidates()
    fact_sheet = build_demo_fact_sheet()
    page_inputs = build_demo_page_inputs()
    now = "2026-08-22T00:00:00Z"

    package = run_pipeline(conn, account, brand, candidates, fact_sheet, page_inputs, now)
    persist_content_package(conn, package)

    print(f"account: {account.name} ({account.account_id})")
    print(f"content_id: {package.content_id}")
    print(f"status: {package.status}")
    if package.canonical_content:
        print(f"page_count: {package.canonical_content.page_count}")
        print(f"page_plan: {package.canonical_content.page_plan}")
    if package.instagram_caption:
        print("--- instagram caption ---")
        print(package.instagram_caption)
    if package.threads_text:
        print("--- threads text ---")
        print(package.threads_text)
    if package.qa_result:
        print(f"qa_status: {package.qa_result.status.value}")
        if package.qa_result.checks_failed:
            print(f"qa_checks_failed: {package.qa_result.checks_failed}")

    conn.close()
    return 0 if package.status in ("COMPLETE", "NEEDS_REVIEW") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the social-automation content pipeline for one account.")
    parser.add_argument("--account", required=True, help="account_id under accounts/<account_id>/")
    parser.add_argument("--demo", action="store_true", help="run with a deterministic demo fixture (no web/LLM)")
    args = parser.parse_args()

    if not args.demo:
        print("Only --demo is supported in this MVP foundation build.", file=sys.stderr)
        return 2

    try:
        return run_demo(args.account)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
