import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gpt_chat


class GptChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "gpt_chat_threads.json"
        self.patcher = mock.patch.object(gpt_chat, "THREADS_FILE", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_create_thread_and_append_messages(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="暑期海报")
        self.assertEqual(thread["project"], "小灯塔")
        user = gpt_chat.append_user_message(thread["id"], text="画一只猫", image_urls=[])
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="job1")
        self.assertEqual(user["role"], "user")
        self.assertEqual(asst["status"], "pending")
        loaded = gpt_chat.get_thread(thread["id"])
        self.assertEqual(len(loaded["messages"]), 2)

    def test_reject_when_pending(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        self.assertTrue(gpt_chat.thread_has_pending(thread["id"]))

    def test_last_success_image(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        gpt_chat.complete_assistant_message(
            thread["id"], asst["id"], status="done", image_urls=["variant_a.png"], error=""
        )
        self.assertEqual(gpt_chat.last_success_image(thread["id"]), "variant_a.png")

    def test_complete_error_clears_pending(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        gpt_chat.complete_assistant_message(
            thread["id"], asst["id"], status="error", image_urls=[], error="失败"
        )
        self.assertFalse(gpt_chat.thread_has_pending(thread["id"]))


if __name__ == "__main__":
    unittest.main()
