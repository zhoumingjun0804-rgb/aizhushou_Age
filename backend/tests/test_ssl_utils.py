import unittest

from ssl_utils import host_bypasses_proxy, should_use_proxy_for_url


class TestProxyBypass(unittest.TestCase):
    def test_company_gpt_hosts_never_use_http_proxy(self):
        self.assertTrue(host_bypasses_proxy("liuyi-llm-risk.61info.cn"))
        self.assertTrue(host_bypasses_proxy("gptproto.com"))
        self.assertTrue(host_bypasses_proxy("api.gptproto.com"))
        self.assertFalse(
            should_use_proxy_for_url("https://gptproto.com/v1/images/generations", True)
        )
        self.assertFalse(
            should_use_proxy_for_url(
                "https://liuyi-llm-risk.61info.cn/api/gptproto/v1/images/generations",
                True,
            )
        )

    def test_external_https_can_use_proxy(self):
        self.assertTrue(should_use_proxy_for_url("https://lgw.lovart.ai/v1", True))


if __name__ == "__main__":
    unittest.main()
