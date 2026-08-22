import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import ConfigError, load_account_config, load_brand_config  # noqa: E402


class TestConfig(unittest.TestCase):
    def test_swipe_info_config_loads(self):
        account = load_account_config("swipe_info")
        self.assertEqual(account.account_id, "swipe_info")
        self.assertTrue(account.enabled)
        self.assertEqual(account.content.pages_min, 4)
        self.assertEqual(account.content.pages_max, 8)

        brand = load_brand_config(account.brand_config_path)
        self.assertEqual(brand.canvas_width, 1080)
        self.assertEqual(brand.canvas_height, 1350)
        self.assertEqual(brand.typography_family, "Pretendard")

    def test_second_account_loads_without_core_code_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts_root = os.path.join(tmp, "accounts")
            acc_dir = os.path.join(accounts_root, "second_brand")
            os.makedirs(acc_dir)

            with open(os.path.join(acc_dir, "brand.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "brand:\n  name: \"SECOND\"\n"
                    "canvas:\n  width: 1080\n  height: 1350\n  ratio: \"4:5\"\n"
                    "colors:\n  primary: \"#123456\"\n"
                    "backgrounds:\n  white: \"#FFFFFF\"\n"
                    "typography:\n  family: \"Pretendard\"\n"
                    "layout:\n  safe_margin_left: 80\n"
                )
            with open(os.path.join(acc_dir, "account.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "account:\n  id: \"second_brand\"\n  name: \"Second Brand\"\n  enabled: true\n"
                    "content:\n  categories: [\"topic_a\"]\n  min_candidates: 5\n  min_score: 60\n"
                    "  pages_min: 4\n  pages_max: 6\n"
                    "platforms:\n  instagram: true\n  threads: false\n"
                    "publishing:\n  instagram_time: \"09:00\"\n  threads_time: \"09:10\"\n"
                    f"brand_config: \"{os.path.join(acc_dir, 'brand.yaml').replace(os.sep, '/')}\"\n"
                )

            account = load_account_config("second_brand", accounts_root=accounts_root)
            self.assertEqual(account.account_id, "second_brand")
            self.assertEqual(account.content.pages_max, 6)
            brand = load_brand_config(account.brand_config_path)
            self.assertEqual(brand.name, "SECOND")

    def test_invalid_account_config_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts_root = os.path.join(tmp, "accounts")
            acc_dir = os.path.join(accounts_root, "broken")
            os.makedirs(acc_dir)
            with open(os.path.join(acc_dir, "account.yaml"), "w", encoding="utf-8") as f:
                f.write("account:\n  id: \"broken\"\n  name: \"Broken\"\n  enabled: true\n")
            with self.assertRaises(ConfigError):
                load_account_config("broken", accounts_root=accounts_root)

    def test_invalid_page_range_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts_root = os.path.join(tmp, "accounts")
            acc_dir = os.path.join(accounts_root, "badpages")
            os.makedirs(acc_dir)
            with open(os.path.join(acc_dir, "brand.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "brand:\n  name: \"X\"\ncanvas:\n  width: 1080\n  height: 1350\n  ratio: \"4:5\"\n"
                    "colors:\n  primary: \"#123456\"\nbackgrounds:\n  white: \"#FFFFFF\"\n"
                    "typography:\n  family: \"Pretendard\"\nlayout:\n  safe_margin_left: 80\n"
                )
            with open(os.path.join(acc_dir, "account.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "account:\n  id: \"badpages\"\n  name: \"Bad\"\n  enabled: true\n"
                    "content:\n  categories: [\"a\"]\n  min_candidates: 5\n  min_score: 60\n"
                    "  pages_min: 2\n  pages_max: 10\n"
                    "platforms:\n  instagram: true\n  threads: true\n"
                    "publishing:\n  instagram_time: \"09:00\"\n  threads_time: \"09:10\"\n"
                    f"brand_config: \"{os.path.join(acc_dir, 'brand.yaml').replace(os.sep, '/')}\"\n"
                )
            with self.assertRaises(ConfigError):
                load_account_config("badpages", accounts_root=accounts_root)


if __name__ == "__main__":
    unittest.main()
