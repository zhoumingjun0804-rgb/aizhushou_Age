import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import gpt_chat


class GptChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.threads = Path(self.tmp.name) / "threads.json"
        self.history = Path(self.tmp.name) / "history.json"
        self.history.write_text("[]", encoding="utf-8")
        self.p1 = mock.patch.object(gpt_chat, "THREADS_FILE", self.threads)
        self.p2 = mock.patch.object(app, "HISTORY_FILE", self.history)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.tmp.cleanup()

    def test_mode_label_gpt_chat(self):
        self.assertEqual(app._history_mode_label("gpt_chat"), "💬GPT对话")

    def test_upsert_summary_creates_then_updates(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="画猫")
        hid = app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="画猫",
            output_images=["a.png"],
        )
        items = app.load_history()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["mode"], "gpt_chat")
        self.assertEqual(items[0]["thread_id"], thread["id"])
        self.assertEqual(items[0]["id"], hid)
        app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="改成蓝色",
            output_images=["b.png"],
        )
        items = app.load_history()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["output_images"], ["b.png"])
        self.assertIn("改成蓝色", items[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
