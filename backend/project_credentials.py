"""按项目组加载 LLM / Lovart 配置（禁止无后缀全局 Key 回落）。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from project_auth import ALLOWED_PROJECTS, project_slug


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


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_lovart_credentials_for_project(project: str) -> list[tuple[str, str]]:
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
    )


def require_project_llm_config(project: str) -> ProjectLlmConfig:
    return get_project_llm_config(project)


def credentials_status(project: str) -> dict[str, bool]:
    try:
        cfg = get_project_llm_config(project)
    except ProjectCredentialsError:
        return {"deepseek": False, "lovart": False}
    return {
        "deepseek": bool(cfg.deepseek_api_key),
        "lovart": bool(cfg.lovart_credentials),
        "qianwen": bool(cfg.qianwen_api_key),
        "kimi": bool(cfg.kimi_api_key),
        "doubao": bool(cfg.doubao_api_key),
    }
