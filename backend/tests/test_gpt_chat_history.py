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

    def test_upsert_merges_same_thread_without_clearing(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="画猫")
        hid1 = app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="画猫",
            output_images=["a.png"],
        )
        hid2 = app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="改成蓝色",
            output_images=["b.png"],
        )
        hid3 = app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="再加点花",
            output_images=[],
            error="The model is overloaded",
            status="error",
        )
        items = app.load_history()
        self.assertEqual(len(items), 1)
        self.assertEqual(hid1, hid2)
        self.assertEqual(hid1, hid3)
        self.assertEqual(items[0]["id"], hid1)
        self.assertEqual(items[0]["prompt"], "画猫")
        self.assertEqual(items[0]["output_images"], ["a.png", "b.png"])
        self.assertEqual(items[0]["error"], "The model is overloaded")
        self.assertEqual(items[0]["status"], "error")
        self.assertIn("失败", items[0]["meta_tags"])

    def test_coalesce_duplicate_thread_rows(self):
        items = [
            {
                "id": "new",
                "mode": "gpt_chat",
                "thread_id": "t1",
                "prompt": "重试，现在超时",
                "status": "error",
                "error": "繁忙",
            },
            {
                "id": "old",
                "mode": "gpt_chat",
                "thread_id": "t1",
                "prompt": "给我做一个对比图",
                "output_images": ["a.png"],
                "status": "done",
            },
            {"id": "other", "mode": "text2img", "prompt": "别的"},
        ]
        merged = app._coalesce_gpt_chat_history_items(items)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["id"], "new")
        self.assertEqual(merged[0]["prompt"], "给我做一个对比图")
        self.assertEqual(merged[0]["output_images"], ["a.png"])
        self.assertEqual(merged[0]["error"], "繁忙")
        self.assertEqual(merged[1]["id"], "other")

    def test_filter_includes_pending_thread_missing_from_history(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.try_append_turn(thread["id"], text="hi", image_urls=[], assistant_id="a1")
        items = app.filter_history_items([])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["thread_id"], thread["id"])
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["prompt"], "hi")


if __name__ == "__main__":
    unittest.main()
