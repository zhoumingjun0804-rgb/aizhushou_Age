import unittest

from gpt_image_client import (
    _async_job_kind,
    _extract_image_any,
    _operation_location_from_headers,
    _should_fallback_to_sync,
)


class TestGptAsyncPollHelpers(unittest.TestCase):
    def test_operation_location_is_case_insensitive(self):
        loc = _operation_location_from_headers(
            {"Operation-Location": "https://example/ops/abc"}
        )
        self.assertEqual(loc, "https://example/ops/abc")

    def test_running_and_terminal_statuses(self):
        self.assertEqual(_async_job_kind("queued"), "running")
        self.assertEqual(_async_job_kind("in_progress"), "running")
        self.assertEqual(_async_job_kind("notRunning"), "running")
        self.assertEqual(_async_job_kind("completed"), "success")
        self.assertEqual(_async_job_kind("succeeded"), "success")
        self.assertEqual(_async_job_kind("failed"), "failed")
        self.assertEqual(_async_job_kind("cancelled"), "failed")

    def test_extracts_azure_nested_image_result(self):
        import tempfile
        from pathlib import Path

        png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
            "/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as tmp:
            parsed = _extract_image_any(
                {"status": "succeeded", "result": {"data": [{"b64_json": png}]}},
                Path(tmp),
            )
            self.assertIsNotNone(parsed)
            self.assertTrue(parsed[0].startswith("file://"))
            self.assertIsNone(parsed[1])

    def test_timeout_does_not_fallback_to_sync(self):
        self.assertFalse(_should_fallback_to_sync("GPT 生图网关超时，请稍后重试。"))
        self.assertFalse(_should_fallback_to_sync("GPT 生图模型当前繁忙，请稍后重试。"))
        self.assertTrue(_should_fallback_to_sync("GPT 生图网关路径错误（404）"))
        self.assertTrue(_should_fallback_to_sync("unsupported media type"))
        self.assertTrue(_should_fallback_to_sync("Unknown parameter: background"))

    def test_follow_async_submit_polls_202(self):
        from gpt_image_client import GptImageClient

        client = GptImageClient(api_key="sk-test", provider="azure", timeout=30)
        polled = {}

        def fake_poll(url, deadline):
            polled["url"] = url
            return "file:///tmp/x.png", None

        client._poll_url = fake_poll
        followed = client._follow_async_submit(
            202,
            {"id": "op-1", "status": "running"},
            {"operation-location": "https://gw/ops/op-1"},
            "https://gw/v1/images/generations",
        )
        self.assertEqual(followed, ("file:///tmp/x.png", None))
        self.assertEqual(polled["url"], "https://gw/ops/op-1")


if __name__ == "__main__":
    unittest.main()
