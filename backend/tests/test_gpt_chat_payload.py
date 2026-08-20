import unittest

from gpt_image_client import (
    GptAuth,
    _auth_headers,
    _friendly_error,
    _retry_wait_seconds,
    build_chat_completion_payload,
    chat_completion_token_param,
    is_azure_gateway,
)


class TestChatCompletionTokenParam(unittest.TestCase):
    def test_gpt5_uses_max_completion_tokens(self):
        self.assertEqual(
            chat_completion_token_param("gpt-5.4", 500),
            {"max_completion_tokens": 500},
        )

    def test_o_series_uses_max_completion_tokens(self):
        self.assertEqual(
            chat_completion_token_param("o3-mini", 500),
            {"max_completion_tokens": 500},
        )

    def test_gpt4_uses_max_tokens(self):
        self.assertEqual(
            chat_completion_token_param("gpt-4o", 500),
            {"max_tokens": 500},
        )

    def test_gpt5_payload_omits_temperature(self):
        payload = build_chat_completion_payload(
            "gpt-5.4",
            [{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=500,
        )
        self.assertEqual(payload["max_completion_tokens"], 500)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("max_tokens", payload)

    def test_gpt4_payload_includes_temperature(self):
        payload = build_chat_completion_payload(
            "gpt-4o",
            [{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=500,
        )
        self.assertEqual(payload["max_tokens"], 500)
        self.assertEqual(payload["temperature"], 0.7)


class TestAzureAuthHeaders(unittest.TestCase):
    def test_azure_sends_apim_and_openai_key_headers(self):
        headers = _auth_headers(GptAuth(api_key_header="abc123"), "application/json")
        self.assertEqual(headers["api-key"], "abc123")
        self.assertEqual(headers["Ocp-Apim-Subscription-Key"], "abc123")
        self.assertEqual(headers["x-api-key"], "abc123")
        self.assertEqual(headers["Authorization"], "Bearer abc123")
        self.assertIn("Aizhushou-GPT", headers["User-Agent"])

    def test_cloudflare_html_is_not_shown_as_invalid_key(self):
        msg = _friendly_error(
            403,
            "<html><title>Access denied | gptproto.com used Cloudflare to restrict access</title></html>",
        )
        self.assertIn("Cloudflare", msg)
        self.assertIn("直连", msg)
        self.assertNotIn("<html>", msg)

    def test_appid_not_published_is_not_path_mismatch(self):
        msg = _friendly_error(500, {"msg": "当前appId:abcd 未对外提供服务"})
        self.assertIn("尚未对该 appId 开放", msg)
        self.assertNotIn("路径不匹配", msg)

    def test_subscription_key_error_mentions_project_paths(self):
        msg = _friendly_error(
            401,
            "Access denied due to invalid subscription key or wrong API endpoint.",
        )
        self.assertIn("OPENAI_API_KEY_XDT", msg)
        self.assertIn("gptproto", msg)

    def test_404_includes_request_url(self):
        msg = _friendly_error(
            404,
            {"msg": "404 NOT_FOUND"},
            url="https://liuyi-llm-risk.61info.cn/api/gptproto/v1/images/generations",
        )
        self.assertIn("liuyi-llm-risk.61info.cn", msg)
        self.assertIn("/api/gptproto", msg)

    def test_gptproto_url_is_company_gateway(self):
        self.assertTrue(
            is_azure_gateway("https://liuyi-llm-risk.61info.cn/api/gptproto")
        )
        self.assertFalse(is_azure_gateway("https://api.openai.com"))

    def test_overloaded_is_not_quota_error(self):
        msg = _friendly_error(
            429,
            {
                "error": {
                    "message": (
                        "The model is overloaded. Please try again later.  "
                        "tid:701d7fede7890fd8b7a54931fff47dfd_1787199691584"
                    )
                }
            },
        )
        self.assertIn("模型当前繁忙", msg)
        self.assertNotIn("额度或频率受限", msg)

    def test_real_quota_429_still_says_limited(self):
        msg = _friendly_error(429, {"error": {"message": "You exceeded your current quota"}})
        self.assertIn("额度或频率受限", msg)

    def test_overloaded_retry_waits_longer(self):
        self.assertGreaterEqual(
            _retry_wait_seconds(0, "GPT 生图模型当前繁忙，请稍后再试"),
            5,
        )
        self.assertEqual(_retry_wait_seconds(0, "GPT 生图额度或频率受限"), 1)

    def test_gateway_timeout_payload_is_clean_and_retryable(self):
        msg = _friendly_error(
            500,
            {"msg": "Request Timeout", "code": 500, "data": None, "http_status": 504},
        )
        self.assertIn("网关超时", msg)
        self.assertNotIn("{'msg'", msg)
        self.assertGreaterEqual(_retry_wait_seconds(0, msg), 8)

    def test_payload_prefers_gateway_http_status(self):
        from gpt_image_client import _payload_http_status
        self.assertEqual(
            _payload_http_status(500, {"msg": "Request Timeout", "http_status": 504}),
            504,
        )


if __name__ == "__main__":
    unittest.main()
