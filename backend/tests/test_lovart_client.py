import os
import unittest

from lovart_client import (
    _lovart_unlimited_attempts,
    is_lovart_credit_error,
    is_lovart_limit_error,
)


class LovartLimitErrorTests(unittest.TestCase):
    def test_credit_insufficient_en(self):
        msg = (
            "Insufficient credits. Top up or switch to unlimited mode "
            "(set-mode --unlimited)."
        )
        self.assertTrue(is_lovart_credit_error(msg))
        self.assertTrue(is_lovart_limit_error(msg))

    def test_credit_insufficient_zh(self):
        msg = "信用不足。请充值或切换至无限制模式（set-mode --unlimited）"
        self.assertTrue(is_lovart_credit_error(msg))
        self.assertTrue(is_lovart_limit_error(msg))

    def test_concurrent_limit(self):
        self.assertTrue(is_lovart_limit_error("Concurrent task limit reached"))

    def test_unrelated_error(self):
        self.assertFalse(is_lovart_limit_error("Lovart 创建项目失败"))
        self.assertFalse(is_lovart_credit_error("Lovart 创建项目失败"))


class LovartUnlimitedPrefTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("LOVART_UNLIMITED", None)

    def test_auto_default(self):
        os.environ.pop("LOVART_UNLIMITED", None)
        self.assertEqual(_lovart_unlimited_attempts(), (False, True))

    def test_always_unlimited(self):
        os.environ["LOVART_UNLIMITED"] = "1"
        self.assertEqual(_lovart_unlimited_attempts(), (True,))

    def test_fast_only(self):
        os.environ["LOVART_UNLIMITED"] = "0"
        self.assertEqual(_lovart_unlimited_attempts(), (False,))


if __name__ == "__main__":
    unittest.main()
