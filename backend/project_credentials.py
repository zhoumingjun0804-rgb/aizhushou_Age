"""按项目组加载 LLM / Lovart 配置（禁止无后缀全局 Key 回落）。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from gpt_image_client import (
    GPT_IMAGE_MODELS,
    is_azure_gateway,
    is_official_openai_api_key,
    resolve_gpt_auth,
)
from project_auth import ALLOWED_PROJECTS, project_slug

GPT_IMAGE_LABELS = {
    "gpt-image-2": "GPT Image 2",
    "gpt-image-1.5": "GPT Image 1.5",
    "gpt-image-1-mini": "GPT Image 1 Mini",
}

OFFICIAL_OPENAI_IMAGE_BASE = "https://api.openai.com"

# 小灯塔已停用 Lovart：忽略 env 中的 LOVART_*_XDT，前端不再展示该入口
LOVART_DISABLED_PROJECTS = frozenset({"小灯塔"})


class ProjectCredentialsError(Exception):
    pass


@dataclass
class ProjectLlmConfig:
    slug: str
    project: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    qianwen_api_key: str
    qianwen_base_url: str
    qianwen_model: str
    kimi_api_key: str
    kimi_base_url: str
    kimi_model: str
    doubao_api_key: str
    doubao_base_url: str
    doubao_model: str
    doubao_vision_model: str
    lovart_credentials: list[tuple[str, str]]
    lovart_base_url: str
    openai_api_key: str
    openai_base_url: str
    openai_chat_model: str


@dataclass
class GptImageSettings:
    api_key: str
    base_url: str
    provider: str
    fallback_bearer_key: str = ""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def lovart_enabled_for_project(project: str) -> bool:
    return project not in LOVART_DISABLED_PROJECTS


def load_lovart_credentials_for_project(project: str) -> list[tuple[str, str]]:
    if not lovart_enabled_for_project(project):
        return []

    slug = project_slug(project)
    pairs: list[tuple[str, str]] = []

    def append(ak: str, sk: str) -> None:
        ak, sk = ak.strip(), sk.strip()
        if ak and sk:
            pairs.append((ak, sk))

    append(_env(f"LOVART_ACCESS_KEY_{slug}"), _env(f"LOVART_SECRET_KEY_{slug}"))

    for index in range(2, 11):
        ak = _env(f"LOVART_ACCESS_KEY_{slug}_{index}")
        sk = _env(f"LOVART_SECRET_KEY_{slug}_{index}")
        if not ak and not sk:
            continue
        append(ak, sk)

    bulk_aks = _env(f"LOVART_ACCESS_KEYS_{slug}")
    bulk_sks = _env(f"LOVART_SECRET_KEYS_{slug}")
    if bulk_aks and bulk_sks:
        aks = [p for p in bulk_aks.split(",") if p.strip()]
        sks = [p for p in bulk_sks.split(",") if p.strip()]
        for ak, sk in zip(aks, sks):
            append(ak, sk)

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def _is_agenthub_url(base_url: str) -> bool:
    return "agenthub" in (base_url or "").lower()


def azure_image_base_mismatch(slug: str, base_url: str) -> str | None:
    """小灯塔勿指向不存在的 hll-smart-draw 路径。"""
    lower = (base_url or "").lower()
    if slug == "XDT" and "hll-smart-draw" in lower:
        return (
            "小灯塔的 OPENAI_IMAGE_BASE_URL_XDT 配成了 azure-open-ai-hll-smart-draw（网关无此路径）。"
            "请改为 azure-open-ai-xdt-smart-draw，并使用有效的 OPENAI_API_KEY_XDT。"
        )
    return None


def _resolve_gpt_image_provider(slug: str, image_base: str) -> str:
    explicit = (_env(f"OPENAI_IMAGE_PROVIDER_{slug}") or _env("OPENAI_IMAGE_PROVIDER") or "").lower()
    if explicit in ("azure", "company", "azure-openai"):
        return "azure"
    if explicit == "agenthub":
        return "agenthub"
    if explicit == "official":
        return "official"
    if is_azure_gateway(image_base):
        return "azure"
    if _is_agenthub_url(image_base):
        return "agenthub"
    return "official"


def get_project_gpt_api_key(slug: str) -> str:
    """项目组 GPT Key（生图 / 润色 / chat 共用，禁止无后缀回落）。"""
    return _env(f"OPENAI_API_KEY_{slug}") or _env(f"OPENAI_APP_KEY_{slug}")


def _gpt_key_usable(api_key: str, provider: str) -> bool:
    if not api_key:
        return False
    if provider == "official":
        return is_official_openai_api_key(api_key)
    return True


def get_gpt_chat_settings(cfg: ProjectLlmConfig) -> GptImageSettings:
    """GPT 润色 / chat：与生图共用 Key，Azure 网关共用 IMAGE_BASE_URL。"""
    slug = cfg.slug
    api_key = get_project_gpt_api_key(slug)
    image_base = _env(f"OPENAI_IMAGE_BASE_URL_{slug}") or _env("OPENAI_IMAGE_BASE_URL")
    provider = _resolve_gpt_image_provider(slug, image_base)
    if provider == "azure":
        chat_base = image_base
    else:
        chat_base = (
            _env(f"OPENAI_CHAT_BASE_URL_{slug}")
            or _env(f"OPENAI_BASE_URL_{slug}")
            or _env("OPENAI_BASE_URL")
            or image_base
            or cfg.openai_base_url
        )
    return GptImageSettings(
        api_key=api_key,
        base_url=chat_base,
        provider=provider,
        fallback_bearer_key=cfg.deepseek_api_key,
    )


def get_gpt_image_settings(cfg: ProjectLlmConfig) -> GptImageSettings:
    """GPT 生图：official / azure（公司网关 api-key）/ agenthub。"""
    slug = cfg.slug
    image_base = _env(f"OPENAI_IMAGE_BASE_URL_{slug}") or _env("OPENAI_IMAGE_BASE_URL")
    provider = _resolve_gpt_image_provider(slug, image_base)
    api_key = get_project_gpt_api_key(slug)

    if provider == "azure":
        return GptImageSettings(
            api_key=api_key,
            base_url=image_base,
            provider="azure",
        )

    if provider == "agenthub":
        return GptImageSettings(
            api_key=api_key,
            base_url=image_base
            or _env(f"OPENAI_BASE_URL_{slug}")
            or _env("OPENAI_BASE_URL")
            or cfg.deepseek_base_url,
            provider="agenthub",
            fallback_bearer_key=cfg.deepseek_api_key,
        )

    return GptImageSettings(
        api_key=api_key,
        base_url=image_base or OFFICIAL_OPENAI_IMAGE_BASE,
        provider="official",
    )


def get_project_llm_config(project: str) -> ProjectLlmConfig:
    if project not in ALLOWED_PROJECTS:
        raise ProjectCredentialsError(f"未知项目组: {project}")
    slug = project_slug(project)

    deepseek_key = _env(f"DEEPSEEK_API_KEY_{slug}")
    if not deepseek_key:
        raise ProjectCredentialsError(f"{project} 未配置 DEEPSEEK_API_KEY_{slug}")

    return ProjectLlmConfig(
        slug=slug,
        project=project,
        deepseek_api_key=deepseek_key,
        deepseek_base_url=_env(f"DEEPSEEK_BASE_URL_{slug}") or _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=_env(f"DEEPSEEK_MODEL_{slug}") or _env("DEEPSEEK_MODEL", "deepseek-chat"),
        qianwen_api_key=_env(f"QIANWEN_API_KEY_{slug}"),
        qianwen_base_url=_env(f"QIANWEN_BASE_URL_{slug}")
        or _env("QIANWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        qianwen_model=_env(f"QIANWEN_MODEL_{slug}") or _env("QIANWEN_MODEL", "qwen-plus"),
        kimi_api_key=_env(f"KIMI_API_KEY_{slug}"),
        kimi_base_url=_env(f"KIMI_BASE_URL_{slug}") or _env("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
        kimi_model=_env(f"KIMI_MODEL_{slug}") or _env("KIMI_MODEL", "moonshot-v1-8k"),
        doubao_api_key=_env(f"DOUBAO_API_KEY_{slug}"),
        doubao_base_url=_env(f"DOUBAO_BASE_URL_{slug}")
        or _env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        doubao_model=_env(f"DOUBAO_MODEL_{slug}") or _env("DOUBAO_MODEL", "doubao-pro-32k"),
        doubao_vision_model=_env(f"DOUBAO_VISION_MODEL_{slug}")
        or _env("DOUBAO_VISION_MODEL", "doubao-1-5-vision-pro-32k-250115"),
        lovart_credentials=load_lovart_credentials_for_project(project),
        lovart_base_url=_env(f"LOVART_BASE_URL_{slug}") or _env("LOVART_BASE_URL", "https://lgw.lovart.ai"),
        openai_api_key=get_project_gpt_api_key(slug),
        openai_base_url=_env(f"OPENAI_CHAT_BASE_URL_{slug}")
        or _env(f"OPENAI_IMAGE_BASE_URL_{slug}")
        or _env(f"OPENAI_BASE_URL_{slug}")
        or _env("OPENAI_BASE_URL")
        or _env("DEEPSEEK_BASE_URL", "https://agenthub.vipthink.cn"),
        openai_chat_model=_env(f"OPENAI_CHAT_MODEL_{slug}") or _env("OPENAI_CHAT_MODEL", "gpt-5.4"),
    )


def require_project_llm_config(project: str) -> ProjectLlmConfig:
    return get_project_llm_config(project)


def get_available_models(project: str) -> dict[str, list[dict[str, str]]]:
    """按项目组 env 返回前端可选的生图 / 润色模型列表。"""
    try:
        cfg = get_project_llm_config(project)
    except ProjectCredentialsError:
        return {"image_backends": [], "analyze_models": []}

    image_backends: list[dict[str, str]] = []
    image_cfg = get_gpt_image_settings(cfg)
    if _gpt_key_usable(image_cfg.api_key, image_cfg.provider):
        for model_id in GPT_IMAGE_MODELS:
            image_backends.append(
                {
                    "value": f"gpt:{model_id}",
                    "label": GPT_IMAGE_LABELS.get(model_id, model_id),
                }
            )
    if cfg.lovart_credentials:
        image_backends.append({"value": "lovart", "label": "Lovart 龙虾"})

    analyze_models: list[dict[str, str]] = []
    if cfg.deepseek_api_key:
        model_id = cfg.deepseek_model or "claude-haiku"
        if "claude" in model_id.lower():
            label = "Claude Haiku（默认）"
        else:
            label = f"{model_id}（默认）"
        analyze_models.append({"value": "", "label": label})

    chat_cfg = get_gpt_chat_settings(cfg)
    chat_ok = _gpt_key_usable(chat_cfg.api_key, chat_cfg.provider)
    if chat_cfg.provider == "agenthub" and chat_ok:
        auth = resolve_gpt_auth(chat_cfg.api_key, cfg.deepseek_api_key, chat_cfg.base_url)
        chat_ok = bool(auth.bearer)
    if chat_ok:
        chat_model = cfg.openai_chat_model or "gpt-5.4"
        analyze_models.append({"value": chat_model, "label": chat_model})

    return {"image_backends": image_backends, "analyze_models": analyze_models}


def image_backend_allowed(project: str, image_backend_value: str | None) -> bool:
    raw = (image_backend_value or "lovart").strip()
    allowed = {item["value"] for item in get_available_models(project)["image_backends"]}
    return raw in allowed


def analyze_model_allowed(project: str, analyze_model: str | None) -> bool:
    raw = (analyze_model or "").strip()
    allowed = {item["value"] for item in get_available_models(project)["analyze_models"]}
    return raw in allowed


def gpt_image_available_for_project(project: str) -> bool:
    try:
        cfg = get_project_llm_config(project)
    except ProjectCredentialsError:
        return False
    image_cfg = get_gpt_image_settings(cfg)
    if not _gpt_key_usable(image_cfg.api_key, image_cfg.provider):
        return False
    if image_cfg.provider in ("azure", "agenthub") and not (image_cfg.base_url or "").strip():
        return False
    return True


def credentials_status(project: str) -> dict[str, bool]:
    try:
        cfg = get_project_llm_config(project)
    except ProjectCredentialsError:
        return {"deepseek": False, "lovart": False, "gpt": False}
    image_cfg = get_gpt_image_settings(cfg)
    gpt_ok = _gpt_key_usable(image_cfg.api_key, image_cfg.provider)
    return {
        "deepseek": bool(cfg.deepseek_api_key),
        "gpt": gpt_ok,
        "lovart": bool(cfg.lovart_credentials),
        "qianwen": bool(cfg.qianwen_api_key),
        "kimi": bool(cfg.kimi_api_key),
        "doubao": bool(cfg.doubao_api_key),
    }
