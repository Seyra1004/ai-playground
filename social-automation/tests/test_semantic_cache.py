import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import semantic_cache  # noqa: E402


class TestSemanticCache(unittest.TestCase):
    def test_miss_then_hit_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = semantic_cache.compute_semantic_cache_key("ev1", "acct1", "brand1")
            self.assertIsNone(semantic_cache.load_semantic_output(tmp, key))

            payload = {"pages": [{"page_number": 1}], "instagram_caption": "c", "threads_text": "t"}
            semantic_cache.save_semantic_output(tmp, key, payload)

            loaded = semantic_cache.load_semantic_output(tmp, key)
            self.assertEqual(loaded, payload)

    def test_cache_key_changes_when_evidence_changes(self):
        k1 = semantic_cache.compute_semantic_cache_key("ev1", "acct1", "brand1")
        k2 = semantic_cache.compute_semantic_cache_key("ev2", "acct1", "brand1")
        self.assertNotEqual(k1, k2)

    def test_cache_key_stable_for_identical_inputs(self):
        k1 = semantic_cache.compute_semantic_cache_key("ev1", "acct1", "brand1")
        k2 = semantic_cache.compute_semantic_cache_key("ev1", "acct1", "brand1")
        self.assertEqual(k1, k2)

    def test_cache_key_changes_when_brand_changes(self):
        k1 = semantic_cache.compute_semantic_cache_key("ev1", "acct1", "brandA")
        k2 = semantic_cache.compute_semantic_cache_key("ev1", "acct1", "brandB")
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
