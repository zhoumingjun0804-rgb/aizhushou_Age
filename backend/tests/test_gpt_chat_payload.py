import unittest

from gpt_image_client import build_chat_completion_payload, chat_completion_token_param


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


if __name__ == "__main__":
    unittest.main()
