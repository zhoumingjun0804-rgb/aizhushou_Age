import os
import unittest
from unittest.mock import patch

from project_credentials import (
    ProjectCredentialsError,
    analyze_model_allowed,
    get_available_models,
    get_gpt_chat_settings,
    get_gpt_image_settings,
    get_project_gpt_api_key,
    get_project_llm_config,
    image_backend_allowed,
    load_lovart_credentials_for_project,
    lovart_enabled_for_project,
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
        clear=True,
    )
    def test_lovart_per_project(self):
        hll = load_lovart_credentials_for_project("画啦啦")
        xdt = load_lovart_credentials_for_project("小灯塔")
        self.assertEqual(hll, [("ak_hll", "sk_hll")])
        # 小灯塔已停用 Lovart，即使 env 有 Key 也不加载
        self.assertEqual(xdt, [])
        self.assertTrue(lovart_enabled_for_project("画啦啦"))
        self.assertFalse(lovart_enabled_for_project("小灯塔"))

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "global-only"}, clear=False)
    def test_no_global_fallback(self):
        env = os.environ
        for key in list(env.keys()):
            if key.endswith("_HLL") or key.endswith("_XDT"):
                env.pop(key, None)
        with self.assertRaises(ProjectCredentialsError):
            require_project_llm_config("画啦啦")

    @patch.dict(
        os.environ,
        {
            "LOVART_ACCESS_KEY_HLL": "ak_hll",
            "LOVART_SECRET_KEY_HLL": "sk_hll",
            "DEEPSEEK_API_KEY_HLL": "ds_hll",
            "OPENAI_IMAGE_PROVIDER_HLL": "azure",
            "OPENAI_API_KEY_HLL": "cf88022e744b473fba1664303b725371",
            "OPENAI_IMAGE_BASE_URL_HLL": "https://llm-risk-coding.61info.cn/api/azure-open-ai-hll-smart-draw/openai",
            "DEEPSEEK_BASE_URL": "https://agenthub.vipthink.cn",
        },
        clear=True,
    )
    def test_available_models_hll_lovart_and_gpt_polish_only(self):
        models = get_available_models("画啦啦")
        values = [b["value"] for b in models["image_backends"]]
        self.assertIn("lovart", values)
        self.assertIn("gpt:gpt-image-2", values)
        analyze_values = [m["value"] for m in models["analyze_models"]]
        self.assertIn("", analyze_values)
        self.assertIn("gpt-5.4", analyze_values)
        cfg = get_project_llm_config("画啦啦")
        self.assertEqual(get_project_gpt_api_key("HLL"), "cf88022e744b473fba1664303b725371")
        chat = get_gpt_chat_settings(cfg)
        self.assertEqual(chat.api_key, "cf88022e744b473fba1664303b725371")
        self.assertEqual(chat.provider, "azure")

    @patch.dict(
        os.environ,
        {
            "LOVART_ACCESS_KEY_XDT": "ak_xdt",
            "LOVART_SECRET_KEY_XDT": "sk_xdt",
            "DEEPSEEK_API_KEY_XDT": "ds_xdt",
            "OPENAI_API_KEY_XDT": "sk-proj-official",
            "OPENAI_IMAGE_BASE_URL": "https://api.openai.com",
            "DEEPSEEK_BASE_URL": "https://agenthub.vipthink.cn",
        },
        clear=True,
    )
    @patch.dict(
        os.environ,
        {
            "DEEPSEEK_API_KEY_XDT": "ds_xdt",
            "OPENAI_API_KEY_XDT": "50698569116849458f0bf35abc066b76",
            "OPENAI_IMAGE_BASE_URL_XDT": (
                "https://llm-risk-coding.61info.cn/api/azure-open-ai-xdt-smart-draw/openai"
            ),
        },
        clear=True,
    )
    def test_available_models_xdt_azure_gateway(self):
        models = get_available_models("小灯塔")
        values = [b["value"] for b in models["image_backends"]]
        self.assertIn("gpt:gpt-image-2", values)
        image_cfg = get_gpt_image_settings(get_project_llm_config("小灯塔"))
        self.assertEqual(image_cfg.provider, "azure")
        self.assertEqual(get_project_gpt_api_key("XDT"), "50698569116849458f0bf35abc066b76")

    @patch.dict(
        os.environ,
        {
            "LOVART_ACCESS_KEY_XDT": "ak_xdt",
            "LOVART_SECRET_KEY_XDT": "sk_xdt",
            "DEEPSEEK_API_KEY_XDT": "ds_xdt",
            "OPENAI_API_KEY_XDT": "sk-proj-official",
            "OPENAI_IMAGE_BASE_URL": "https://api.openai.com",
            "DEEPSEEK_BASE_URL": "https://agenthub.vipthink.cn",
        },
        clear=True,
    )
    def test_available_models_xdt_gpt_image_without_polish_gpt(self):
        models = get_available_models("小灯塔")
        values = [b["value"] for b in models["image_backends"]]
        self.assertNotIn("lovart", values)
        self.assertIn("gpt:gpt-image-2", values)
        self.assertFalse(image_backend_allowed("小灯塔", "lovart"))
        analyze_values = [m["value"] for m in models["analyze_models"]]
        self.assertIn("", analyze_values)
        self.assertIn("gpt-5.4", analyze_values)
        self.assertTrue(analyze_model_allowed("小灯塔", "gpt-5.4"))
