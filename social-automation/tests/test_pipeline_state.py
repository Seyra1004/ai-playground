import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_connection, init_db  # noqa: E402
from pipeline.state import run_stage  # noqa: E402


class TestPipelineState(unittest.TestCase):
    def setUp(self):
        self.conn = get_connection(":memory:")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_cache_reuses_unchanged_successful_stage(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return {"value": 42}

        result1, hit1 = run_stage(self.conn, "acct", "content1", "stage_a", {"x": 1}, fn, "t1")
        result2, hit2 = run_stage(self.conn, "acct", "content1", "stage_a", {"x": 1}, fn, "t2")

        self.assertFalse(hit1)
        self.assertTrue(hit2)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(result1, result2)

    def test_changed_input_reruns_stage(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return {"value": calls["n"]}

        run_stage(self.conn, "acct", "content1", "stage_a", {"x": 1}, fn, "t1")
        run_stage(self.conn, "acct", "content1", "stage_a", {"x": 2}, fn, "t2")

        self.assertEqual(calls["n"], 2)

    def test_failed_stage_can_rerun_without_restarting_upstream(self):
        upstream_calls = {"n": 0}
        downstream_calls = {"n": 0}
        should_fail = {"flag": True}

        def upstream_fn():
            upstream_calls["n"] += 1
            return {"upstream": True}

        def downstream_fn():
            downstream_calls["n"] += 1
            if should_fail["flag"]:
                raise RuntimeError("boom")
            return {"downstream": True}

        run_stage(self.conn, "acct", "content1", "upstream", {"x": 1}, upstream_fn, "t1")
        with self.assertRaises(RuntimeError):
            run_stage(self.conn, "acct", "content1", "downstream", {"x": 1}, downstream_fn, "t1")

        should_fail["flag"] = False
        run_stage(self.conn, "acct", "content1", "upstream", {"x": 1}, upstream_fn, "t2")
        result, _hit = run_stage(self.conn, "acct", "content1", "downstream", {"x": 1}, downstream_fn, "t2")

        self.assertEqual(upstream_calls["n"], 1)  # upstream never re-executed
        self.assertEqual(downstream_calls["n"], 2)  # downstream retried after failure
        self.assertEqual(result, {"downstream": True})


if __name__ == "__main__":
    unittest.main()
