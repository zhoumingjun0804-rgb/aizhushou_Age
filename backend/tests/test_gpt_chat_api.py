import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import gpt_chat


class FakeQueue:
    def __init__(self, on_submit=None, submit_error=None):
        self.payloads = []
        self.on_submit = on_submit
        self.submit_error = submit_error

    def submit_generation(self, payload, runner):
        self.payloads.append(payload)
        if self.on_submit:
            self.on_submit(payload)
        if self.submit_error:
            raise self.submit_error
        return "job123"

    def get_job(self, job_id):
        return {"status": "queued", "position": 0}


def multipart(fields):
    boundary = "----gpt-chat-test"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        if isinstance(value, tuple):
            filename, data = value
            chunks.append(
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
            )
            chunks.append(data)
            chunks.append(b"\r\n")
        else:
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            chunks.append(str(value).encode())
            chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


class GptChatApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.threads = Path(self.tmp.name) / "threads.json"
        self.upload_dir = Path(self.tmp.name) / "uploads"
        self.output_dir = Path(self.tmp.name) / "outputs"
        self.upload_dir.mkdir()
        self.output_dir.mkdir()
        self.history = Path(self.tmp.name) / "history.json"
        self.history.write_text("[]", encoding="utf-8")
        self.patchers = [
            mock.patch.object(gpt_chat, "THREADS_FILE", self.threads),
            mock.patch.object(app, "HISTORY_FILE", self.history),
            mock.patch.object(app, "UPLOAD_DIR", self.upload_dir),
            mock.patch.object(app, "OUTPUT_DIR", self.output_dir),
            mock.patch.object(app, "is_gate_enabled", return_value=False),
            mock.patch.object(app, "fixed_project", return_value=""),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def make_handler(self, path, body=b"", content_type="application/json"):
        handler = app.Handler.__new__(app.Handler)
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": content_type,
        }
        handler.sent = None
        handler._send_json = lambda payload, status=200: setattr(
            handler, "sent", (payload, status)
        )
        handler.send_response = lambda status: setattr(handler, "sent", ({}, status))
        handler.end_headers = lambda: None
        return handler

    def post_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        handler = self.make_handler(path, body, "application/json")
        handler.do_POST()
        return handler.sent

    def post_multipart(self, path, fields):
        boundary, body = multipart(fields)
        handler = self.make_handler(
            path,
            body,
            f"multipart/form-data; boundary={boundary}",
        )
        handler.do_POST()
        return handler.sent

    def test_create_thread(self):
        payload, status = self.post_json("/api/gpt-chat/threads", {"project": "小灯塔"})

        self.assertEqual(status, 201)
        self.assertEqual(payload["thread"]["project"], "小灯塔")
        self.assertTrue(gpt_chat.get_thread(payload["thread"]["id"]))

    def test_text2img_submit_appends_messages_and_payload(self):
        fake_queue = FakeQueue()
        with (
            mock.patch.object(app, "_reload_runtime_env", return_value=None),
            mock.patch.object(app, "gpt_image_available_for_project", return_value=True),
            mock.patch.object(app, "raise_if_duplicate_high", return_value=None),
            mock.patch.object(app, "gpt_queue", fake_queue),
        ):
            thread = gpt_chat.create_thread(project="小灯塔")
            payload, status = self.post_multipart(
                f"/api/gpt-chat/threads/{thread['id']}/messages",
                {
                    "project": "小灯塔",
                    "client_id": "client-a",
                    "text": "画一只猫",
                    "ratio": "16:9",
                },
            )

        self.assertEqual(status, 201)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job_id"], "job123")
        self.assertEqual(payload["status_url"], "/api/generation/jobs/job123")
        submitted = fake_queue.payloads[0]
        self.assertEqual(submitted["image_backend"], "gpt")
        self.assertEqual(submitted["count"], 1)
        self.assertEqual(submitted["kind"], "with_prompt")
        self.assertEqual(submitted["mode"], "text2img")
        self.assertEqual(submitted["ratio"], "16:9")
        self.assertEqual(submitted["output_width"], 1920)
        self.assertEqual(submitted["output_height"], 1080)
        self.assertEqual(submitted["image_paths"], [])
        self.assertEqual(submitted["gpt_chat_thread_id"], thread["id"])
        stored = gpt_chat.get_thread(thread["id"])
        self.assertEqual([m["role"] for m in stored["messages"]], ["user", "assistant"])
        self.assertEqual(stored["messages"][1]["id"], submitted["gpt_chat_assistant_id"])
        self.assertEqual(stored["messages"][1]["job_id"], "job123")

    def test_user_ref_image_urls_point_to_uploads(self):
        fake_queue = FakeQueue()
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
            b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        with (
            mock.patch.object(app, "_reload_runtime_env", return_value=None),
            mock.patch.object(app, "gpt_image_available_for_project", return_value=True),
            mock.patch.object(app, "raise_if_duplicate_high", return_value=None),
            mock.patch.object(app, "gpt_queue", fake_queue),
        ):
            thread = gpt_chat.create_thread(project="小灯塔")
            payload, status = self.post_multipart(
                f"/api/gpt-chat/threads/{thread['id']}/messages",
                {
                    "project": "小灯塔",
                    "client_id": "client-a",
                    "text": "按参考图来",
                    "ref_image_0": ("ref.png", png),
                },
            )

        self.assertEqual(status, 201)
        stored = gpt_chat.get_thread(thread["id"])
        urls = stored["messages"][0]["image_urls"]
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("/uploads/ref_"))
        saved = self.upload_dir / urls[0].rsplit("/", 1)[-1]
        self.assertTrue(saved.is_file())
        self.assertTrue(fake_queue.payloads[0]["image_paths"])

    def test_assistant_exists_before_submit_allows_fast_completion(self):
        seen = {"pending": False}

        def complete_during_submit(payload):
            stored = gpt_chat.get_thread(payload["gpt_chat_thread_id"])
            messages = stored.get("messages") or []
            seen["pending"] = any(
                m.get("id") == payload["gpt_chat_assistant_id"]
                and m.get("role") == "assistant"
                and m.get("status") == "pending"
                for m in messages
            )
            gpt_chat.complete_assistant_message(
                payload["gpt_chat_thread_id"],
                payload["gpt_chat_assistant_id"],
                status="done",
                image_urls=["fast.png"],
                error="",
            )

        fake_queue = FakeQueue(on_submit=complete_during_submit)
        with (
            mock.patch.object(app, "_reload_runtime_env", return_value=None),
            mock.patch.object(app, "gpt_image_available_for_project", return_value=True),
            mock.patch.object(app, "raise_if_duplicate_high", return_value=None),
            mock.patch.object(app, "gpt_queue", fake_queue),
        ):
            thread = gpt_chat.create_thread(project="小灯塔")
            payload, status = self.post_multipart(
                f"/api/gpt-chat/threads/{thread['id']}/messages",
                {
                    "project": "小灯塔",
                    "client_id": "client-a",
                    "text": "画一只猫",
                },
            )

        self.assertEqual(status, 201)
        self.assertTrue(payload["ok"])
        self.assertTrue(seen["pending"])
        stored = gpt_chat.get_thread(thread["id"])
        assistant = stored["messages"][1]
        self.assertEqual(assistant["status"], "done")
        self.assertEqual(assistant["image_urls"], ["fast.png"])
        self.assertEqual(assistant["job_id"], "job123")

    def test_submit_failure_marks_placeholder_assistant_error(self):
        fake_queue = FakeQueue(submit_error=app.QueueFullError(1))
        with (
            mock.patch.object(app, "_reload_runtime_env", return_value=None),
            mock.patch.object(app, "gpt_image_available_for_project", return_value=True),
            mock.patch.object(app, "raise_if_duplicate_high", return_value=None),
            mock.patch.object(app, "gpt_queue", fake_queue),
        ):
            thread = gpt_chat.create_thread(project="小灯塔")
            payload, status = self.post_multipart(
                f"/api/gpt-chat/threads/{thread['id']}/messages",
                {
                    "project": "小灯塔",
                    "client_id": "client-a",
                    "text": "画一只猫",
                },
            )

        self.assertEqual(status, 503)
        self.assertIn("队列已满", payload["error"])
        stored = gpt_chat.get_thread(thread["id"])
        self.assertEqual(len(stored["messages"]), 2)
        assistant = stored["messages"][1]
        self.assertEqual(assistant["status"], "error")
        self.assertIn("队列已满", assistant["error"])
        self.assertFalse(gpt_chat.thread_has_pending(thread["id"]))

    def test_reject_pending_assistant(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="job-old")

        payload, status = self.post_multipart(
            f"/api/gpt-chat/threads/{thread['id']}/messages",
            {
                "project": "小灯塔",
                "client_id": "client-a",
                "text": "继续画",
            },
        )

        self.assertEqual(status, 409)
        self.assertIn("进行中", payload["error"])

    def test_get_thread_wrong_project_403(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        handler = self.make_handler(f"/api/gpt-chat/threads/{thread['id']}?project=画啦啦")
        handler.do_GET()

        payload, status = handler.sent
        self.assertEqual(status, 403)
        self.assertIn("无权访问", payload["error"])

    def test_get_thread_recovers_pending_when_job_missing(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="missing-job")
        handler = self.make_handler(f"/api/gpt-chat/threads/{thread['id']}?project=小灯塔")

        with mock.patch.object(app, "find_job", return_value=None):
            handler.do_GET()

        payload, status = handler.sent
        self.assertEqual(status, 200)
        assistant = payload["thread"]["messages"][0]
        self.assertEqual(assistant["status"], "error")
        self.assertIn("任务不存在或已过期", assistant["error"])
        self.assertFalse(gpt_chat.thread_has_pending(thread["id"]))

    def test_submit_writes_pending_history(self):
        fake_queue = FakeQueue()
        with (
            mock.patch.object(app, "_reload_runtime_env", return_value=None),
            mock.patch.object(app, "gpt_image_available_for_project", return_value=True),
            mock.patch.object(app, "raise_if_duplicate_high", return_value=None),
            mock.patch.object(app, "gpt_queue", fake_queue),
        ):
            thread = gpt_chat.create_thread(project="小灯塔")
            payload, status = self.post_multipart(
                f"/api/gpt-chat/threads/{thread['id']}/messages",
                {
                    "project": "小灯塔",
                    "client_id": "client-a",
                    "text": "画一只猫",
                },
            )
        self.assertEqual(status, 201)
        hist = app.load_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["thread_id"], thread["id"])
        self.assertEqual(hist[0]["status"], "pending")
        self.assertEqual(hist[0]["prompt"], "画一只猫")

    def test_get_missing_thread_restores_from_history(self):
        app.save_history(
            [
                {
                    "id": "h1",
                    "mode": "gpt_chat",
                    "thread_id": "deadbeef1234",
                    "project": "小灯塔",
                    "prompt": "画猫",
                    "messages": [
                        {"id": "u1", "role": "user", "text": "画猫", "image_urls": []},
                        {
                            "id": "a1",
                            "role": "assistant",
                            "status": "error",
                            "error": "超时",
                            "image_urls": [],
                        },
                    ],
                }
            ]
        )
        self.assertIsNone(gpt_chat.get_thread("deadbeef1234"))
        handler = self.make_handler("/api/gpt-chat/threads/deadbeef1234?project=小灯塔")
        handler.do_GET()
        payload, status = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["thread"]["id"], "deadbeef1234")
        self.assertEqual(payload["thread"]["messages"][0]["text"], "画猫")
        self.assertTrue(gpt_chat.get_thread("deadbeef1234"))


if __name__ == "__main__":
    unittest.main()
