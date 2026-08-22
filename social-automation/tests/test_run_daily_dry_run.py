import os
import shutil
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_daily import run_daily  # noqa: E402

TEST_DATE = "1999-12-31"  # far from any real production date, safe to clean up
TEST_ACCOUNT = "swipe_info"
TRACKING_ACCOUNT = f"{TEST_ACCOUNT}--dryrun"


class TestRunDailyDryRun(unittest.TestCase):
    def tearDown(self):
        out_dir = os.path.join("output", TRACKING_ACCOUNT, TEST_DATE)
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        db_path = os.path.join("data", f"{TEST_ACCOUNT}.db")
        if os.path.isfile(db_path):
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM runs WHERE account_id = ?", (TRACKING_ACCOUNT,))
            conn.execute("DELETE FROM pipeline_stage_state WHERE content_id LIKE ?", (f"dryrun-{TEST_ACCOUNT}-{TEST_DATE}%",))
            conn.execute("DELETE FROM cache WHERE cache_key LIKE ?", (f"{TEST_ACCOUNT}:dryrun-{TEST_ACCOUNT}-{TEST_DATE}%",))
            conn.commit()
            conn.close()

    def test_dry_run_reaches_complete_with_full_output_structure(self):
        exit_code = run_daily(TEST_ACCOUNT, TEST_DATE, dry_run=True, resume=False)
        self.assertEqual(exit_code, 0)

        out_dir = os.path.join("output", TRACKING_ACCOUNT, TEST_DATE)
        self.assertTrue(os.path.isdir(os.path.join(out_dir, "instagram")))
        self.assertTrue(os.path.isdir(os.path.join(out_dir, "preview")))
        for fname in (
            "instagram_caption.txt", "threads.txt", "sources.json", "fact_sheet.json",
            "claim_source_map.json", "qa_report.json", "run_summary.json", "candidates.json",
        ):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, fname)), f"missing {fname}")

        pngs = [f for f in os.listdir(os.path.join(out_dir, "instagram")) if f.endswith(".png")]
        self.assertGreaterEqual(len(pngs), 4)
        self.assertLessEqual(len(pngs), 8)

    def test_rerun_is_idempotent_and_reuses_cached_stages(self):
        run_daily(TEST_ACCOUNT, TEST_DATE, dry_run=True, resume=False)

        db_path = os.path.join("data", f"{TEST_ACCOUNT}.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        before = {
            r["stage"]: r["retry_count"]
            for r in conn.execute(
                "SELECT stage, retry_count FROM pipeline_stage_state WHERE content_id LIKE ?",
                (f"dryrun-{TEST_ACCOUNT}-{TEST_DATE}%",),
            )
        }
        conn.close()

        exit_code = run_daily(TEST_ACCOUNT, TEST_DATE, dry_run=True, resume=True)
        self.assertEqual(exit_code, 0)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        after = {
            r["stage"]: r["retry_count"]
            for r in conn.execute(
                "SELECT stage, retry_count FROM pipeline_stage_state WHERE content_id LIKE ?",
                (f"dryrun-{TEST_ACCOUNT}-{TEST_DATE}%",),
            )
        }
        conn.close()

        self.assertEqual(before, after)  # no stage was re-executed, only cache-hit


if __name__ == "__main__":
    unittest.main()
