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

    def test_set_assistant_job_id_preserves_status(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="")
        gpt_chat.complete_assistant_message(
            thread["id"], asst["id"], status="done", image_urls=["fast.png"], error=""
        )
        gpt_chat.set_assistant_job_id(thread["id"], asst["id"], "job123")

        stored = gpt_chat.get_thread(thread["id"])
        assistant = stored["messages"][0]
        self.assertEqual(assistant["status"], "done")
        self.assertEqual(assistant["job_id"], "job123")
        self.assertEqual(assistant["image_urls"], ["fast.png"])

    def test_fail_stale_pending_marks_missing_job_error(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="missing-job")

        changed = gpt_chat.fail_stale_pending(thread["id"], reason="任务不存在或已过期")

        self.assertTrue(changed)
        stored = gpt_chat.get_thread(thread["id"])
        assistant = stored["messages"][0]
        self.assertEqual(assistant["status"], "error")
        self.assertIn("任务不存在或已过期", assistant["error"])
        self.assertFalse(gpt_chat.thread_has_pending(thread["id"]))

    def test_try_append_turn_rejects_existing_pending_atomically(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="j1")

        result = gpt_chat.try_append_turn(
            thread["id"],
            text="继续画",
            image_urls=[],
            assistant_id="assistant2",
        )

        self.assertIsNone(result)
        stored = gpt_chat.get_thread(thread["id"])
        self.assertEqual(len(stored["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
