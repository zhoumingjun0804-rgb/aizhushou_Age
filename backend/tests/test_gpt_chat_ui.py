import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


class GptChatUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_gpt_nav_tab_exists(self):
        self.assertRegex(self.html, r'data-tab="gpt"[^>]*>.*GPT')

    def test_gpt_tab_panel_and_composer(self):
        self.assertIn('id="gptTab"', self.html)
        self.assertIn('id="gptChatMessages"', self.html)
        self.assertIn('id="gptChatInput"', self.html)
        self.assertIn('id="gptChatSendBtn"', self.html)
        self.assertIn('id="gptChatFileInput"', self.html)

    def test_gpt_optional_ratio_quality(self):
        self.assertIn('id="gptChatRatioSelect"', self.html)
        self.assertIn('id="gptChatQualitySelect"', self.html)

    def test_gpt_chat_js_helpers_exist(self):
        for name in (
            "sendGptChatMessage",
            "renderGptChatThread",
            "openGptChatThread",
            "pollGptChatJob",
        ):
            self.assertIn("function " + name, self.html)

    def test_gpt_tab_has_no_logo_or_structured_fields(self):
        m = re.search(r'id="gptTab"[\s\S]*?(?=<div class="card tab-content"|$)', self.html)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertNotIn('id="mainTitle"', block)
        self.assertNotIn('id="logoPositionSelect"', block)
        self.assertNotIn('id="requirementName"', block)

    def test_history_opens_gpt_chat_thread(self):
        self.assertIn("openGptChatThread", self.html)
        self.assertIn("gpt_chat", self.html)

    def test_poll_job_404_refreshes_thread(self):
        m = re.search(r'async function pollGptChatJob\(jobId\) \{[\s\S]*?\n\}', self.html)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("res.status === 404", block)
        self.assertIn("fetchGptChatThread(gptChatThreadId)", block)


if __name__ == "__main__":
    unittest.main()
