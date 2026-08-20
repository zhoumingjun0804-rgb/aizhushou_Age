import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


class GenerationRetryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_retryable_error_helper_exists(self):
        self.assertIn("function isRetryableGenerationError", self.html)
        self.assertIn("function confirmRetryGeneration", self.html)
        self.assertIn("模型当前繁忙", self.html)
        self.assertIn("overloaded", self.html)
        self.assertIn("额度或频率受限", self.html)

    def test_retry_prompt_asks_to_retry_again(self):
        self.assertIn("是否再次重试", self.html)
        self.assertIn("这是暂时性问题", self.html)

    def test_main_generation_flow_offers_retry(self):
        start = self.html.index("async function runGenerationFlow")
        end = self.html.index("async function refreshGenerationJobs")
        block = self.html[start:end]
        self.assertIn("confirmRetryGeneration", block)
        self.assertIn("return runGenerationFlow(formData, kind)", block)

    def test_tool_generation_flows_offer_retry(self):
        for name in ("generateSplashHero", "generateLandingModule", "generateLiveroom"):
            marker = "async function " + name
            start = self.html.index(marker)
            nxt = self.html.find("\nasync function ", start + 1)
            block = self.html[start:nxt if nxt != -1 else start + 8000]
            self.assertIn("noteRetryableGenerationFailure", block, msg=name)


if __name__ == "__main__":
    unittest.main()
