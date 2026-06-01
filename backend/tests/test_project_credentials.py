import os
import unittest
from unittest.mock import patch

from project_credentials import (
    ProjectCredentialsError,
    load_lovart_credentials_for_project,
    require_project_llm_config,
)


class ProjectCredentialsTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "LOVART_ACCESS_KEY_HLL": "ak_hll",
            "LOVART_SECRET_KEY_HLL": "sk_hll",
            "LOVART_ACCESS_KEY_XDT": "ak_xdt",
            "LOVART_SECRET_KEY_XDT": "sk_xdt",
            "DEEPSEEK_API_KEY_HLL": "ds_hll",
            "DEEPSEEK_API_KEY_XDT": "ds_xdt",
            "DEEPSEEK_BASE_URL": "https://example.com",
            "DEEPSEEK_MODEL": "m1",
            "LOVART_BASE_URL": "https://lgw.lovart.ai",
        },
        clear=False,
    )
    def test_lovart_per_project(self):
        hll = load_lovart_credentials_for_project("画啦啦")
        xdt = load_lovart_credentials_for_project("小灯塔")
        self.assertEqual(hll, [("ak_hll", "sk_hll")])
        self.assertEqual(xdt, [("ak_xdt", "sk_xdt")])

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "global-only"}, clear=False)
    def test_no_global_fallback(self):
        env = os.environ
        for key in list(env.keys()):
            if key.endswith("_HLL") or key.endswith("_XDT"):
                env.pop(key, None)
        with self.assertRaises(ProjectCredentialsError):
            require_project_llm_config("画啦啦")
