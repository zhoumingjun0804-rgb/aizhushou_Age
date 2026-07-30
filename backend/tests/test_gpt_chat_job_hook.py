import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import gpt_chat


class GptChatJobHookTests(unittest.TestCase):
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

    def test_notify_done_updates_message_and_history(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="猫")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="jobx")
        payload = {
            "gpt_chat_thread_id": thread["id"],
            "gpt_chat_assistant_id": asst["id"],
            "project": "小灯塔",
            "prompt": "画猫",
        }
        app._notify_gpt_chat_job(payload, status="done", output_images=["out.png"], error="")
        t = gpt_chat.get_thread(thread["id"])
        msg = next(m for m in t["messages"] if m["id"] == asst["id"])
        self.assertEqual(msg["status"], "done")
        self.assertEqual(msg["image_urls"], ["out.png"])
        hist = app.load_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["mode"], "gpt_chat")

    def test_notify_error_sets_error_no_regular_requirement(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="joby")
        payload = {
            "gpt_chat_thread_id": thread["id"],
            "gpt_chat_assistant_id": asst["id"],
            "project": "小灯塔",
            "prompt": "画猫",
        }
        app._notify_gpt_chat_job(payload, status="error", output_images=[], error="超时")
        t = gpt_chat.get_thread(thread["id"])
        msg = next(m for m in t["messages"] if m["id"] == asst["id"])
        self.assertEqual(msg["status"], "error")
        self.assertEqual(msg["error"], "超时")


if __name__ == "__main__":
    unittest.main()
