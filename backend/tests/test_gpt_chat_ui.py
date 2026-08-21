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
            "retryLastGptChatTurn",
        ):
            self.assertIn("function " + name, self.html)

    def test_gpt_chat_error_offers_retry(self):
        self.assertIn("再次重试", self.html)
        start = self.html.index("async function pollGptChatJob")
        nxt = self.html.find("\nasync function ", start + 1)
        block = self.html[start:nxt]
        self.assertIn("confirmRetryGeneration", block)
        self.assertIn("retryLastGptChatTurn", block)

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

    def test_history_visible_items_include_gpt_chat_thread(self):
        start = self.html.index("function _historyVisibleItems")
        nxt = self.html.find("\nfunction ", start + 1)
        block = self.html[start:nxt]
        self.assertIn("gpt_chat", block)
        self.assertIn("thread_id", block)

    def test_open_gpt_chat_resumes_pending_job(self):
        start = self.html.index("async function openGptChatThread")
        nxt = self.html.find("\nasync function ", start + 1)
        block = self.html[start:nxt]
        self.assertIn("pollGptChatJob", block)
        fetch_at = block.index("fetchGptChatThread")
        assign_at = block.index("gptChatThreadId =")
        self.assertGreater(assign_at, fetch_at)

    def test_gpt_tab_hidden_without_active_class(self):
        """非 active 时 #gptTab 不得强制 display:flex，否则会压过 .tab-content 的 hidden。"""
        # 去掉 CSS 注释，避免注释文案误匹配
        style = re.sub(r'/\*.*?\*/', '', self.html, flags=re.S)
        css_blocks = re.findall(r'#gptTab[^{.\s]*(\.[^{]*)?\{[^}]+\}', style)
        # 上面正则过严，改为直接扫规则块
        css_blocks = re.findall(r'#gptTab[^{]*\{[^}]+\}', style)
        bare_flex = False
        active_flex = False
        for block in css_blocks:
            has_flex = re.search(r'display\s*:\s*flex', block) is not None
            if not has_flex:
                continue
            if re.search(r'#gptTab[^{]*\.active', block):
                active_flex = True
            else:
                bare_flex = True
        self.assertFalse(bare_flex, msg='inactive #gptTab must not set display:flex')
        self.assertTrue(active_flex, msg='expected #gptTab....active { display: flex }')

    def test_poll_job_404_refreshes_thread(self):
        m = re.search(r'async function pollGptChatJob\(jobId\) \{[\s\S]*?\n\}', self.html)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("res.status === 404", block)
        self.assertIn("fetchGptChatThread(gptChatThreadId)", block)

    def test_gpt_chat_allows_four_reference_images(self):
        self.assertIn("最多4张", self.html)
        self.assertIn("gptChatAttachFiles.length >= 4", self.html)
        self.assertIn("gptChatAttachFiles.slice(0, 4)", self.html)

    def test_gpt_chat_ref_images_use_uploads_url(self):
        start = self.html.index("function gptChatOutputUrl")
        nxt = self.html.find("\nfunction ", start + 1)
        block = self.html[start:nxt]
        self.assertIn("/uploads/", block)
        self.assertIn("ref_", block)


if __name__ == "__main__":
    unittest.main()
