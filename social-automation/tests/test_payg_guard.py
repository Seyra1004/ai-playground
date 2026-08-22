import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.payg_guard import ALLOW_PAYG_ENV_VAR, PAYGBlockedError, assert_no_payg, payg_guard_active  # noqa: E402


class TestPAYGGuard(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(ALLOW_PAYG_ENV_VAR, None)

    def tearDown(self):
        if self._saved is not None:
            os.environ[ALLOW_PAYG_ENV_VAR] = self._saved
        else:
            os.environ.pop(ALLOW_PAYG_ENV_VAR, None)

    def test_blocked_by_default(self):
        self.assertTrue(payg_guard_active())
        with self.assertRaises(PAYGBlockedError):
            assert_no_payg("example_paid_provider")

    def test_never_silently_enabled_by_unrelated_env(self):
        os.environ["SOME_OTHER_VAR"] = "1"
        with self.assertRaises(PAYGBlockedError):
            assert_no_payg("example_paid_provider")
        del os.environ["SOME_OTHER_VAR"]

    def test_explicit_override_allows_it(self):
        os.environ[ALLOW_PAYG_ENV_VAR] = "1"
        self.assertFalse(payg_guard_active())
        assert_no_payg("example_paid_provider")  # must not raise


if __name__ == "__main__":
    unittest.main()
