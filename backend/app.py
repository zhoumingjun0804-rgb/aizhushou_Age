#!/usr/bin/env python3
"""AI 视觉设计助手 v4 - 需求解析 + 多图变体 + 项目组选择"""
import http.server
import socketserver
import json
import uuid
import subprocess
import pathlib
import urllib.request
import urllib.parse
import ssl
import time
import re
import os
import shutil
import errno
import threading
import concurrent.futures
from typing import Optional

from lovart_client import (
    LovartClient,
    LovartError,
    is_lovart_connection_error,
    is_lovart_limit_error,
    mask_access_key,
)
from project_auth import (
    unlock,
    resolve_token,
    password_for,
    ALLOWED_PROJECTS,
    project_slug,
    is_gate_enabled,
)
from project_credentials import (
    ProjectCredentialsError,
    ProjectLlmConfig,
    analyze_model_allowed,
    credentials_status,
    get_available_models,
    get_gpt_chat_settings,
    get_gpt_image_settings,
    gpt_image_available_for_project,
    image_backend_allowed,
    load_lovart_credentials_for_project,
    require_project_llm_config,
)
from lovart_queue import (
    DuplicateHighJobError,
    LovartQueue,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    QueueFullError,
)
from comfyui_client import ComfyUIClient, ComfyUIClientError
from gpt_image_client import (
    GptImageClient,
    GptImageError,
    build_chat_completion_payload,
    call_gpt_chat,
    resolve_gpt_image_model,
    validate_official_gpt_image_key,
)
from sd_client import StableDiffusionClient, SDClientError
from gif_to_svga.converter import (
    DEFAULT_MAX_BYTES,
    gif_to_svga as convert_gif_to_svga,
    VALID_FPS as SVGA_VALID_FPS,
)
from ai_outpaint import (
    layout_background_extend_prompt,
    run_lovart_extend_to_size,
    run_gpt_extend_to_size,
    splash_extend_prompt,
    splash_subframe_extend_prompt,
)
from multi_size_export import (
    export_multi_sizes,
    export_splash_subframe_sizes,
    export_manual_splash_uploads,
    merge_multi_size_export_results,
    build_manual_only_export_result,
    load_output_sizes,
    normalize_export_sizes,
    normalize_product_type,
    sizes_config_path,
)
from layout_extend import export_layout_extend, list_layout_presets
from product_design import (
    collect_reference_image_paths,
    count_project_assets,
    detect_project_catalog,
    list_design_types_for_project,
    list_flat_reference_images,
    list_typed_reference_images,
    project_product_type,
    project_refs_dir,
    read_project_meta,
    typed_reference_dir,
)
from image_crop import crop_image_to_size
from image_cutout import (
    apply_cutout_mask,
    build_ai_extract_prompt,
    compute_extract_crop_bbox,
    has_rembg,
    preferred_cutout_backend,
    save_extract_crop,
)
from gif_maker import make_animated_gif, make_breathing_gif


def _resolve_dreamina_bin():
    env_bin = os.environ.get("DREAMINA_BIN", "").strip()
    if env_bin:
        return env_bin
    detected = shutil.which("dreamina")
    if detected:
        return detected
    return "dreamina"


def _dreamina_command_path():
    dreamina_path = pathlib.Path(DREAMINA_BIN)
    if dreamina_path.is_file():
        return str(dreamina_path)
    detected = shutil.which(DREAMINA_BIN)
    if detected:
        return detected
    return None

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
PROJECTS_DIR = pathlib.Path(os.environ.get("PROJECTS_DIR", str(BASE_DIR / "projects")))
HISTORY_FILE = BASE_DIR / "history.json"


def _load_env_file(overwrite: bool = False):
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (overwrite or key not in os.environ):
            os.environ[key] = value


def _reload_runtime_env():
    """重新读取 .env，使修改 Key 后无需重启进程（开发模式）。"""
    global LOVART_BASE_URL
    global LOVART_POLL_TIMEOUT, LOVART_MAX_CONCURRENCY, LOVART_TASK_RETRY
    global LOVART_TASK_RETRY_WAIT, LOVART_MODE, LOVART_QUALITY_HINT, IMAGE_BACKEND
    global LOVART_QUEUE_MAX, LOVART_JOB_TTL, LOVART_JOB_MAX_SECONDS, LOVART_ETA_AVG_SECONDS
    global lovart_queue
    global COMFYUI_API_URL, COMFYUI_CHECKPOINT, SD_API_URL, LOCAL_GENERATION_TIMEOUT
    global DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    global QIANWEN_BASE_URL, QIANWEN_MODEL
    global KIMI_BASE_URL, KIMI_MODEL
    global DOUBAO_BASE_URL, DOUBAO_MODEL, DOUBAO_VISION_MODEL
    _load_env_file(overwrite=True)
    LOVART_BASE_URL = os.environ.get("LOVART_BASE_URL", "https://lgw.lovart.ai").strip()
    LOVART_POLL_TIMEOUT = int(os.environ.get("LOVART_POLL_TIMEOUT", "300"))
    LOVART_MAX_CONCURRENCY = max(1, int(os.environ.get("LOVART_MAX_CONCURRENCY", "1")))
    LOVART_TASK_RETRY = max(1, int(os.environ.get("LOVART_TASK_RETRY", "5")))
    LOVART_TASK_RETRY_WAIT = max(5, int(os.environ.get("LOVART_TASK_RETRY_WAIT", "15")))
    LOVART_MODE = os.environ.get("LOVART_MODE", "fast").strip() or "fast"
    LOVART_QUALITY_HINT = os.environ.get(
        "LOVART_QUALITY_HINT",
        "适合手机屏幕与网页展示，宽度约1200到1536像素，细节清晰但不必4K",
    ).strip()
    IMAGE_BACKEND = os.environ.get("IMAGE_BACKEND", "lovart").strip().lower()
    COMFYUI_API_URL = os.environ.get("COMFYUI_API_URL", "http://127.0.0.1:8188").strip()
    COMFYUI_CHECKPOINT = os.environ.get("COMFYUI_CHECKPOINT", "").strip()
    SD_API_URL = os.environ.get("SD_API_URL", "http://127.0.0.1:7860").strip()
    LOCAL_GENERATION_TIMEOUT = int(os.environ.get("LOCAL_GENERATION_TIMEOUT", "300"))
    DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    QIANWEN_BASE_URL = os.environ.get("QIANWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    QIANWEN_MODEL = os.environ.get("QIANWEN_MODEL", "qwen-plus")
    KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshot-v1-8k")
    DOUBAO_BASE_URL = os.environ.get("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "doubao-pro-32k")
    DOUBAO_VISION_MODEL = os.environ.get("DOUBAO_VISION_MODEL", "doubao-1-5-vision-pro-32k-250115")
    LOVART_QUEUE_MAX = max(1, int(os.environ.get("LOVART_QUEUE_MAX", "20")))
    LOVART_JOB_TTL = max(60, int(os.environ.get("LOVART_JOB_TTL", "3600")))
    LOVART_JOB_MAX_SECONDS = max(60, int(os.environ.get("LOVART_JOB_MAX_SECONDS", "1800")))
    LOVART_ETA_AVG_SECONDS = max(10, int(os.environ.get("LOVART_ETA_AVG_SECONDS", "90")))
    lovart_queue = _make_lovart_queue()


_load_env_file()
DREAMINA_BIN = _resolve_dreamina_bin()
PORT = int(os.environ.get("PORT", "8000"))
LOVART_BASE_URL = os.environ.get("LOVART_BASE_URL", "https://lgw.lovart.ai").strip()
LOVART_POLL_TIMEOUT = int(os.environ.get("LOVART_POLL_TIMEOUT", "300"))
LOVART_MAX_CONCURRENCY = max(1, int(os.environ.get("LOVART_MAX_CONCURRENCY", "1")))
LOVART_TASK_RETRY = max(1, int(os.environ.get("LOVART_TASK_RETRY", "5")))
LOVART_TASK_RETRY_WAIT = max(5, int(os.environ.get("LOVART_TASK_RETRY_WAIT", "15")))
LOVART_MODE = os.environ.get("LOVART_MODE", "fast").strip() or "fast"
LOVART_QUALITY_HINT = os.environ.get(
    "LOVART_QUALITY_HINT",
    "适合手机屏幕与网页展示，宽度约1200到1536像素，细节清晰但不必4K",
).strip()
LOVART_QUEUE_MAX = max(1, int(os.environ.get("LOVART_QUEUE_MAX", "20")))
LOVART_JOB_TTL = max(60, int(os.environ.get("LOVART_JOB_TTL", "3600")))
LOVART_JOB_MAX_SECONDS = max(60, int(os.environ.get("LOVART_JOB_MAX_SECONDS", "1800")))
LOVART_ETA_AVG_SECONDS = max(10, int(os.environ.get("LOVART_ETA_AVG_SECONDS", "90")))
SMART_CUTOUT_JOB_DIR = UPLOAD_DIR / "smart_cutout_jobs"
COMFYUI_API_URL = os.environ.get("COMFYUI_API_URL", "http://127.0.0.1:8188").strip()
COMFYUI_CHECKPOINT = os.environ.get("COMFYUI_CHECKPOINT", "").strip()
SD_API_URL = os.environ.get("SD_API_URL", "http://127.0.0.1:7860").strip()
LOCAL_GENERATION_TIMEOUT = int(os.environ.get("LOCAL_GENERATION_TIMEOUT", "300"))
IMAGE_BACKEND = os.environ.get("IMAGE_BACKEND", "lovart").strip().lower()

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _make_lovart_queue() -> LovartQueue:
    return LovartQueue(
        max_workers=LOVART_MAX_CONCURRENCY,
        queue_max=LOVART_QUEUE_MAX,
        job_ttl=LOVART_JOB_TTL,
        job_max_seconds=LOVART_JOB_MAX_SECONDS,
        eta_avg_seconds=LOVART_ETA_AVG_SECONDS,
    )


lovart_queue = _make_lovart_queue()

from ssl_utils import make_ssl_context

ssl_ctx = make_ssl_context()

# ─── 大模型 API（按项目组配置，见 project_credentials）────────────────
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
QIANWEN_BASE_URL = os.environ.get("QIANWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QIANWEN_MODEL = os.environ.get("QIANWEN_MODEL", "qwen-plus")
KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshot-v1-8k")
DOUBAO_BASE_URL = os.environ.get("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "doubao-pro-32k")
DOUBAO_VISION_MODEL = os.environ.get("DOUBAO_VISION_MODEL", "doubao-1-5-vision-pro-32k-250115")


def call_openai_chat(
    messages,
    config: ProjectLlmConfig,
    model: str,
    temperature=0.7,
    max_tokens=1000,
    timeout=120,
):
    """调用 GPT chat（按项目组 Key / Azure 或 AgentHub 网关）。"""
    chat_cfg = get_gpt_chat_settings(config)
    if not chat_cfg.api_key:
        return None, f"未配置 OPENAI_API_KEY_{config.slug}"
    return call_gpt_chat(
        messages,
        api_key=chat_cfg.api_key,
        base_url=chat_cfg.base_url,
        provider=chat_cfg.provider,
        model=model,
        fallback_bearer_key=chat_cfg.fallback_bearer_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def call_deepseek(
    messages,
    config: ProjectLlmConfig,
    temperature=0.7,
    max_tokens=1000,
    timeout=120,
    max_retries=2,
):
    """调用 DeepSeek / AgentHub Claude 等 OpenAI 兼容 chat API"""
    if not config.deepseek_api_key:
        return None, f"未配置 DEEPSEEK_API_KEY_{config.slug}"
    headers = {
        "Authorization": f"Bearer {config.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    model = config.deepseek_model
    payload = build_chat_completion_payload(
        model,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    url = f"{config.deepseek_base_url.rstrip('/')}/v1/chat/completions"
    last_error = "润色请求失败"

    for attempt in range(max(1, max_retries)):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"], None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_error = f"HTTP Error {e.code}: {body[:200]}"
            if e.code in (524, 502, 503, 504, 429) and attempt + 1 < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None, last_error
        except Exception as e:
            last_error = str(e)
            lower = last_error.lower()
            if attempt + 1 < max_retries and ("timeout" in lower or "524" in lower):
                time.sleep(2 * (attempt + 1))
                continue
            return None, last_error
    return None, last_error


def call_qianwen(messages, config: ProjectLlmConfig, temperature=0.7, max_tokens=1000):
    """调用通义千问 API（OpenAI 兼容格式）"""
    if not config.qianwen_api_key:
        return None, f"未配置 QIANWEN_API_KEY_{config.slug}"
    headers = {
        "Authorization": f"Bearer {config.qianwen_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config.qianwen_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{config.qianwen_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content'], None
    except Exception as e:
        return None, str(e)


def call_kimi(messages, config: ProjectLlmConfig, temperature=0.7, max_tokens=1000):
    """调用 Kimi API（OpenAI 兼容格式）"""
    if not config.kimi_api_key:
        return None, f"未配置 KIMI_API_KEY_{config.slug}"
    headers = {
        "Authorization": f"Bearer {config.kimi_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config.kimi_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{config.kimi_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content'], None
    except Exception as e:
        return None, str(e)


def call_doubao(messages, config: ProjectLlmConfig, temperature=0.7, max_tokens=1000):
    """调用豆包 API（OpenAI 兼容格式）"""
    if not config.doubao_api_key:
        return None, f"未配置 DOUBAO_API_KEY_{config.slug}"
    headers = {
        "Authorization": f"Bearer {config.doubao_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config.doubao_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{config.doubao_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content'], None
    except Exception as e:
        return None, str(e)


# ─── 历史记录 ───────────────────────────────────────────────────
def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text('utf-8'))
        except:
            return []
    return []

def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), 'utf-8')

def add_history(entry):
    history = load_history()
    history.insert(0, entry)
    save_history(history)


def _history_title_from_prompt(prompt: str, limit: int = 28) -> str:
    if prompt is None:
        text = ""
    elif isinstance(prompt, str):
        text = prompt.strip()
    else:
        text = str(prompt).strip()
    if not text:
        return "未命名记录"
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _history_mode_label(mode: str) -> str:
    if mode == "edit":
        return "✏️局部修图"
    if mode == "text2img":
        return "✨文字生图"
    return "📷图片改图"


def _main_title_from_summary(summary) -> str:
    if not isinstance(summary, dict):
        return ""
    return str(summary.get("主标题") or "").strip()


def _history_meta_tags(mode: str, variants_count: int = 0) -> list:
    tags = [_history_mode_label(mode)]
    try:
        count = int(variants_count or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 1:
        tags.append(f"{count}张")
    return tags


def build_history_entry(*, mode: str, prompt: str, description: str = "", source: str = "", **kwargs) -> dict:
    entry = {
        "id": kwargs.pop("id", None) or uuid.uuid4().hex[:8],
        "timestamp": kwargs.pop("timestamp", None) or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "prompt": prompt,
        "title": _history_title_from_prompt(prompt),
        "description": (description or "").strip(),
        "meta_tags": _history_meta_tags(mode, kwargs.get("variants_count", 0)),
        "source": source,
        "schema_version": kwargs.pop("schema_version", None) or 1,
    }
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        entry[key] = value
    return entry


def _subframe_history_tool_label(tool: str) -> str:
    if tool == "landing_extend":
        return "📄头图延展"
    if tool == "splash_subframe":
        return "📐开屏拓展"
    return "📷多尺寸扩边"


def _infer_subframe_history_tool(sizes: list) -> str:
    ids = [str(spec.get("id", "")) for spec in sizes if isinstance(spec, dict)]
    if any(i.startswith("lhe_") for i in ids):
        return "landing_extend"
    if any(i.startswith("expand_") for i in ids):
        return "splash_subframe"
    return "subframe_export"


def _subframe_history_prompt(tool: str, sizes: list, remark: str = "") -> str:
    parts = []
    for spec in sizes:
        if not isinstance(spec, dict):
            continue
        parts.append(str(spec.get("name") or f"{spec.get('width')}×{spec.get('height')}"))
    size_text = "、".join(parts)
    if tool == "landing_extend":
        prefix = "头图延展"
    elif tool == "splash_subframe":
        prefix = "开屏拓展"
    else:
        prefix = "多尺寸扩边"
    prompt = f"{prefix} · {size_text}" if size_text else prefix
    extra = (remark or "").strip()
    if extra:
        prompt += f" · {extra}"
    return prompt


def add_subframe_export_history(
    *,
    tool: str,
    project: str,
    sizes: list,
    images: list,
    remark: str = "",
) -> None:
    ai_images = [
        img["filename"]
        for img in images
        if isinstance(img, dict) and img.get("filename") and img.get("source") != "passthrough"
    ]
    output_images = ai_images or [
        img["filename"] for img in images if isinstance(img, dict) and img.get("filename")
    ]
    if not output_images:
        return
    count = len(output_images)
    entry = build_history_entry(
        mode="img2img",
        prompt=_subframe_history_prompt(tool, sizes, remark),
        description=(remark or "").strip(),
        source=tool or "subframe_export",
        project=project,
        output_images=output_images,
        variants_count=count,
        meta_tags=[_subframe_history_tool_label(tool or ""), f"{count}张"],
    )
    add_history(entry)


def filter_history_items(items):
    """仅返回本地 outputs 仍存在的图片，避免历史缩略图 404。"""
    filtered = []
    for item in items:
        entry = dict(item)
        mode = entry.get("mode", "")
        prompt = entry.get("prompt", "")
        existing_title = entry.get("title")
        if existing_title is None or existing_title == "":
            entry["title"] = _history_title_from_prompt(prompt)
        elif isinstance(existing_title, str):
            entry["title"] = existing_title
        else:
            entry["title"] = str(existing_title)
        entry["description"] = str(entry.get("description") or "").strip()
        meta_tags = entry.get("meta_tags")
        if not isinstance(meta_tags, list) or not meta_tags:
            entry["meta_tags"] = _history_meta_tags(mode, entry.get("variants_count", 0))
        entry["schema_version"] = entry.get("schema_version") or 1
        images = list(entry.get("output_images") or [])
        if not images and entry.get("output_image"):
            images = [entry["output_image"]]
        available = [name for name in images if (OUTPUT_DIR / name).exists()]
        if available:
            entry["output_images"] = available
            entry["output_image"] = available[0]
        else:
            entry.pop("output_images", None)
            entry.pop("output_image", None)
        filtered.append(entry)
    return filtered


# ─── 项目组管理 ──────────────────────────────────────────────────
def list_projects():
    """列出允许的项目组（画啦啦、小灯塔）"""
    if not PROJECTS_DIR.exists():
        return []
    by_name = {}
    for p in PROJECTS_DIR.iterdir():
        if not p.is_dir() or p.name.startswith('.') or p.name not in ALLOWED_PROJECTS:
            continue
        meta = read_project_meta(p.name)
        assets = count_project_assets(p.name)
        by_name[p.name] = {
            "name": p.name,
            "display_name": p.name,
            "style_tags": meta.get("style_tags", []),
            "description": meta.get("description", ""),
            "lovart_project_id": meta.get("lovart_project_id", ""),
            "catalog": assets["catalog"],
            "product_type": project_product_type(p.name),
            "count": assets["imageCount"],
            "typeCount": assets["typeCount"],
        }
    return [by_name[name] for name in ALLOWED_PROJECTS if name in by_name]

def get_project_meta(project_name):
    """获取项目组元数据"""
    proj_dir = PROJECTS_DIR / project_name
    if not proj_dir.exists():
        return None
    meta_file = proj_dir / "project.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text('utf-8'))
        except:
            pass
    return {"name": project_name, "style_tags": [], "description": ""}


def save_project_meta(project_name, **updates):
    """写入项目组 project.json（如 lovart_project_id）。"""
    proj_dir = PROJECTS_DIR / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    meta = get_project_meta(project_name) or {}
    meta.setdefault("name", project_name)
    for key, value in updates.items():
        if value is not None:
            meta[key] = value
    (proj_dir / "project.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def lovart_project_title(local_project: str, meta: dict | None = None) -> str:
    """Lovart 网页侧项目文件夹名，默认「{项目组}-A智绘」。"""
    meta = meta if meta is not None else (get_project_meta(local_project) or {})
    custom = (meta.get("lovart_project_title") or "").strip()
    if custom:
        return custom
    base = (meta.get("display_name") or local_project).strip()
    return f"{base}-A智绘"


def _sync_lovart_project_title(client: LovartClient, project_id: str, title: str) -> None:
    if not title:
        return
    try:
        current = client.get_project_name(project_id)
        if current != title:
            client.rename_project(project_id, title)
    except LovartError:
        pass


def ensure_lovart_project(local_project: str, client: LovartClient) -> str:
    """本地项目组绑定一个 Lovart project_id；已绑定则只校验复用，不再 project/save 建新项目。"""
    if not local_project:
        return os.environ.get("LOVART_DEFAULT_PROJECT_ID", "").strip()

    proj_dir = PROJECTS_DIR / local_project
    proj_dir.mkdir(parents=True, exist_ok=True)

    meta = get_project_meta(local_project) or {}
    title = lovart_project_title(local_project, meta)
    existing = (meta.get("lovart_project_id") or "").strip()

    if existing and client.validate_project(existing):
        _sync_lovart_project_title(client, existing, title)
        print(f"[Lovart] 复用项目组「{local_project}」→ {existing[:16]}…")
        return existing

    if existing:
        print(f"[Lovart] 项目组「{local_project}」原绑定 {existing[:12]}… 已失效，将新建")

    new_id = client.create_project(title=title)
    if new_id:
        save_project_meta(
            local_project,
            lovart_project_id=new_id,
            display_name=title,
        )
        print(f"[Lovart] 项目组「{local_project}」首次绑定 Lovart 项目 {new_id[:16]}…")
    return new_id or ""


def lovart_project_required_error(project: str, product_type: str | None = None) -> Optional[str]:
    if (project or "").strip() or os.environ.get("LOVART_DEFAULT_PROJECT_ID", "").strip():
        return None
    return (
        "生图请先选择项目组；图片将归入该组绑定的 Lovart 项目。"
        "若要用 Lovart 网页里已有文件夹，请在 projects/<组名>/project.json 填写 lovart_project_id。"
    )


def _selected_images_from_fields(fields: dict, project: str) -> list:
    selected_imgs = fields.get("selected_project_images")
    if selected_imgs:
        try:
            return json.loads(selected_imgs) if isinstance(selected_imgs, str) else selected_imgs
        except json.JSONDecodeError:
            return []
    return []


def _build_image_paths_from_selection(fields: dict, project: str) -> list:
    """仅使用用户在项目参考图网格中勾选的图片，未勾选则不注入项目组素材。"""
    selected_list = _selected_images_from_fields(fields, project)
    if not selected_list:
        return []
    return collect_reference_image_paths(selected_list, project)

def get_project_images(project_name, design_type: str = ""):
    """获取项目参考图：folder_types 按设计类型子目录，static_types 为扁平 refs/。"""
    if not project_name:
        return []
    if detect_project_catalog(project_name) == "folder_types":
        if design_type:
            return list_typed_reference_images(project_name, design_type)
        return []
    return list_flat_reference_images(project_name)


# ─── 需求智能解析 ────────────────────────────────────────────────
STYLE_KEYWORDS = {
    "活泼": ["高饱和度", "圆润线条", "明亮配色"],
    "可爱": ["柔和曲线", "粉嫩色系", "Q版风格"],
    "专业": ["简洁线条", "商务蓝", "稳重构图"],
    "科技": ["渐变色", "蓝紫色调", "几何元素"],
    "温馨": ["暖色调", "柔和光效", "家庭氛围"],
    "趣味": ["撞色搭配", "手绘风格", "卡通元素"],
    "高端": ["金色点缀", "深色背景", "精致排版"],
    "清新": ["淡雅配色", "留白设计", "自然元素"],
}

AUDIENCE_KEYWORDS = {
    "3-6岁": ["幼儿", "宝宝", "小朋友", "幼儿园"],
    "6-12岁": ["小学生", "孩子", "儿童"],
    "家长": ["妈妈", "爸爸", "家长", "亲子"],
    "学生": ["学生", "大学生", "考研"],
}

CTA_KEYWORDS = ["立即领课", "免费试听", "立即报名", "点击参与", "马上报名", "限时优惠", "立即购买", "了解更多"]

TITLE_THEME_HINTS = [
    (("夏天", "夏日", "暑期", "炎热", "好热", "高温"), [
        "炎热阳光", "清凉解暑对比", "西瓜冷饮泳镜", "明亮蓝橙配色", "青春活力氛围", "夏日户外场景",
    ]),
    (("母亲节", "妈妈", "母亲"), [
        "温馨亲情氛围", "柔和粉紫配色", "鲜花与爱心元素", "温暖柔光", "家庭场景",
    ]),
    (("春节", "新年", "过年", "马年"), [
        "节日喜庆氛围", "红金配色", "灯笼烟花元素", "团圆场景", "国潮装饰",
    ]),
    (("招生", "报名", "课程", "试听"), [
        "教育宣传氛围", "活泼明亮配色", "卡通学生元素", "信息层级清晰", "行动号召醒目",
    ]),
    (("促销", "优惠", "折扣", "限时"), [
        "促销氛围", "高对比配色", "价格标签元素", "紧迫感构图", "重点信息突出",
    ]),
]

def parse_design_request(text):
    """解析设计需求，提取关键要素"""
    result = {
        "theme": "",
        "audience": [],
        "copywriting": [],
        "cta": [],
        "style": [],
        "style_params": [],
        "original_text": text
    }
    
    # 提取主题（简单规则：第一句或关键短语）
    sentences = re.split(r'[。！？\n]', text)
    if sentences:
        first_sentence = sentences[0].strip()
        if len(first_sentence) > 5:
            result["theme"] = first_sentence[:50]
    
    # 提取受众
    for audience, keywords in AUDIENCE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                result["audience"].append(audience)
                break
    
    # 提取 CTA
    for cta in CTA_KEYWORDS:
        if cta in text:
            result["cta"].append(cta)
    
    # 提取文案（引号内的内容）
    copy_matches = re.findall(r'[""「」『』【】]([^""「」『』【】]+)[""「」『』【】]', text)
    result["copywriting"] = copy_matches[:5]
    
    # 提取风格关键词
    for style, params in STYLE_KEYWORDS.items():
        if style in text:
            result["style"].append(style)
            result["style_params"].extend(params)
    
    # 去重
    result["audience"] = list(set(result["audience"]))
    result["style"] = list(set(result["style"]))
    result["style_params"] = list(set(result["style_params"]))
    
    return result

def generate_design_summary(parsed):
    """生成设计摘要"""
    summary = {
        "主题": parsed["theme"] or "待确认",
        "受众": "、".join(parsed["audience"]) if parsed["audience"] else "待确认",
        "核心文案": "、".join(parsed["copywriting"][:3]) if parsed["copywriting"] else "待确认",
        "动作召唤": "、".join(parsed["cta"]) if parsed["cta"] else "待确认",
        "视觉风格": "、".join(parsed["style"]) if parsed["style"] else "默认活泼风格",
        "建议参数": parsed["style_params"] if parsed["style_params"] else ["高饱和度", "圆润线条", "明亮配色"],
    }
    return summary

def build_prompt_from_summary(summary, project_meta=None):
    """根据设计需求生成 prompt（兼容旧调用）"""
    return expand_prompt_from_summary(summary, project_meta)


def _match_theme_hints(text):
    hints = []
    seen = set()
    for keywords, values in TITLE_THEME_HINTS:
        if any(keyword in text for keyword in keywords):
            for value in values:
                if value not in seen:
                    seen.add(value)
                    hints.append(value)
    return hints


def expand_prompt_from_summary(summary, project_meta=None):
    """结合主副标题扩写适合 AI 绘图的提示词"""
    design_type = (summary.get("设计类型") or "海报").strip()
    main_title = (summary.get("主标题") or "").strip()
    sub_title = (summary.get("副标题") or "").strip()
    visual_desc = (summary.get("画面描述") or "").strip()
    style = (summary.get("风格") or "").strip()
    layout = (summary.get("排版参考") or "").strip()
    notes = (summary.get("补充备注") or "").strip()

    parts = []
    if style:
        parts.append(f"{style}风格")
    else:
        parts.append("清新插画风格")
    parts.append(f"{design_type}设计")

    if main_title:
        parts.append(f'标题文字"{main_title}"')

    if sub_title and sub_title != main_title:
        parts.append(f'副标题"{sub_title}"')
    elif sub_title:
        parts.append("副标题与主标题呼应同一主题")

    copy_context = " ".join(
        part for part in [main_title, sub_title if sub_title != main_title else "", visual_desc, notes] if part
    )
    parts.extend(_match_theme_hints(copy_context))

    if visual_desc:
        parts.append(f"画面内容：{visual_desc}")
    elif not _match_theme_hints(copy_context):
        parts.append("根据标题文案延展主体、场景与氛围")

    if layout:
        parts.append(f"排版：{layout}")
    else:
        parts.append("主标题大字号突出，副标题次级排版，留白清晰")

    if notes:
        parts.append(f"备注：{notes}")

    if project_meta and project_meta.get("style_tags"):
        parts.append(f"品牌规范：{', '.join(project_meta['style_tags'])}")

    deduped = []
    seen = set()
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            deduped.append(part)
    return "，".join(deduped)


ANALYZE_SYSTEM_PROMPT = """你是专业的视觉设计助手，擅长将设计需求转化为适合AI绘图的提示词。

你的任务：
1. 理解用户的设计需求（设计类型、标题、画面描述、排版要求等）
2. 重点结合主标题与副标题的语义进行联想扩写，补充主体、配色、风格、构图、氛围
3. 当主副标题内容相同或相近时，不要机械重复文案，而是综合两者延展画面与情绪
4. 必须让主标题和副标题的文字内容出现在成图提示中，确保设计图能展示这些标题
5. 优化为简洁的提示词，适合 AI 绘图
6. 必要时补充缺失元素（如未指定配色，则根据标题主题推荐合适的配色方案）

输出格式要求：
- 直接输出优化后的提示词，不要解释
- 中英文混合，关键词用逗号分隔
- 风格标签放在前面，如：扁平插画风格、3D立体风格、中国风插画
- **主标题内容必须包含在提示词中**，如：标题文字“暑期班火热招生中”
- **副标题内容如有则包含**，如：副标题“限时优惠 前50名8折”
- 主体元素放在中间
- 氛围和细节放在后面

示例输出：
扁平插画风格,少儿教育海报,标题文字"暑期班火热招生中",副标题"限时优惠",蓝色橙色配色,可爱卡通角色,数学符号元素,简洁排版,活泼有趣氛围
3D立体风格,产品宣传图,标题"新品首发",紫色渐变背景,科技感光效,现代简约构图,专业商务氛围"""


def _primary_llm_provider_label(config: ProjectLlmConfig):
    base = (config.deepseek_base_url or "").lower()
    if "dtok.ai" in base or "agenthub" in base:
        return "dtok"
    return "deepseek"


def analyze_prompt_from_summary(
    summary,
    project_meta=None,
    regenerate=False,
    project: str = "",
    analyze_model: str = "",
):
    """调用 LLM 润色提示词；全部失败时降级为本地规则拼接。

    返回 (prompt, source, provider, model, warning)
    source: llm | fallback
    """
    user_parts = []
    if summary.get("设计类型"):
        user_parts.append(f"设计类型：{summary['设计类型']}")
    if summary.get("主标题"):
        user_parts.append(f"主标题：{summary['主标题']}")
    if summary.get("副标题"):
        user_parts.append(f"副标题：{summary['副标题']}")
    if summary.get("风格"):
        user_parts.append(f"风格：{summary['风格']}")
    if summary.get("画面描述"):
        user_parts.append(f"画面描述：{summary['画面描述']}")
    if summary.get("排版参考"):
        user_parts.append(f"排版参考：{summary['排版参考']}")
    if summary.get("补充备注"):
        user_parts.append(f"补充备注：{summary['补充备注']}")
    if project_meta and project_meta.get("style_tags"):
        user_parts.append(f"品牌风格要求：{', '.join(project_meta['style_tags'])}")
    user_parts.append("请结合主标题与副标题共同扩写画面关键词，避免只重复标题文字。")
    if regenerate:
        user_parts.append(
            "请给出一版与常见模板不同的新表述变体，可调整配色、构图与氛围词，但必须保留主副标题文案。"
        )

    user_message = "\n".join(user_parts) if user_parts else "请帮我生成通用的设计提示词"
    messages = [
        {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    temperature = 0.85 if regenerate else 0.7
    cfg = require_project_llm_config(project)
    chosen_model = (analyze_model or cfg.deepseek_model or "").strip()
    use_gpt = chosen_model.lower().startswith("gpt-")
    provider_label = f"GPT {chosen_model}" if use_gpt else "Claude Haiku"

    if use_gpt:
        ai_prompt, error = call_openai_chat(
            messages,
            cfg,
            model=chosen_model,
            temperature=temperature,
            max_tokens=500,
        )
        if not error:
            print(f"[GPT] AI 输出: {ai_prompt}")
            return (ai_prompt or "").strip(), "llm", "gpt", chosen_model, None
        print(f"[GPT Error] {error}, 降级到简单拼接")
        primary_error = error
    else:
        ai_prompt, error = call_deepseek(messages, cfg, temperature=temperature, max_tokens=500)
        if not error:
            print(f"[{_primary_llm_provider_label(cfg)}] AI 输出: {ai_prompt}")
            return (ai_prompt or "").strip(), "llm", _primary_llm_provider_label(cfg), cfg.deepseek_model, None
        print(f"[Claude/AgentHub Error] {error}, 降级到简单拼接")
        primary_error = error

    fallback = expand_prompt_from_summary(summary, project_meta)
    detail = (primary_error or "未知错误")[:160]
    if use_gpt:
        hint = f"请检查 OPENAI_API_KEY_{cfg.slug} / AgentHub 连通性，或改选 Claude Haiku（默认）润色模型。"
    else:
        hint = (
            f"请检查 DEEPSEEK_API_KEY_{cfg.slug} / AgentHub 连通性，"
            f"稍后再试，或改选 {cfg.openai_chat_model or 'gpt-5.4'} 润色模型。"
        )
    warning = f"{provider_label} 润色失败（{detail}），已使用本地规则拼接。{hint}"
    return fallback, "fallback", "local", "", warning


def _normalize_lovart_error(message):
    if not message:
        return message
    if is_lovart_connection_error(message):
        return (
            f"无法连接 Lovart 服务器（{LOVART_BASE_URL}）。"
            "请检查网络、VPN，或联系 IT 开放该域名后重试。"
        )
    if "Concurrent task limit" in message:
        return (
            "Lovart 当前已有任务在生成，请等待上一张完成后再试；"
            "若持续失败，请确认只运行了一个 start.sh 服务。"
        )
    return message


def call_lovart(
    mode,
    prompt,
    image_paths=None,
    ratio="1:1",
    poll_timeout=90,
    local_project=None,
    lovart_project_id=None,
    output_width=None,
    output_height=None,
    size_mode="online",
    dpi=None,
    lovart_task_kind=None,
):
    """调用 Lovart OpenAPI 生图；同项目组多 Key 时在并发/额度受限时自动切换。"""
    if not local_project:
        return None, "未指定项目组，无法选择 Lovart Key"
    lovart_credentials = load_lovart_credentials_for_project(local_project)
    if not lovart_credentials:
        slug = require_project_llm_config(local_project).slug
        return None, f"{local_project} 未配置 Lovart Key（LOVART_ACCESS_KEY_{slug}）"

    timeout = max(poll_timeout, LOVART_POLL_TIMEOUT)
    last_error = None
    meta = get_project_meta(local_project) or {}
    project_title = lovart_project_title(local_project, meta)
    cfg = require_project_llm_config(local_project)
    lovart_base = cfg.lovart_base_url

    for cred_index, (access_key, secret_key) in enumerate(lovart_credentials):
        client = LovartClient(
            access_key=access_key,
            secret_key=secret_key,
            base_url=lovart_base,
            timeout=timeout,
        )
        resolved_project_id = lovart_project_id
        if local_project and not resolved_project_id:
            resolved_project_id = ensure_lovart_project(local_project, client) or None

        switch_to_next_key = False

        ratio, gen_w, gen_h = resolve_output_dimensions(ratio, output_width, output_height)
        quality_hint = LOVART_QUALITY_HINT
        if output_width and output_height:
            quality_hint = (
                f"{quality_hint}，目标输出约 {gen_w}×{gen_h} 像素"
            )
        if size_mode == "print" and dpi:
            quality_hint = f"{quality_hint}，用于 {dpi}dpi 印刷物料"

        for attempt in range(LOVART_TASK_RETRY):
            try:
                image_url, error = client.generate_image(
                    prompt=prompt,
                    image_paths=image_paths,
                    ratio=ratio,
                    timeout=timeout,
                    mode=LOVART_MODE,
                    quality_hint=quality_hint,
                    project_id=resolved_project_id,
                    project_title=project_title,
                    task_kind=lovart_task_kind or "generate",
                )
            except LovartError as e:
                image_url, error = None, e.message

            if image_url:
                if cred_index > 0:
                    print(f"[Lovart] 备用 Key {mask_access_key(access_key)} 生成成功")
                return image_url, None

            last_error = _normalize_lovart_error(error)
            if not error:
                break

            if is_lovart_connection_error(error):
                break

            has_backup_key = cred_index + 1 < len(lovart_credentials)
            if is_lovart_limit_error(error) and has_backup_key:
                print(
                    f"[Lovart] Key {mask_access_key(access_key)} 并发或额度受限，"
                    f"切换到备用 Key ({cred_index + 2}/{len(lovart_credentials)})"
                )
                switch_to_next_key = True
                break

            if is_lovart_limit_error(error) and attempt < LOVART_TASK_RETRY - 1:
                wait_seconds = LOVART_TASK_RETRY_WAIT * (attempt + 1)
                print(
                    f"[Lovart] Key {mask_access_key(access_key)} 并发任务已满，"
                    f"{wait_seconds}s 后重试 ({attempt + 1}/{LOVART_TASK_RETRY})"
                )
                time.sleep(wait_seconds)
                continue
            break

        if switch_to_next_key:
            continue
        break

    return None, last_error or "Lovart 生成失败"


def check_lovart_reachable(project: str, timeout: int = 8) -> tuple[bool, str]:
    """快速探测 Lovart API 是否可达（用于生图/智能提取前置检查）。"""
    creds = load_lovart_credentials_for_project(project)
    if not creds:
        slug = require_project_llm_config(project).slug
        return False, f"{project} 未配置 Lovart Key（LOVART_ACCESS_KEY_{slug}）"
    ak, sk = creds[0]
    cfg = require_project_llm_config(project)
    client = LovartClient(
        access_key=ak,
        secret_key=sk,
        base_url=cfg.lovart_base_url,
        timeout=timeout,
    )
    try:
        client.set_mode(unlimited=False)
        return True, ""
    except LovartError as e:
        return False, _normalize_lovart_error(e.message)
    except Exception as e:
        return False, _normalize_lovart_error(str(e))


# ─── 即梦 CLI 调用 ──────────────────────────────────────────────
def call_dreamina(mode, prompt, image_paths=None, model_version=None, resolution=None, ratio="1:1", poll_timeout=90):
    """调用即梦 CLI"""
    dreamina_cmd = _dreamina_command_path()
    if not dreamina_cmd:
        return None, f"未找到即梦 CLI: {DREAMINA_BIN}，请安装 dreamina 或设置 DREAMINA_BIN"

    if mode == "text2img":
        cmd = [dreamina_cmd, "text2image", "--prompt", prompt, "--ratio", ratio, "--poll", str(poll_timeout)]
        if model_version:
            cmd += ["--model_version", model_version]
    else:
        cmd = [dreamina_cmd, "image2image"]
        if isinstance(image_paths, list):
            cmd += ["--images", ",".join(str(p) for p in image_paths)]
        else:
            cmd += ["--images", str(image_paths)]
        cmd += ["--prompt", prompt, "--ratio", ratio, "--poll", str(poll_timeout)]
        if model_version:
            cmd += ["--model_version", model_version]
        if resolution:
            cmd += ["--resolution_type", resolution]
    
    print(f"[CMD] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print(f"[STDOUT] {result.stdout[:500]}")
    if result.stderr:
        print(f"[STDERR] {result.stderr[:300]}")
    
    if result.returncode != 0:
        return None, result.stderr or "即梦生成失败"
    
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        for line in result.stdout.strip().split('\n'):
            if line.strip().startswith('{'):
                try:
                    data = json.loads(line.strip())
                    break
                except:
                    continue
        else:
            return None, f"无法解析即梦输出: {result.stdout[:200]}"
    
    try:
        images = data.get("result_json", {}).get("images", [])
        if not images:
            return None, "即梦未返回图片"
        return images[0].get("image_url", ""), None
    except Exception as e:
        return None, f"解析失败: {e}"


def _resolve_image_backend():
    backend = IMAGE_BACKEND
    if backend in ("auto", ""):
        return "lovart"
    return backend


def normalize_image_backend(value=None):
    backend = (value or "").strip().lower()
    if backend.startswith("gpt:"):
        return "gpt"
    aliases = {
        "sd": "stable_diffusion",
        "stable-diffusion": "stable_diffusion",
        "stable diffusion": "stable_diffusion",
    }
    backend = aliases.get(backend, backend)
    if backend:
        return backend
    return _resolve_image_backend()


def resolve_gpt_model(image_backend_value=None, fields: dict | None = None) -> str:
    raw = (image_backend_value or "").strip()
    if raw.startswith("gpt:"):
        return resolve_gpt_image_model(model=raw.split(":", 1)[1])
    if fields:
        explicit = str(fields.get("gpt_model", "") or "").strip()
        if explicit:
            return resolve_gpt_image_model(model=explicit)
        tier = str(fields.get("gpt_tier", "") or "").strip()
        if tier:
            return resolve_gpt_image_model(tier=tier)
    return resolve_gpt_image_model()


ONLINE_RATIO_TO_SIZE = {
    "1:1": (1024, 1024),
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


def ratio_to_size(ratio: str):
    return ONLINE_RATIO_TO_SIZE.get(ratio, (1024, 1024))


def _parse_positive_int(value) -> Optional[int]:
    try:
        n = int(str(value).strip())
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_output_dimensions(
    ratio: str,
    output_width=None,
    output_height=None,
) -> tuple[str, int, int]:
    w = _parse_positive_int(output_width)
    h = _parse_positive_int(output_height)
    if w and h:
        return bbox_to_ratio(w, h), w, h
    rw, rh = ratio_to_size(ratio or "1:1")
    return ratio or "1:1", rw, rh


def parse_size_fields(fields: dict) -> dict:
    ratio = str(fields.get("ratio", "1:1") or "1:1")
    ow = fields.get("output_width")
    oh = fields.get("output_height")
    ratio, width, height = resolve_output_dimensions(ratio, ow, oh)
    size_mode = str(fields.get("size_mode", "online") or "online").strip() or "online"
    dpi = _parse_positive_int(fields.get("dpi"))
    return {
        "ratio": ratio,
        "output_width": width,
        "output_height": height,
        "size_mode": size_mode,
        "dpi": dpi,
        "size_label": str(fields.get("size_label", "") or "").strip(),
        "width_mm": fields.get("width_mm"),
        "height_mm": fields.get("height_mm"),
    }


def variant_entry_from_path(output_filename: str, output_path: pathlib.Path) -> dict:
    """生图变体元数据（含实际像素尺寸，供下载命名）。"""
    from PIL import Image

    with Image.open(output_path) as im:
        w, h = im.size
    return {"filename": output_filename, "width": w, "height": h, "error": None}


def call_comfyui(
    mode,
    prompt,
    image_paths=None,
    ratio="1:1",
    poll_timeout=90,
    output_width=None,
    output_height=None,
):
    if mode == "img2img" and image_paths:
        return None, "ComfyUI 当前仅支持文生图，请先去掉参考图或改用 Lovart / Stable Diffusion"
    if not COMFYUI_CHECKPOINT:
        return None, "未配置 COMFYUI_CHECKPOINT（ComfyUI 模型文件名）"

    _, width, height = resolve_output_dimensions(ratio, output_width, output_height)
    client = ComfyUIClient(
        base_url=COMFYUI_API_URL,
        timeout=max(poll_timeout, LOCAL_GENERATION_TIMEOUT),
    )
    try:
        return client.generate_image(prompt, width, height, image_paths=image_paths)
    except ComfyUIClientError as e:
        return None, e.message


def call_stable_diffusion(
    mode,
    prompt,
    image_paths=None,
    ratio="1:1",
    poll_timeout=90,
    output_width=None,
    output_height=None,
):
    _, width, height = resolve_output_dimensions(ratio, output_width, output_height)
    client = StableDiffusionClient(
        base_url=SD_API_URL,
        timeout=max(poll_timeout, LOCAL_GENERATION_TIMEOUT),
    )
    try:
        return client.generate_image(prompt, width, height, image_paths=image_paths if mode == "img2img" else None)
    except SDClientError as e:
        return None, e.message


def call_gpt(
    mode,
    prompt,
    image_paths=None,
    ratio="1:1",
    poll_timeout=90,
    local_project=None,
    output_width=None,
    output_height=None,
    gpt_model=None,
    mask_path=None,
    prefer_responses=False,
):
    if not local_project:
        return None, "GPT 生图请先选择项目组"
    try:
        cfg = require_project_llm_config(local_project)
    except ProjectCredentialsError as e:
        return None, str(e)
    image_cfg = get_gpt_image_settings(cfg)
    if not image_cfg.api_key:
        if image_cfg.provider == "agenthub":
            return None, f"{local_project} 未配置 OPENAI_APP_KEY_{cfg.slug}（AgentHub 生图）"
        return None, f"{local_project} 未配置 OPENAI_API_KEY_{cfg.slug}（官方 OpenAI 生图）"

    if image_cfg.provider == "official":
        key_err = validate_official_gpt_image_key(image_cfg.api_key, cfg.slug, local_project)
        if key_err:
            return None, key_err

    _, width, height = resolve_output_dimensions(ratio, output_width, output_height)
    model = resolve_gpt_image_model(model=gpt_model)
    client = GptImageClient(
        api_key=image_cfg.api_key,
        base_url=image_cfg.base_url,
        timeout=max(poll_timeout, LOCAL_GENERATION_TIMEOUT),
        temp_dir=OUTPUT_DIR,
        fallback_bearer_key=image_cfg.fallback_bearer_key,
        provider=image_cfg.provider,
    )
    try:
        return client.generate_image(
            prompt,
            model=model,
            width=width,
            height=height,
            image_paths=image_paths if mode == "img2img" else None,
            mask_path=pathlib.Path(mask_path) if mask_path else None,
            prefer_responses=bool(prefer_responses),
        )
    except GptImageError as e:
        return None, e.message


def call_image_generator(
    mode,
    prompt,
    image_paths=None,
    model_version=None,
    resolution=None,
    ratio="1:1",
    poll_timeout=90,
    local_project=None,
    lovart_project_id=None,
    image_backend=None,
    output_width=None,
    output_height=None,
    size_mode="online",
    dpi=None,
    gpt_model=None,
    lovart_task_kind=None,
    mask_path=None,
    prefer_responses=False,
):
    backend = normalize_image_backend(image_backend)
    if backend == "gpt":
        return call_gpt(
            mode,
            prompt,
            image_paths=image_paths,
            ratio=ratio,
            poll_timeout=poll_timeout,
            local_project=local_project,
            output_width=output_width,
            output_height=output_height,
            gpt_model=gpt_model,
            mask_path=mask_path,
            prefer_responses=prefer_responses,
        )
    if backend == "lovart":
        return call_lovart(
            mode,
            prompt,
            image_paths=image_paths,
            ratio=ratio,
            poll_timeout=poll_timeout,
            local_project=local_project,
            lovart_project_id=lovart_project_id,
            output_width=output_width,
            output_height=output_height,
            size_mode=size_mode,
            dpi=dpi,
            lovart_task_kind=lovart_task_kind,
        )
    if backend == "comfyui":
        return call_comfyui(
            mode,
            prompt,
            image_paths=image_paths,
            ratio=ratio,
            poll_timeout=poll_timeout,
            output_width=output_width,
            output_height=output_height,
        )
    if backend == "stable_diffusion":
        return call_stable_diffusion(
            mode,
            prompt,
            image_paths=image_paths,
            ratio=ratio,
            poll_timeout=poll_timeout,
            output_width=output_width,
            output_height=output_height,
        )
    return call_dreamina(
        mode,
        prompt,
        image_paths=image_paths,
        model_version=model_version,
        resolution=resolution,
        ratio=ratio,
        poll_timeout=poll_timeout,
    )


def generate_variants(
    prompt,
    image_paths=None,
    count=4,
    mode="text2img",
    ratio="1:1",
    local_project=None,
    image_backend=None,
):
    """生成多张变体"""
    backend = normalize_image_backend(image_backend)
    poll_timeout = LOVART_POLL_TIMEOUT if backend == "lovart" else LOCAL_GENERATION_TIMEOUT

    lovart_project_id = None
    if backend == "lovart" and local_project:
        creds = load_lovart_credentials_for_project(local_project)
        if creds:
            ak, sk = creds[0]
            cfg = require_project_llm_config(local_project)
            client = LovartClient(
                access_key=ak,
                secret_key=sk,
                base_url=cfg.lovart_base_url,
                timeout=poll_timeout,
            )
            lovart_project_id = ensure_lovart_project(local_project, client) or None

    def generate_one(idx):
        image_url, error = call_image_generator(
            mode,
            prompt,
            image_paths,
            model_version="4.6",
            ratio=ratio,
            poll_timeout=poll_timeout,
            local_project=local_project,
            lovart_project_id=lovart_project_id,
            image_backend=backend,
        )
        return {"idx": idx, "url": image_url, "error": error}

    if backend == "lovart":
        return [generate_one(i) for i in range(count)]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(count, 4)) as executor:
        futures = [executor.submit(generate_one, i) for i in range(count)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda x: x["idx"])
    return results


def _normalize_reference_upload(path: pathlib.Path) -> pathlib.Path:
    """GIF 动图取首帧转 PNG，便于生图 API 识别。"""
    if path.suffix.lower() != ".gif":
        return path
    from PIL import Image

    png_path = path.with_suffix(".png")
    with Image.open(path) as im:
        im.seek(0)
        im.convert("RGBA").save(png_path, "PNG")
    path.unlink(missing_ok=True)
    return png_path


def _save_ref_images_from_fields(fields: dict) -> list:
    paths = []
    # 落地页头图可达 排版1+原型1+风格3；其它流程仍通常 ≤3
    for i in range(8):
        ref_key = f"ref_image_{i}"
        ref_data = fields.get(ref_key)
        if ref_data and isinstance(ref_data, dict):
            file_ext = pathlib.Path(ref_data.get("filename", ".png")).suffix or ".png"
            ref_filename = f"ref_{uuid.uuid4().hex}{file_ext}"
            ref_path = UPLOAD_DIR / ref_filename
            ref_path.write_bytes(ref_data["data"])
            paths.append(_normalize_reference_upload(ref_path))
    return paths


GENERATION_REF_PROMPT_SUFFIX = (
    "请严格参考上传参考图的画风、角色/IP 造型、配色与构图，保持视觉风格一致，"
    "仅按需求文案替换主题内容，不要换成另一种插画风格。"
)


def _save_logo_from_fields(fields: dict):
    logo_data = fields.get("logo_image")
    if not logo_data or not isinstance(logo_data, dict):
        return None
    file_ext = pathlib.Path(logo_data.get("filename", ".png")).suffix or ".png"
    logo_path = UPLOAD_DIR / f"logo_{uuid.uuid4().hex}{file_ext}"
    logo_path.write_bytes(logo_data["data"])
    return logo_path


def build_generation_payload(fields: dict, kind: str) -> dict:
    """从 multipart 字段构建生图任务 payload。"""
    project = str(fields.get("project", "") or "").strip()
    count = int(str(fields.get("count", "1")).strip() or "1")
    client_id = str(fields.get("client_id", "") or "").strip()
    image_backend_raw = str(fields.get("image_backend", "") or "")
    image_backend = normalize_image_backend(image_backend_raw)
    gpt_model = resolve_gpt_model(image_backend_raw, fields) if image_backend == "gpt" else None
    size_info = parse_size_fields(fields)

    payload = {
        "kind": kind,
        "client_id": client_id,
        "project": project,
        "count": count,
        "ratio": size_info["ratio"],
        "output_width": size_info["output_width"],
        "output_height": size_info["output_height"],
        "size_mode": size_info["size_mode"],
        "dpi": size_info["dpi"],
        "size_label": size_info["size_label"],
        "width_mm": size_info["width_mm"],
        "height_mm": size_info["height_mm"],
        "image_backend": image_backend,
        "image_backend_raw": image_backend_raw,
        "gpt_model": gpt_model,
    }

    if kind == "with_prompt":
        payload["prompt"] = str(fields.get("prompt", "") or "").strip()
    else:
        summary_json = fields.get("summary", "{}")
        try:
            summary = json.loads(summary_json) if isinstance(summary_json, str) else summary_json
        except json.JSONDecodeError:
            summary = {}
        project_meta = get_project_meta(project) if project else None
        payload["summary"] = summary
        payload["prompt"] = expand_prompt_from_summary(summary, project_meta)

    image_paths = []
    input_filename = None
    uploaded_file = fields.get("file")
    if uploaded_file and isinstance(uploaded_file, dict):
        file_ext = pathlib.Path(uploaded_file.get("filename", ".png")).suffix or ".png"
        input_filename = f"input_{uuid.uuid4().hex}{file_ext}"
        input_path = UPLOAD_DIR / input_filename
        input_path.write_bytes(uploaded_file["data"])
        image_paths.append(str(input_path))

    image_paths.extend(str(p) for p in _build_image_paths_from_selection(fields, project))
    user_ref_paths = _save_ref_images_from_fields(fields)
    image_paths.extend(str(p) for p in user_ref_paths)
    if user_ref_paths:
        base_prompt = payload.get("prompt") or ""
        payload["prompt"] = (
            f"{base_prompt}。{GENERATION_REF_PROMPT_SUFFIX}"
            if base_prompt
            else GENERATION_REF_PROMPT_SUFFIX
        )

    logo_path = _save_logo_from_fields(fields)
    logo_position = normalize_logo_position(str(fields.get("logo_position", "") or ""))
    if logo_path:
        max_refs = 3
        image_paths = image_paths[:max_refs]
        image_paths.append(str(logo_path))
        suffix = build_logo_prompt_suffix(logo_position)
        base_prompt = payload.get("prompt") or ""
        payload["prompt"] = f"{base_prompt}。{suffix}" if base_prompt else suffix

    payload["image_paths"] = image_paths
    payload["logo_path"] = str(logo_path) if logo_path else None
    payload["logo_position"] = logo_position
    payload["input_filename"] = input_filename
    payload["mode"] = "img2img" if image_paths else "text2img"
    return payload


def execute_generation_job(job: dict) -> None:
    """队列 worker 执行生图任务。"""
    payload = job["payload"]
    job_id = job["job_id"]
    started = time.time()
    with lovart_queue._jobs_lock:
        stored = lovart_queue._jobs.get(job_id)
        if stored:
            stored["started_at"] = started

    project = payload.get("project") or None
    lovart_err = lovart_project_required_error(project or "")
    if lovart_err:
        lovart_queue.fail_job(job_id, lovart_err)
        return

    prompt = payload.get("prompt", "")
    count = int(payload.get("count") or 1)
    ratio = payload.get("ratio", "1:1")
    output_width = payload.get("output_width")
    output_height = payload.get("output_height")
    size_mode = payload.get("size_mode", "online")
    dpi = payload.get("dpi")
    mode = payload.get("mode", "text2img")
    image_paths = [pathlib.Path(p) for p in payload.get("image_paths") or []]
    backend = normalize_image_backend(payload.get("image_backend"))
    input_filename = payload.get("input_filename")

    lovart_queue.set_progress(job_id, 0, count)
    variants = []

    for idx in range(count):
        if lovart_queue.check_job_timeout(job_id):
            return
        if time.time() - started > LOVART_JOB_MAX_SECONDS:
            lovart_queue.fail_job(job_id, f"任务超时（超过 {LOVART_JOB_MAX_SECONDS} 秒）")
            return

        image_url, error = call_image_generator(
            mode,
            prompt,
            image_paths if mode == "img2img" else None,
            model_version="4.6",
            ratio=ratio,
            poll_timeout=LOVART_POLL_TIMEOUT if backend == "lovart" else LOCAL_GENERATION_TIMEOUT,
            local_project=project,
            image_backend=backend,
            output_width=output_width,
            output_height=output_height,
            size_mode=size_mode,
            dpi=dpi,
            gpt_model=payload.get("gpt_model"),
        )
        if image_url:
            output_filename = f"variant_{uuid.uuid4().hex}.png"
            output_path = OUTPUT_DIR / output_filename
            try:
                download_image(image_url, output_path)
                logo_file = payload.get("logo_path")
                if logo_file and pathlib.Path(logo_file).is_file():
                    try:
                        apply_logo_overlay(
                            output_path,
                            logo_file,
                            payload.get("logo_position") or "top_left",
                        )
                    except Exception as logo_err:
                        print(f"[LOGO] overlay failed: {logo_err}")
                # GPT 已按用户线上尺寸生成：保留原图，不做二次裁切/缩放
                if backend != "gpt":
                    finalize_generation_output(output_path, output_width, output_height)
                else:
                    try:
                        from PIL import Image
                        with Image.open(output_path) as im:
                            print(
                                f"[GPT] keep native output {im.size[0]}x{im.size[1]} "
                                f"(requested {output_width}x{output_height})"
                            )
                    except Exception:
                        pass
                variants.append(variant_entry_from_path(output_filename, output_path))
            except Exception as e:
                variants.append({"filename": None, "error": format_url_error(e)})
        else:
            variants.append({"filename": None, "error": error or "生成失败"})
        lovart_queue.set_progress(job_id, idx + 1, count)

    size_notice = None
    if backend == "gpt":
        req_w = _parse_positive_int(output_width)
        req_h = _parse_positive_int(output_height)
        first_ok = next((v for v in variants if v.get("filename") and v.get("width") and v.get("height")), None)
        if req_w and req_h and first_ok:
            aw, ah = int(first_ok["width"]), int(first_ok["height"])
            if aw != req_w or ah != req_h:
                size_notice = {
                    "requested_width": req_w,
                    "requested_height": req_h,
                    "actual_width": aw,
                    "actual_height": ah,
                }

    lovart_queue.set_variants(job_id, variants)
    with lovart_queue._jobs_lock:
        stored = lovart_queue._jobs.get(job_id)
        if stored and stored.get("status") == "running":
            stored["status"] = "done"
            stored["finished_at"] = time.time()
            stored["size_notice"] = size_notice

    if variants and variants[0].get("filename"):
        output_images = [v["filename"] for v in variants if v.get("filename")]
        summary = payload.get("summary") or {}
        entry = build_history_entry(
            mode=mode,
            prompt=prompt,
            description="",
            source="job",
            project=project or "",
            input_image=input_filename,
            output_images=output_images,
            variants_count=len(output_images),
            main_title=_main_title_from_summary(summary),
        )
        add_history(entry)


def format_url_error(exc: Exception, context: str = "") -> str:
    """将 urllib 底层错误转成可操作的提示。"""
    msg = str(getattr(exc, "reason", None) or exc)
    if "Connection refused" in msg or "Errno 61" in msg:
        prefix = f"{context}：" if context else ""
        return (
            f"{prefix}连接被拒绝（目标地址无服务响应）。"
            "请确认 ./start.sh 已启动、.env 中 LOVART_BASE_URL 正确，且网络可访问 Lovart。"
        )
    if context:
        return f"{context}：{msg}"
    return msg


def download_image(url, save_path):
    if url.startswith("file://"):
        shutil.copy2(url[7:], save_path)
        print(f"[DL] Copied local file -> {save_path}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=120) as resp:
            data = resp.read()
            with open(save_path, 'wb') as f:
                f.write(data)
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        raise RuntimeError(format_url_error(e, f"下载图片失败 ({url[:80]})")) from e
    print(f"[DL] Downloaded {len(data)} bytes -> {save_path}")


# ─── 图片裁剪与合成 ─────────────────────────────────────────────
def crop_image_pil(src_path, dst_path, x, y, w, h):
    """使用 PIL 裁剪图片区域"""
    from PIL import Image

    with Image.open(src_path) as img:
        iw, ih = img.size
        x1 = max(0, min(int(x), iw - 1))
        y1 = max(0, min(int(y), ih - 1))
        x2 = max(x1 + 1, min(int(x) + int(w), iw))
        y2 = max(y1 + 1, min(int(y) + int(h), ih))
        cropped = img.crop((x1, y1, x2, y2))
        cropped.save(dst_path)
    print(f"[CROP-PIL] {x1},{y1} {x2-x1}x{y2-y1} -> {dst_path}")


def composite_image_pil(base_path, overlay_path, output_path, x, y):
    """将 overlay 粘贴到 base 的 (x,y) 位置"""
    from PIL import Image

    base = Image.open(base_path).convert("RGBA")
    overlay = Image.open(overlay_path).convert("RGBA")
    ow, oh = overlay.size
    x = max(0, min(int(x), base.width - 1))
    y = max(0, min(int(y), base.height - 1))
    if x + ow > base.width:
        overlay = overlay.crop((0, 0, base.width - x, oh))
    if y + oh > base.height:
        overlay = overlay.crop((0, 0, overlay.width, base.height - y))
    base.paste(overlay, (x, y), overlay)
    base.convert("RGB").save(output_path)
    print(f"[COMPOSITE-PIL] pasted at ({x},{y}) -> {output_path}")


LOGO_POSITION_LABELS = {
    "top_left": "左上角",
    "top_right": "右上角",
    "bottom_left": "左下角",
    "bottom_right": "右下角",
}


def normalize_logo_position(value: str) -> str:
    raw = (value or "top_left").strip().lower()
    return raw if raw in LOGO_POSITION_LABELS else "top_left"


def build_logo_prompt_suffix(position: str = "top_left") -> str:
    pos_label = LOGO_POSITION_LABELS.get(normalize_logo_position(position), "左上角")
    return (
        f"品牌Logo：最后一张参考附件为官方Logo素材（建议透明底）。"
        f"若画面中已有Logo或品牌标识，请替换为该Logo；"
        f"若画面中没有Logo，则在{pos_label}添加该Logo。"
        f"Logo须清晰完整、比例不变形，不遮挡主标题。"
    )


def apply_logo_overlay(
    image_path,
    logo_path,
    position: str = "top_left",
    *,
    margin_ratio: float = 0.03,
    max_width_ratio: float = 0.18,
):
    """将 Logo 叠加到成图指定角落（透明底 PNG 效果最佳）。"""
    from PIL import Image

    position = normalize_logo_position(position)
    base = Image.open(image_path).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")
    bw, bh = base.size
    max_lw = max(1, int(bw * max_width_ratio))
    lw, lh = logo.size
    scale = min(1.0, max_lw / max(lw, 1))
    new_lw = max(1, int(lw * scale))
    new_lh = max(1, int(lh * scale))
    if logo.size != (new_lw, new_lh):
        logo = logo.resize((new_lw, new_lh), Image.LANCZOS)
    margin = max(4, int(min(bw, bh) * margin_ratio))
    coords = {
        "top_left": (margin, margin),
        "top_right": (bw - new_lw - margin, margin),
        "bottom_left": (margin, bh - new_lh - margin),
        "bottom_right": (bw - new_lw - margin, bh - new_lh - margin),
    }
    x, y = coords[position]
    base.paste(logo, (x, y), logo)
    base.convert("RGB").save(image_path)
    print(f"[LOGO] overlay {position} ({x},{y}) {new_lw}x{new_lh} -> {image_path}")


def crop_image(src_path, dst_path, x, y, w, h):
    try:
        crop_image_pil(src_path, dst_path, x, y, w, h)
        return
    except Exception as e:
        print(f"[CROP] PIL failed: {e}, trying sips")
    cmd = ["sips", "-c", str(h), str(w), "--cropOffset", str(y), str(x),
           str(src_path), "--out", str(dst_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        shutil.copy2(src_path, dst_path)
        print(f"[CROP-FALLBACK] sips failed, copied full image")
    else:
        print(f"[CROP] {w}x{h}+{x}+{y} -> {dst_path}")


def composite_image(base_path, overlay_path, output_path, x, y):
    script = f'''
    const app = Application.currentApplication();
    app.includeStandardAdditions = true;
    
    const base = $.NSImage.alloc.initWithContentsOfFile("{base_path}");
    const overlay = $.NSImage.alloc.initWithContentsOfFile("{overlay_path}");
    
    if (!base || !overlay) {{
        console.log("Failed to load images");
    }}
    
    const baseRep = base.representations.objectAtIndex(0);
    const baseW = baseRep.pixelsWide;
    const baseH = baseRep.pixelsHigh;
    
    const newImage = $.NSImage.alloc.initWithSize($.NSMakeSize(baseW, baseH));
    newImage.lockFocus;
    
    base.drawInRect($.NSMakeRect(0, 0, baseW, baseH));
    
    const overlayRep = overlay.representations.objectAtIndex(0);
    const overlayW = overlayRep.pixelsWide;
    const overlayH = overlayRep.pixelsHigh;
    overlay.drawInRect($.NSMakeRect({x}, {y}, overlayW, overlayH));
    
    const tiffData = newImage.TIFFRepresentation;
    const bitmap = $.NSBitmapImageRep.alloc.initWithData(tiffData);
    const pngData = bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
    
    pngData.writeToFile("{output_path}", true);
    newImage.unlockFocus;
    '''
    
    script_path = pathlib.Path("/tmp/_composite_" + uuid.uuid4().hex + ".js")
    script_path.write_text(script, 'utf-8')
    
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(script_path)],
        capture_output=True, text=True, timeout=30
    )
    
    try:
        script_path.unlink()
    except:
        pass
    
    if result.returncode != 0 or not pathlib.Path(output_path).exists():
        try:
            composite_image_pil(base_path, overlay_path, output_path, x, y)
        except Exception as e:
            shutil.copy2(overlay_path, output_path)
            print(f"[COMPOSITE-FALLBACK] JXA/PIL failed ({e}), using overlay only")
    else:
        print(f"[COMPOSITE] Pasted overlay at ({x},{y}) -> {output_path}")


def _save_base64_image(data_url: str, prefix: str = "edit") -> pathlib.Path | None:
    """将 base64 data URL 保存为本地图片文件。"""
    import base64

    if not data_url or not isinstance(data_url, str):
        return None
    try:
        if "," in data_url:
            _, data_part = data_url.split(",", 1)
            raw = base64.b64decode(data_part)
        else:
            raw = base64.b64decode(data_url)
    except Exception:
        return None
    ref_path = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:10]}.png"
    ref_path.write_bytes(raw)
    return ref_path


def _save_edit_reference_images_from_payload(data: dict, max_count: int = 3) -> list[pathlib.Path]:
    """解析修图请求中的参考图（base64 data URL），保存到 uploads/。"""
    import base64

    refs = data.get("reference_images") or data.get("referenceImages") or []
    if isinstance(refs, str):
        refs = [refs] if refs else []
    paths: list[pathlib.Path] = []
    for i, ref in enumerate(refs[:max_count]):
        if not ref or not isinstance(ref, str):
            continue
        try:
            if "," in ref:
                _, data_part = ref.split(",", 1)
                raw = base64.b64decode(data_part)
            else:
                raw = base64.b64decode(ref)
        except Exception:
            continue
        ref_path = UPLOAD_DIR / f"edit_ref_{uuid.uuid4().hex[:10]}.png"
        ref_path.write_bytes(raw)
        paths.append(ref_path)
    return paths


def build_edit_prompt(
    description,
    edit_type="",
    keep_elements="",
    region_only=False,
    has_reference=False,
):
    prompt = description or ""
    if has_reference:
        prompt = f"{prompt} 请参考上传的参考图风格、配色与构图进行修改。".strip()
    if region_only:
        prompt = (
            f"{prompt}。"
            "【局部修改】只改选区内的指定内容；严格保持与原图相同的画风、配色、光影、质感、文字字号与排版；"
            "不要整体重绘，不要添加白色背景、遮罩、描边或留白边；"
            "输出必须铺满整张图并与输入图尺寸、比例完全一致，选区外内容不得出现。"
        )
    if keep_elements:
        prompt = f"{prompt} 保留: {keep_elements}"
    if edit_type:
        prompt = f"[{edit_type}] {prompt}"
    return prompt


def bbox_to_ratio(w, h):
    import math

    w, h = max(int(w), 1), max(int(h), 1)
    g = math.gcd(w, h)
    return f"{w // g}:{h // g}"


def resize_image_file(path, target_w, target_h):
    from PIL import Image

    with Image.open(path) as img:
        if img.size == (target_w, target_h):
            return
        img.resize((target_w, target_h), Image.LANCZOS).save(path)


def fit_image_letterbox(path, target_w, target_h):
    """等比缩放进目标画布，不足处留边，不裁切画面内容。"""
    from PIL import Image

    target_w, target_h = int(target_w), int(target_h)
    with Image.open(path) as img:
        if img.size == (target_w, target_h):
            return
        src = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
        sw, sh = src.size
        scale = min(target_w / max(sw, 1), target_h / max(sh, 1))
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        resized = src.resize((nw, nh), Image.LANCZOS)
        if src.mode == "RGBA":
            canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            canvas.paste(resized, ((target_w - nw) // 2, (target_h - nh) // 2), resized)
        else:
            canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            canvas.paste(resized, ((target_w - nw) // 2, (target_h - nh) // 2))
        canvas.save(path)


def resize_image_cover(path, target_w, target_h):
    """等比放大后居中裁切，避免拉伸导致文字变形。"""
    from PIL import Image

    target_w, target_h = int(target_w), int(target_h)
    with Image.open(path) as img:
        sw, sh = img.size
        if sw == target_w and sh == target_h:
            return
        scale = max(target_w / max(sw, 1), target_h / max(sh, 1))
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        resized = img.resize((nw, nh), Image.LANCZOS)
        left = max(0, (nw - target_w) // 2)
        top = max(0, (nh - target_h) // 2)
        resized.crop((left, top, left + target_w, top + target_h)).save(path)


def finalize_generation_output(
    output_path: pathlib.Path,
    output_width=None,
    output_height=None,
) -> None:
    """将生图结果适配到目标像素：同比例直接缩放，异比例留边适配，避免裁切或压扁。"""
    import math
    from PIL import Image

    w = _parse_positive_int(output_width)
    h = _parse_positive_int(output_height)
    if not w or not h:
        return
    with Image.open(output_path) as img:
        sw, sh = img.size
        if (sw, sh) == (w, h):
            return
        source_ratio = sw / max(sh, 1)
        target_ratio = w / max(h, 1)
        if abs(math.log(source_ratio / target_ratio)) <= 0.02:
            resize_image_file(output_path, w, h)
        else:
            fit_image_letterbox(output_path, w, h)


def generate_splash_subframe_image(
    input_path: pathlib.Path,
    target_w: int,
    target_h: int,
    local_project: str,
    *,
    remark: str | None = None,
) -> "Image.Image":
    """拓展子画面：与普通生图相同的 GPT img2img（edits、无蒙版），后台注入内置提示词。"""
    from PIL import Image

    prompt = splash_subframe_extend_prompt(target_w, target_h, remark=remark)
    ratio = bbox_to_ratio(target_w, target_h)
    image_url, error = call_image_generator(
        "img2img",
        prompt,
        image_paths=[input_path],
        ratio=ratio,
        poll_timeout=LOCAL_GENERATION_TIMEOUT,
        local_project=local_project,
        image_backend="gpt",
        gpt_model=resolve_gpt_image_model(model="gpt-image-2"),
        output_width=target_w,
        output_height=target_h,
        mask_path=None,
    )
    if not image_url:
        raise ValueError(error or "GPT 拓展子画面生成失败，请稍后重试")
    raw = UPLOAD_DIR / f"splash_subframe_{uuid.uuid4().hex[:10]}.png"
    try:
        download_image(image_url, raw)
        finalize_generation_output(raw, target_w, target_h)
        with Image.open(raw) as im:
            return im.convert("RGBA")
    finally:
        try:
            raw.unlink()
        except OSError:
            pass


def compute_context_crop(x, y, w, h, img_w, img_h, margin_ratio=0.12):
    """扩大选区裁剪范围，让 img2img 看到周边背景，减少白边与遮罩感。"""
    mx = max(4, int(w * margin_ratio))
    my = max(4, int(h * margin_ratio))
    cx = max(0, int(x) - mx)
    cy = max(0, int(y) - my)
    cx2 = min(int(img_w), int(x) + int(w) + mx)
    cy2 = min(int(img_h), int(y) + int(h) + my)
    cw, ch = cx2 - cx, cy2 - cy
    return cx, cy, cw, ch, int(x) - cx, int(y) - cy, int(w), int(h)


def ensure_image_dimensions(path, target_w, target_h):
    from PIL import Image

    with Image.open(path) as img:
        if img.size == (target_w, target_h):
            return
        img.resize((target_w, target_h), Image.LANCZOS).save(path)


def _rounded_rect_mask(size, radius):
    from PIL import Image, ImageDraw

    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    r = min(int(radius), w // 2, h // 2)
    if r > 0:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    else:
        draw.rectangle((0, 0, w - 1, h - 1), fill=255)
    return mask


def prepare_replacement_overlay(replacement_path, target_w, target_h, corner_radius=None):
    """将替换图等比缩放居中到选区，并套用圆角蒙版。"""
    from PIL import Image

    target_w, target_h = int(target_w), int(target_h)
    with Image.open(replacement_path) as repl:
        repl = repl.convert("RGBA")
    sw, sh = repl.size
    scale = min(target_w / max(sw, 1), target_h / max(sh, 1))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    fitted = repl.resize((nw, nh), Image.LANCZOS)
    overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    ox = (target_w - nw) // 2
    oy = (target_h - nh) // 2
    overlay.paste(fitted, (ox, oy), fitted)
    if corner_radius is None:
        corner_radius = max(4, int(min(target_w, target_h) * 0.06))
    corner_mask = _rounded_rect_mask((target_w, target_h), corner_radius)
    alpha = overlay.split()[3]
    overlay.putalpha(Image.composite(alpha, Image.new("L", (target_w, target_h), 0), corner_mask))
    return overlay


def composite_replacement_region(
    base_path, replacement_path, output_path, x, y, w, h, corner_radius=None
):
    """仅替换选区内画面：等比贴合、居中、圆角，选区外原图不变。"""
    from PIL import Image

    with Image.open(base_path) as orig:
        orig_mode = orig.mode
        base = orig.convert("RGBA")
    overlay = prepare_replacement_overlay(replacement_path, w, h, corner_radius)
    x1 = max(0, int(x))
    y1 = max(0, int(y))
    base.paste(overlay, (x1, y1), overlay)
    if orig_mode == "RGB":
        base.convert("RGB").save(output_path)
    else:
        base.save(output_path)
    print(f"[REPLACE] fitted region ({x1},{y1}) {int(w)}x{int(h)} -> {output_path}")


def composite_region_paste(base_path, overlay_path, output_path, x, y, w, h):
    """将修图结果粘贴回选区；支持带透明通道的替换图。"""
    from PIL import Image

    with Image.open(base_path) as orig:
        orig_mode = orig.mode
        base = orig.convert("RGBA")
    overlay = Image.open(overlay_path).convert("RGBA")

    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = min(base.width, x1 + int(w))
    y2 = min(base.height, y1 + int(h))
    actual_w, actual_h = x2 - x1, y2 - y1
    if actual_w <= 0 or actual_h <= 0:
        base.save(output_path)
        return

    if overlay.size != (actual_w, actual_h):
        overlay = overlay.resize((actual_w, actual_h), Image.LANCZOS)
    base.paste(overlay, (x1, y1), overlay)
    if orig_mode == "RGB":
        base.convert("RGB").save(output_path)
    else:
        base.save(output_path)
    print(f"[PASTE] region ({x1},{y1}) {actual_w}x{actual_h} -> {output_path}")


def call_img2img_with_retry(
    input_path,
    prompt,
    ratio="1:1",
    max_retries=3,
    local_project=None,
    image_backend=None,
    queue_priority=PRIORITY_LOW,
    reference_paths=None,
    output_width=None,
    output_height=None,
    lovart_task_kind=None,
    mask_path=None,
):
    backend = normalize_image_backend(image_backend)
    gpt_model = resolve_gpt_model(image_backend) if backend == "gpt" else None
    ref_list = [str(p) for p in (reference_paths or [])]

    def _once():
        last_error = None
        image_paths = [str(input_path)] + ref_list
        for attempt in range(max_retries):
            try:
                image_url, error = call_image_generator(
                    mode="img2img",
                    prompt=prompt,
                    image_paths=image_paths,
                    ratio=ratio,
                    local_project=local_project,
                    image_backend=backend,
                    gpt_model=gpt_model,
                    output_width=output_width,
                    output_height=output_height,
                    lovart_task_kind=lovart_task_kind,
                    mask_path=mask_path,
                )
                if image_url:
                    return image_url, None
                last_error = error
                print(f"[EDIT] 第{attempt + 1}次尝试失败: {error}")
            except Exception as e:
                last_error = str(e)
                print(f"[EDIT] 第{attempt + 1}次尝试异常: {last_error}")
        return None, last_error

    if backend == "lovart":
        return lovart_queue.run_sync(
            queue_priority,
            _once,
            label="img2img",
        )
    return _once()


def run_ai_extract_subject(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
    prompt: str,
    trim: bool = True,
    local_project: Optional[str] = None,
) -> dict:
    """AI 抠图：以参考图（可选框选）走 GPT Image 2 生图，按用户说明输出。"""
    from PIL import Image

    if not local_project or not gpt_image_available_for_project(local_project):
        raise ValueError("未配置 GPT 生图 Key，无法使用 AI 抠图")

    with Image.open(input_path) as im:
        img_w, img_h = im.size
    x, y, w, h = compute_extract_crop_bbox(img_w, img_h, roi_x, roi_y, roi_w, roi_h)
    crop_path = UPLOAD_DIR / f"ai_extract_crop_{uuid.uuid4().hex[:12]}.png"
    save_extract_crop(input_path, crop_path, x, y, w, h)
    try:
        ai_prompt = build_ai_extract_prompt(prompt)
        ratio = bbox_to_ratio(w, h)
        image_url, error = call_image_generator(
            "img2img",
            ai_prompt,
            image_paths=[crop_path],
            ratio=ratio,
            poll_timeout=LOCAL_GENERATION_TIMEOUT,
            local_project=local_project,
            image_backend="gpt",
            gpt_model=resolve_gpt_image_model(model="gpt-image-2"),
            output_width=w,
            output_height=h,
            mask_path=None,
        )
        if not image_url:
            raise ValueError(error or "GPT Image 2 生成失败，请稍后重试")

        raw_path = UPLOAD_DIR / f"ai_extract_raw_{uuid.uuid4().hex[:12]}.png"
        try:
            download_image(image_url, raw_path)
            finalize_generation_output(raw_path, w, h)
            with Image.open(raw_path) as im:
                out = im.convert("RGBA")
                if trim:
                    bbox = out.getbbox()
                    if bbox:
                        out = out.crop(bbox)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                out.save(output_path, format="PNG", optimize=True)
                out_w, out_h = out.size
        finally:
            try:
                raw_path.unlink()
            except OSError:
                pass
        return {
            "width": out_w,
            "height": out_h,
            "roiX": x,
            "roiY": y,
            "roiWidth": w,
            "roiHeight": h,
            "extractMode": "gpt_image2",
            "imageBackend": "gpt-image-2",
            "usedPrompt": bool((prompt or "").strip()),
        }
    finally:
        try:
            crop_path.unlink()
        except OSError:
            pass


def _write_smart_cutout_job(job_id: str, data: dict) -> None:
    SMART_CUTOUT_JOB_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"jobId": job_id, **data}
    (SMART_CUTOUT_JOB_DIR / f"{job_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_smart_cutout_job(job_id: str) -> Optional[dict]:
    path = SMART_CUTOUT_JOB_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _run_smart_cutout_job(
    job_id: str,
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
    prompt: str,
    trim: bool,
    local_project: Optional[str],
) -> None:
    try:
        _reload_runtime_env()
        meta = run_ai_extract_subject(
            input_path,
            output_path,
            roi_x,
            roi_y,
            roi_w,
            roi_h,
            prompt,
            trim=trim,
            local_project=local_project,
        )
        _write_smart_cutout_job(job_id, {
            "status": "done",
            "ok": True,
            "output_file": output_path.name,
            "download_url": f"/outputs/{output_path.name}",
            "fileSize": output_path.stat().st_size,
            **meta,
        })
        try:
            add_history(build_history_entry(
                mode="img2img",
                prompt=f"AI 抠图 · {(prompt or '').strip() or '参考图生成'}",
                description=(prompt or "").strip(),
                source="ai_cutout",
                project=local_project or "",
                output_images=[output_path.name],
                variants_count=1,
                meta_tags=["🪄AI抠图", "GPT Image 2"],
            ))
        except Exception as hist_err:
            print(f"[HISTORY] AI 抠图记录失败: {hist_err}")
    except Exception as e:
        print(f"[SMART-CUTOUT] 任务 {job_id} 失败: {e}")
        import traceback
        traceback.print_exc()
        _write_smart_cutout_job(job_id, {
            "status": "done",
            "ok": False,
            "error": str(e),
        })
    finally:
        try:
            input_path.unlink()
        except OSError:
            pass


def edit_image_regions(
    base_path,
    regions,
    edit_type,
    keep_elements,
    ratio,
    local_project=None,
    reference_paths=None,
    image_backend=None,
):
    """对多个选区依次裁剪、修图、仅将结果融合回选区，保持原图尺寸与选区外样式"""
    from PIL import Image

    with Image.open(base_path) as orig:
        orig_w, orig_h = orig.size

    work_path = OUTPUT_DIR / f"edit_work_{uuid.uuid4().hex}.png"
    shutil.copy2(base_path, work_path)

    for idx, region in enumerate(regions):
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        if w < 8 or h < 8:
            continue

        replacement_data = (
            region.get("replacement_image") or region.get("replacementImage") or ""
        ).strip()
        repl_path = None
        if replacement_data:
            repl_path = _save_base64_image(replacement_data, "edit_replace")
            if not repl_path:
                return None, f"选区 {idx + 1} 替换图解码失败"

        desc = (region.get("description") or "").strip()

        # 上传替换图：本地等比贴合进选区，保留选区外原图与圆角，不走 AI 重绘
        if repl_path:
            merged_path = OUTPUT_DIR / f"edit_merged_{uuid.uuid4().hex}.png"
            composite_replacement_region(work_path, repl_path, merged_path, x, y, w, h)
            shutil.move(merged_path, work_path)
            try:
                repl_path.unlink()
            except OSError:
                pass
            continue

        if not desc:
            continue

        cx, cy, cw, ch, ix, iy, iw, ih = compute_context_crop(
            x, y, w, h, orig_w, orig_h
        )
        crop_path = UPLOAD_DIR / f"edit_crop_{uuid.uuid4().hex}.png"
        crop_image(work_path, crop_path, cx, cy, cw, ch)

        crop_ratio = bbox_to_ratio(cw, ch)
        region_prompt = build_edit_prompt(
            desc,
            edit_type,
            keep_elements,
            region_only=True,
            has_reference=bool(reference_paths),
        )
        image_url, error = call_img2img_with_retry(
            crop_path,
            region_prompt,
            ratio=crop_ratio,
            local_project=local_project,
            reference_paths=reference_paths,
            image_backend=image_backend,
            output_width=cw,
            output_height=ch,
        )
        if not image_url:
            return None, f"选区 {idx + 1} 修图失败: {error or '未知错误'}"

        edited_crop = UPLOAD_DIR / f"edit_crop_out_{uuid.uuid4().hex}.png"
        download_image(image_url, edited_crop)
        resize_image_cover(edited_crop, cw, ch)

        paste_path = edited_crop
        inner_crop = None
        if ix or iy or iw != cw or ih != ch:
            inner_crop = UPLOAD_DIR / f"edit_inner_{uuid.uuid4().hex}.png"
            with Image.open(edited_crop) as expanded:
                expanded.crop((ix, iy, ix + iw, iy + ih)).save(inner_crop)
            paste_path = inner_crop

        merged_path = OUTPUT_DIR / f"edit_merged_{uuid.uuid4().hex}.png"
        composite_region_paste(work_path, paste_path, merged_path, x, y, iw, ih)
        shutil.move(merged_path, work_path)

        for tmp in (crop_path, edited_crop, inner_crop):
            if not tmp:
                continue
            try:
                tmp.unlink()
            except OSError:
                pass

    ensure_image_dimensions(work_path, orig_w, orig_h)
    return work_path, None


# ─── 表单解析 ────────────────────────────────────────────────────
def _parse_button_layout(fields: dict) -> tuple[int | None, int | None, int | None, int | None]:
    """从 multipart 字段解析按钮摆位（优先 JSON，兼容分散字段）。"""
    layout_raw = fields.get("button_layout")
    if layout_raw:
        try:
            layout = json.loads(str(layout_raw))
            if isinstance(layout, dict):
                w = _parse_positive_int(layout.get("w") or layout.get("width"))
                h = _parse_positive_int(layout.get("h") or layout.get("height"))
                if w and h:
                    x = int(str(layout.get("x", "0")).strip())
                    y = int(str(layout.get("y", "0")).strip())
                    return x, y, w, h
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    w = _parse_positive_int(fields.get("button_width"))
    h = _parse_positive_int(fields.get("button_height"))
    if not (w and h):
        return None, None, None, None
    try:
        x = int(str(fields.get("button_x", "0")).strip())
        y = int(str(fields.get("button_y", "0")).strip())
    except ValueError:
        return None, None, None, None
    return x, y, w, h


def _parse_layer_layouts(fields: dict) -> dict[str, dict]:
    """解析各动效图层的独立摆位 JSON。"""
    raw = fields.get("layer_layouts")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    layouts: dict[str, dict] = {}
    for key, layout in parsed.items():
        if not isinstance(layout, dict):
            continue
        w = _parse_positive_int(layout.get("w") or layout.get("width"))
        h = _parse_positive_int(layout.get("h") or layout.get("height"))
        if not (w and h):
            continue
        try:
            x = int(str(layout.get("x", "0")).strip())
            y = int(str(layout.get("y", "0")).strip())
        except ValueError:
            continue
        layouts[str(key)] = {"x": x, "y": y, "w": w, "h": h}
    return layouts


def parse_multipart(body, boundary):
    fields = {}
    parts = body.split(b'--' + boundary)
    for part in parts:
        if b'\r\n\r\n' not in part:
            continue
        header_end = part.find(b'\r\n\r\n')
        headers_raw = part[:header_end].decode('utf-8', errors='ignore')
        data = part[header_end + 4:]
        if data.endswith(b'\r\n'):
            data = data[:-2]
        name = filename = None
        for h_line in headers_raw.split('\r\n'):
            if 'name="' in h_line:
                ns = h_line.find('name="') + 6
                ne = h_line.find('"', ns)
                name = h_line[ns:ne]
            if 'filename="' in h_line:
                fs = h_line.find('filename="') + 10
                fe = h_line.find('"', fs)
                filename = h_line[fs:fe]
        if name:
            if filename:
                fields[name] = {'filename': filename, 'data': data}
            else:
                fields[name] = data.decode('utf-8', errors='ignore')
    return fields


# ─── HTML 页面 ────────────────────────────────────────────────────
TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"
HTML_TEMPLATE = TEMPLATE_DIR / "index.html"
STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_html_cache = {"mtime": 0.0, "content": ""}


def _inject_project_gate_flag(html: str) -> str:
    gate_on = is_gate_enabled()
    html = html.replace("__PROJECT_GATE_ENABLED__", "true" if gate_on else "false")
    html = html.replace("__GATE_OVERLAY_EXTRA__", "" if gate_on else " hidden")
    html = html.replace("__APP_MAIN_EXTRA__", " app-locked" if gate_on else "")
    return html


def get_html_page():
    """生产环境缓存模板；DEV_RELOAD=1 时每次请求重新读取（改 UI 无需重启）。"""
    if not HTML_TEMPLATE.exists():
        return "<html><body><h1>缺少 templates/index.html</h1></body></html>"
    dev = os.environ.get("DEV_RELOAD", "").strip().lower() in ("1", "true", "yes")
    if dev:
        return _inject_project_gate_flag(HTML_TEMPLATE.read_text(encoding="utf-8"))
    mtime = HTML_TEMPLATE.stat().st_mtime
    if _html_cache["content"] and _html_cache["mtime"] == mtime:
        return _inject_project_gate_flag(_html_cache["content"])
    content = HTML_TEMPLATE.read_text(encoding="utf-8")
    _html_cache["mtime"] = mtime
    _html_cache["content"] = content
    return _inject_project_gate_flag(content)




# ─── HTTP Handler ────────────────────────────────────────────────
class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):

    def _normalized_path(self):
        path = urllib.parse.urlparse(self.path).path
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')
        return path

    def _query_params(self):
        parsed = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(parsed.query)

    def _product_type_from_query(self):
        params = self._query_params()
        raw = params.get("type", [""])[0]
        return normalize_product_type(raw)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _bearer_token(self) -> str:
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _token_project(self) -> Optional[str]:
        info = resolve_token(self._bearer_token())
        return info["project"] if info else None

    def _auth_any(self) -> bool:
        if not is_gate_enabled():
            return True
        if self._token_project():
            return True
        self._send_json({"error": "未登录或登录已失效"}, status=401)
        return False

    def _resolve_project_for_request(
        self, project_name: str, fields: dict | None = None
    ) -> Optional[str]:
        """解析当前请求的项目组；门禁关闭时允许从 type 参数推断小灯塔/画啦啦。"""
        project_name = (project_name or "").strip()
        if project_name:
            return self._auth_project(project_name)

        if is_gate_enabled():
            return self._auth_project("")

        params = self._query_params()
        query_project = (params.get("project", [""])[0] or "").strip()
        if query_project:
            return self._auth_project(query_project) or query_project

        form_type = ""
        if fields:
            raw_type = fields.get("type", "")
            if isinstance(raw_type, bytes):
                form_type = raw_type.decode("utf-8", errors="ignore").strip()
            else:
                form_type = str(raw_type or "").strip()
        ptype = normalize_product_type(form_type) if form_type else self._product_type_from_query()
        default_name = "小灯塔" if ptype == "xdt" else "画啦啦" if ptype == "hll" else ""
        if default_name:
            return default_name

        self._send_json({"error": "请先在顶部选择项目组（小灯塔 / 画啦啦）"})
        return None

    def _auth_project(self, project_name: str) -> Optional[str]:
        project_name = (project_name or "").strip()
        if not is_gate_enabled():
            if not project_name:
                self._send_json({"error": "请先选择项目组"}, status=400)
                return None
            return project_name
        auth = self._token_project()
        if not auth:
            self._send_json({"error": "未登录或登录已失效"}, status=401)
            return None
        if project_name and project_name != auth:
            self._send_json({"error": "无权访问该项目"}, status=403)
            return None
        return auth

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Client-Id, Authorization')

    def do_OPTIONS(self):
        path = self._normalized_path()
        if path in ('/api/edit-image', '/edit-image', '/parse', '/api/analyze',
                    '/generate-variants', '/generate-with-prompt', '/upscale', '/api/gif-to-svga',
                    '/api/multi-size-export', '/api/crop-image', '/api/magic-cutout',
                    '/api/smart-cutout', '/api/make-breathing-gif', '/api/layout-extend',
                    '/api/generation/jobs', '/api/project-unlock'):
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        path = self._normalized_path()
        if path == '/' or path == '/index.html':
            self._send_html(get_html_page())
        elif path.startswith('/fetch-url'):
            # 抓取网页内容
            parsed_url = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_url.query)
            url = params.get('url', [''])[0]
            self._handle_fetch_url(url)
        elif path.startswith('/static/'):
            self._serve_file(STATIC_DIR, path.split('/')[-1])
        elif path.startswith('/outputs/'):
            self._serve_file(OUTPUT_DIR, path.split('/')[-1])
        elif path.startswith('/projects/') and '/images/' in path:
            parts = path.split('/')
            if len(parts) >= 5:
                project = urllib.parse.unquote(parts[2])
                if self._auth_project(project) is None:
                    return
                filename = urllib.parse.unquote(parts[4].split('?')[0])
                self._serve_file(project_refs_dir(project), filename)
            else:
                self.send_response(404)
                self.end_headers()
        elif path == '/projects':
            if is_gate_enabled():
                auth = self._token_project()
                if not auth:
                    self._send_json({"error": "未登录或登录已失效"}, status=401)
                    return
                all_projects = list_projects()
                one = [p for p in all_projects if p.get("name") == auth]
                self._send_json({"projects": one})
            else:
                self._send_json({"projects": list_projects()})
        elif path.startswith('/projects/') and path.endswith('/images'):
            parts = path.split('/')
            project = urllib.parse.unquote(parts[2])
            if self._auth_project(project) is None:
                return
            params = self._query_params()
            design_type = params.get("design_type", [""])[0]
            self._send_json({
                "images": get_project_images(project, design_type),
                "catalog": detect_project_catalog(project),
            })
        elif path.startswith('/projects/') and '/types/' in path:
            parts = path.split('/')
            if len(parts) >= 6:
                project = urllib.parse.unquote(parts[2])
                if self._auth_project(project) is None:
                    return
                folder = urllib.parse.unquote(parts[4])
                filename = urllib.parse.unquote(parts[5].split('?')[0])
                ref_dir = typed_reference_dir(project, folder)
                if ref_dir:
                    self._serve_file(ref_dir, filename)
                else:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        elif path == '/history':
            if is_gate_enabled():
                auth = self._token_project()
                if not auth:
                    self._send_json({"error": "未登录或登录已失效"}, status=401)
                    return
                items = [
                    i for i in filter_history_items(load_history())
                    if i.get("project") == auth
                ]
            else:
                items = filter_history_items(load_history())
            self._send_json({"items": items})
        elif path == '/api/output-sizes':
            params = self._query_params()
            project = params.get("project", [""])[0]
            if project and self._auth_project(project) is None:
                return
            ptype = self._product_type_from_query()
            if project:
                ptype = project_product_type(project)
            self._send_json({
                "type": ptype,
                "sizes": load_output_sizes(product_type=ptype),
            })
        elif path == '/api/system-info':
            self._handle_system_info()
        elif path.startswith('/api/generation/jobs/'):
            if not self._auth_any():
                return
            job_id = path[len('/api/generation/jobs/'):].strip('/')
            self._handle_generation_job_get(job_id)
        elif path == '/api/generation/jobs':
            if not self._auth_any():
                return
            params = self._query_params()
            client_id = (params.get("client_id", [""])[0] or "").strip()
            if not client_id:
                self._send_json({"error": "缺少 client_id"}, status=400)
                return
            self._send_json({"jobs": lovart_queue.list_jobs(client_id)})
        elif path == '/api/smart-cutout/status':
            if not self._auth_any():
                return
            self._handle_smart_cutout_status()
        elif path == '/api/layout-extend/presets':
            self._send_json({"presets": list_layout_presets()})
        elif path == '/api/design-types':
            params = self._query_params()
            project = urllib.parse.unquote(params.get("project", [""])[0])
            if not project:
                self._send_json({"error": "请指定 project 参数"})
                return
            if self._auth_project(project) is None:
                return
            catalog = detect_project_catalog(project)
            payload = {
                "project": project,
                "catalog": catalog,
                "product_type": project_product_type(project),
                "designTypes": list_design_types_for_project(project),
                "available_models": get_available_models(project),
            }
            if catalog == "folder_types":
                from product_design import folder_type_online_size_presets
                payload["onlineSizePresets"] = folder_type_online_size_presets()
            self._send_json(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self._normalized_path()
        post_routes = {
            '/api/project-unlock': self._handle_project_unlock,
            '/parse': self._handle_parse,
            '/api/analyze': self._handle_analyze,
            '/generate-variants': self._handle_generate_variants,
            '/generate-with-prompt': self._handle_generate_with_prompt,
            '/upscale': self._handle_upscale,
            '/api/edit-image': self._handle_edit_image,
            '/edit-image': self._handle_edit_image,
            '/api/gif-to-svga': self._handle_gif_to_svga,
            '/api/multi-size-export': self._handle_multi_size_export,
            '/api/crop-image': self._handle_crop_image,
            '/api/magic-cutout': self._handle_magic_cutout,
            '/api/smart-cutout': self._handle_smart_cutout,
            '/api/make-breathing-gif': self._handle_make_breathing_gif,
            '/api/layout-extend': self._handle_layout_extend,
            '/api/generation/jobs': self._handle_generation_jobs_post,
        }
        handler = post_routes.get(path)
        if handler:
            handler()
        else:
            print(f"[404] POST {self.path}")
            self.send_response(404)
            self.end_headers()

    def _detect_login_wall(self, text, url):
        """检测是否遇到了登录墙页面"""
        if not text or len(text.strip()) < 200:
            return True
        login_markers = [
            'sign in', 'sign in to', 'login', 'log in', 'sign in with',
            'enterprise account', '扫码登录', '账号登录',
            'request access', 'authorize', 'oauth',
            'challenge.htm', 'login.dingtalk', 'login.feishu',
        ]
        text_lower = text.lower()
        for marker in login_markers:
            if marker in text_lower:
                return True
        return False

    def _fetch_url_with_browser(self, url):
        """用 Playwright 浏览器抓取 JS 渲染页面内容"""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-web-security']
                )
                page = browser.new_page()
                page.set_default_timeout(20000)
                try:
                    page.goto(url, wait_until='domcontentloaded', timeout=15000)
                    page.wait_for_timeout(6000)
                except Exception as goto_err:
                    try:
                        page.goto(url, wait_until='commit', timeout=10000)
                        page.wait_for_timeout(3000)
                    except:
                        browser.close()
                        return None, f"页面加载超时: {str(goto_err)}"
                text = page.evaluate('''() => {
                    const remove = document.querySelectorAll('script,style,nav,header,footer,aside,noscript,iframe');
                    remove.forEach(el => el.remove());
                    const main = document.querySelector('article') ||
                                 document.querySelector('main') ||
                                 document.querySelector('[role="main"]') ||
                                 document.querySelector('.content') ||
                                 document.querySelector('.article-content') ||
                                 document.querySelector('.doc-content') ||
                                 document.querySelector('.document-content') ||
                                 document.querySelector('#content') ||
                                 document.body;
                    return main ? main.innerText : document.body.innerText;
                }''')
                browser.close()
            if self._detect_login_wall(text, url):
                return None, "NEED_LOGIN"
            if text:
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                text = '\n'.join(lines)
                if len(text) > 50000:
                    text = text[:50000]
            return text, None
        except Exception as e:
            return None, f"浏览器抓取失败: {str(e)}"

    def _fetch_url_content(self, url):
        """抓取网页内容：先试 HTTP，内容太少再用浏览器渲染"""
        if not url or not (url.startswith('http://') or url.startswith('https://')):
            return None, "只支持 http/https 网址"

        # 第一步：HTTP 抓取
        text, error = self._fetch_url_http(url)

        # 如果 HTTP 拿到的内容太少（<100字符），可能是 JS 渲染页面，用浏览器重试
        if not error and text and len(text.strip()) >= 100:
            return text, None

        # 第二步：浏览器渲染抓取
        print(f"[fetch] HTTP 内容不足({len(text.strip()) if text else 0}字符)，切换浏览器渲染...")
        browser_text, browser_error = self._fetch_url_with_browser(url)
        if browser_error:
            if browser_error == "NEED_LOGIN":
                return None, "NEED_LOGIN"
            if text and len(text.strip()) > 0:
                return text, None
            return None, browser_error
        return browser_text, None

    def _fetch_url_http(self, url):
        """纯 HTTP 抓取网页内容"""
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            })
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=20) as resp:
                raw = resp.read()
            # 检测编码
            content_type = resp.headers.get('Content-Type', '')
            charset = None
            if 'charset=' in content_type:
                charset = content_type.split('charset=')[-1].split(';')[0].strip().lower()
            # 尝试解码
            html_text = None
            for enc in ([charset] if charset else []) + ['utf-8', 'gbk', 'gb2312', 'gb18030']:
                if not enc:
                    continue
                try:
                    html_text = raw.decode(enc)
                    break
                except:
                    pass
            if html_text is None:
                html_text = raw.decode('utf-8', errors='replace')
            # 用 BeautifulSoup 提取正文
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_text, 'lxml')
                for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript']):
                    tag.decompose()
                main_content = (
                    soup.find('article') or
                    soup.find('main') or
                    soup.find(class_=lambda c: c and any(k in str(c).lower() for k in ['content', 'article', 'body', 'text', 'doc'])) or
                    soup.find(id=lambda i: i and any(k in str(i).lower() for k in ['content', 'article', 'body', 'text', 'doc'])) or
                    soup.find('body') or
                    soup
                )
                text = main_content.get_text(separator='\n', strip=True)
            except ImportError:
                import re
                text = re.sub(r'(?i)<script[^>]*>.*?</script>', '', html_text)
                text = re.sub(r'(?i)<style[^>]*>.*?</style>', '', text)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            text = '\n'.join(lines)
            if len(text) > 50000:
                text = text[:50000]
            return text, None
        except Exception as e:
            return None, f"抓取失败: {str(e)}"

    def _handle_project_unlock(self):
        _reload_runtime_env()
        if not is_gate_enabled():
            self._send_json({"error": "项目组门禁已关闭"}, status=400)
            return
        body = self._read_json_body()
        project = (body.get("project") or "").strip()
        password = body.get("password") or ""
        if project not in ALLOWED_PROJECTS:
            self._send_json({"error": "未知项目组"}, status=400)
            return
        if not password_for(project):
            self._send_json({"error": "该项目组未配置密码"}, status=400)
            return
        token = unlock(project, password)
        if not token:
            self._send_json({"error": "密码错误"}, status=401)
            return
        meta = get_project_meta(project) or {}
        self._send_json({
            "token": token,
            "project": project,
            "display_name": project,
            "catalog": detect_project_catalog(project),
            "product_type": project_product_type(project),
            "credentials_status": credentials_status(project),
            "available_models": get_available_models(project),
        })

    def _handle_fetch_url(self, url):
        """GET /fetch-url?url=... 抓取网页内容"""
        text, error = self._fetch_url_content(url)
        if error:
            self._send_json({"error": error})
        else:
            self._send_json({"text": text, "source": url})

    def _handle_parse(self):
        """解析需求文本（支持直接粘贴网址）"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode('utf-8'))
        except:
            self._send_json({"error": "无效请求"})
            return

        auth_project = self._auth_project((data.get('project') or '').strip())
        if not auth_project:
            return

        text = data.get('text', '').strip()
        project_name = auth_project

        if not text:
            self._send_json({"error": "请输入需求描述或网址"})
            return

        # 如果是网址，先抓取内容
        if text.startswith('http://') or text.startswith('https://'):
            fetched_text, fetch_error = self._fetch_url_content(text)
            if fetch_error == "NEED_LOGIN":
                self._send_json({"error": "此文档需要登录才能访问。请在浏览器中打开文档，手动登录后复制全文内容粘贴到输入框。", "need_login": True})
                return
            if fetch_error:
                self._send_json({"error": fetch_error})
                return
            if not fetched_text or len(fetched_text.strip()) < 20:
                self._send_json({"error": "未能从网址提取到有效内容。该文档可能需要登录或有访问限制，请在浏览器中打开文档 → 全选复制 → 粘贴到输入框。", "need_login": True})
                return
            text = fetched_text

        # 解析需求
        parsed = parse_design_request(text)
        summary = generate_design_summary(parsed)

        # 获取项目元数据
        project_meta = get_project_meta(project_name) if project_name else None

        self._send_json({
            "parsed": parsed,
            "summary": summary,
            "project_meta": project_meta
        })

    def _handle_analyze(self):
        """分析需求，返回关键词（调用 DeepSeek AI 分析）"""
        _reload_runtime_env()
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' in content_type:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            boundary = content_type.split('boundary=')[-1].encode()
            fields = parse_multipart(body, boundary)
        else:
            fields = {}

        summary_json = fields.get('summary', '{}')
        auth_project = self._auth_project(str(fields.get('project', '') or '').strip())
        if not auth_project:
            return

        try:
            summary = json.loads(summary_json)
        except:
            summary = {}

        project_meta = get_project_meta(auth_project)
        regenerate = str(fields.get("regenerate", "0")).strip().lower() in ("1", "true", "yes")
        analyze_model = str(fields.get("analyze_model", "") or "").strip()
        if not analyze_model_allowed(auth_project, analyze_model):
            self._send_json({"error": "当前项目组未配置该润色模型"}, status=400)
            return

        try:
            ai_prompt, source, provider, model, warning = analyze_prompt_from_summary(
                summary,
                project_meta,
                regenerate=regenerate,
                project=auth_project,
                analyze_model=analyze_model,
            )
        except ProjectCredentialsError as e:
            self._send_json({"error": str(e)}, status=503)
            return

        payload = {
            "prompt": ai_prompt,
            "source": source,
            "provider": provider,
        }
        if model:
            payload["model"] = model
        if warning:
            payload["warning"] = warning
        self._send_json(payload)

    def _handle_generation_jobs_post(self):
        """异步生图：创建队列任务。"""
        try:
            _reload_runtime_env()
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传"}, status=400)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            auth_project = self._auth_project(str(fields.get("project", "") or "").strip())
            if not auth_project:
                return
            kind = str(fields.get("kind", "variants") or "variants").strip()
            client_id = str(fields.get("client_id", "") or "").strip()
            if not client_id:
                hdr = self.headers.get("X-Client-Id", "").strip()
                client_id = hdr
            if not client_id:
                self._send_json({"error": "缺少 client_id"}, status=400)
                return

            image_backend_raw = str(fields.get("image_backend", "") or "").strip() or "lovart"
            if not image_backend_allowed(auth_project, image_backend_raw):
                models = get_available_models(auth_project)
                if not models["image_backends"]:
                    self._send_json(
                        {"error": f"{auth_project} 未配置任何生图模型（Lovart 或 GPT Key）"},
                        status=400,
                    )
                    return
                self._send_json({"error": "当前项目组不可用该生图模型"}, status=400)
                return

            payload = build_generation_payload(fields, kind)
            payload["project"] = auth_project
            payload["client_id"] = client_id

            # GPT / Lovart 统一走异步队列：GPT Image 2 单张常需 2–3 分钟，
            # 同步阻塞 HTTP 易超时，导致前端收不到图。
            job_id = lovart_queue.submit_generation(payload, execute_generation_job)
            view = lovart_queue.get_job(job_id)
            self._send_json(
                {
                    "ok": True,
                    "job_id": job_id,
                    "status": view.get("status", "queued") if view else "queued",
                    "position": view.get("position", 0) if view else 0,
                    "status_url": f"/api/generation/jobs/{job_id}",
                },
                status=201,
            )
        except DuplicateHighJobError as e:
            self._send_json(
                {
                    "error": "您已有进行中的生图任务，请等待完成或查看任务状态",
                    "job_id": e.job_id,
                },
                status=409,
            )
        except QueueFullError as e:
            self._send_json({"error": str(e)}, status=503)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)}, status=500)

    def _handle_generation_job_get(self, job_id: str):
        if not job_id:
            self._send_json({"error": "缺少 job_id"}, status=400)
            return
        view = lovart_queue.get_job(job_id)
        if not view:
            self._send_json({"error": "任务不存在或已过期"}, status=404)
            return
        self._send_json(view)

    def _handle_generate_with_prompt(self):
        """使用已有的prompt直接生成图片"""
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' in content_type:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            boundary = content_type.split('boundary=')[-1].encode()
            fields = parse_multipart(body, boundary)
            if normalize_image_backend(fields.get("image_backend")) == "lovart":
                self._send_json(
                    {
                        "error": "Lovart 生图请使用 /api/generation/jobs（前端已切换异步队列）",
                        "migration": "/api/generation/jobs",
                    },
                    status=410,
                )
                return
        else:
            fields = {}

        prompt = fields.get('prompt', '')
        auth_project = self._auth_project(str(fields.get('project', '') or '').strip())
        if not auth_project:
            return
        project = auth_project
        count = int(fields.get('count', '1'))
        ratio = fields.get('ratio', '1:1')
        if not prompt:
            self._send_json({"error": "请提供关键词"})
            return

        product_type = project_product_type(project)
        lovart_err = lovart_project_required_error(project)
        if lovart_err:
            self._send_json({"error": lovart_err})
            return

        image_paths = _build_image_paths_from_selection(fields, project)
        user_ref_paths = _save_ref_images_from_fields(fields)
        image_paths.extend(user_ref_paths)
        if user_ref_paths:
            prompt = f"{prompt}。{GENERATION_REF_PROMPT_SUFFIX}" if prompt else GENERATION_REF_PROMPT_SUFFIX

        mode = "img2img" if image_paths else "text2img"

        try:
            results = generate_variants(
                prompt,
                image_paths if mode == "img2img" else None,
                count,
                mode,
                ratio,
                local_project=project or None,
            )

            variants = []
            for r in results:
                if r.get("url"):
                    output_filename = f"variant_{uuid.uuid4().hex}.png"
                    output_path = OUTPUT_DIR / output_filename
                    try:
                        download_image(r["url"], output_path)
                        variants.append({"filename": output_filename, "error": None})
                    except Exception as e:
                        variants.append({"filename": None, "error": format_url_error(e)})
                else:
                    variants.append({"filename": None, "error": r.get("error", "生成失败")})

            # 保存历史
            if variants and variants[0].get("filename"):
                # 保存所有生成的图片到 output_images
                output_images = [v["filename"] for v in variants if v.get("filename")]
                entry = build_history_entry(
                    mode=mode,
                    prompt=prompt,
                    description="",
                    source="generate",
                    project=project,
                    output_images=output_images,
                    variants_count=len(output_images),
                )
                add_history(entry)

            self._send_json({"variants": variants, "prompt": prompt})

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": format_url_error(e, "生成失败")})

    def _handle_generate_variants(self):
        """生成多张变体"""
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' in content_type:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            boundary = content_type.split('boundary=')[-1].encode()
            fields = parse_multipart(body, boundary)
            if normalize_image_backend(fields.get("image_backend")) == "lovart":
                self._send_json(
                    {
                        "error": "Lovart 生图请使用 /api/generation/jobs",
                        "migration": "/api/generation/jobs",
                    },
                    status=410,
                )
                return
        else:
            fields = {}

        summary_json = fields.get('summary', '{}')
        auth_project = self._auth_project(str(fields.get('project', '') or '').strip())
        if not auth_project:
            return
        project = auth_project
        count = int(fields.get('count', '1'))
        ratio = fields.get('ratio', '1:1')
        uploaded_file = fields.get('file')

        try:
            summary = json.loads(summary_json)
        except:
            summary = {}

        project_meta = get_project_meta(project)

        product_type = project_product_type(project)
        lovart_err = lovart_project_required_error(project)
        if lovart_err:
            self._send_json({"error": lovart_err})
            return

        # 构建 prompt
        prompt = expand_prompt_from_summary(summary, project_meta)

        # 保存上传的图片
        input_filename = None
        image_paths = []

        if uploaded_file and isinstance(uploaded_file, dict):
            file_ext = pathlib.Path(uploaded_file['filename']).suffix or '.png'
            input_filename = f"input_{uuid.uuid4().hex}{file_ext}"
            input_path = UPLOAD_DIR / input_filename
            input_path.write_bytes(uploaded_file['data'])
            image_paths.append(input_path)

        image_paths.extend(_build_image_paths_from_selection(fields, project))
        user_ref_paths = _save_ref_images_from_fields(fields)
        image_paths.extend(user_ref_paths)
        if user_ref_paths:
            prompt = f"{prompt}。{GENERATION_REF_PROMPT_SUFFIX}"

        mode = "img2img" if image_paths else "text2img"

        try:
            # 生成变体
            results = generate_variants(
                prompt,
                image_paths if mode == "img2img" else None,
                count,
                mode,
                ratio,
                local_project=project or None,
            )

            variants = []
            for r in results:
                if r.get("url"):
                    output_filename = f"variant_{uuid.uuid4().hex}.png"
                    output_path = OUTPUT_DIR / output_filename
                    try:
                        download_image(r["url"], output_path)
                        variants.append({"filename": output_filename, "error": None})
                    except Exception as e:
                        variants.append({"filename": None, "error": format_url_error(e)})
                else:
                    variants.append({"filename": None, "error": r.get("error", "生成失败")})

            # 保存历史
            if variants and variants[0].get("filename"):
                # 保存所有生成的图片到 output_images
                output_images = [v["filename"] for v in variants if v.get("filename")]
                entry = build_history_entry(
                    mode=mode,
                    prompt=prompt,
                    description="",
                    source="generate",
                    project=project,
                    input_image=input_filename,
                    output_images=output_images,
                    variants_count=len(output_images),
                    main_title=_main_title_from_summary(summary),
                )
                add_history(entry)

            self._send_json({"variants": variants, "prompt": prompt})

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": format_url_error(e, "生成失败")})

    def _handle_upscale(self):
        """图片放大（暂仅 Lovart，2K 放大功能未开放）"""
        self._send_json({"error": "2K 放大功能暂未开放（当前仅支持 Lovart 生图/修图）"})

    def _serve_file(self, directory, filename):
        filepath = directory / filename.split('?')[0]
        if filepath.exists():
            self.send_response(200)
            ext = filepath.suffix.lower()
            mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'gif': 'image/gif', 'webp': 'image/webp', 'svga': 'application/octet-stream',
                    'js': 'application/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8',
                    'html': 'text/html; charset=utf-8', 'htm': 'text/html; charset=utf-8'}.get(
                ext.lstrip('.'), 'application/octet-stream')
            self.send_header('Content-type', mime)
            self.send_header('Content-Length', str(filepath.stat().st_size))
            self.end_headers()
            self.wfile.write(filepath.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_make_breathing_gif(self):
        """底图（静图或 GIF 动图）+ 动效图层 → 循环 GIF（呼吸 / 浮动 / 摇摆 / 旋转，可组合）。"""
        try:
            if not self._auth_any():
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            bg_field = fields.get("background")
            if not bg_field or not isinstance(bg_field, dict):
                self._send_json({"error": "请上传底图（静图或 GIF 动图）"})
                return

            layer_field_names = {
                "breathing": ("layer_breathing", "button"),
                "float": ("layer_float",),
                "sway": ("layer_sway",),
                "rotate": ("layer_rotate",),
            }
            layer_specs: list[tuple[str, dict]] = []
            for effect, names in layer_field_names.items():
                field = None
                for name in names:
                    candidate = fields.get(name)
                    if candidate and isinstance(candidate, dict) and candidate.get("data"):
                        field = candidate
                        break
                if field:
                    layer_specs.append((effect, field))

            if not layer_specs:
                self._send_json({"error": "请至少上传一个动效图层（呼吸 / 上下浮动 / 左右摇摆 / 微旋转）"})
                return

            intensity = str(fields.get("intensity", "medium")).strip() or "medium"
            if intensity not in ("weak", "medium", "strong"):
                intensity = "medium"
            try:
                duration_sec = float(str(fields.get("duration_sec", "1.6")).strip())
                offset_x = int(str(fields.get("offset_x", "0")).strip())
                offset_y = int(str(fields.get("offset_y", "0")).strip())
                button_scale = float(str(fields.get("button_scale", "1")).strip())
                button_x, button_y, button_width, button_height = _parse_button_layout(fields)
            except ValueError:
                self._send_json({"error": "参数格式无效"})
                return

            job_id = uuid.uuid4().hex[:12]
            bg_name = str(bg_field.get("filename") or "bg.png")
            bg_ext = pathlib.Path(bg_name).suffix.lower()
            if bg_ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                bg_ext = ".png"
            bg_path = UPLOAD_DIR / f"gif_bg_{job_id}{bg_ext}"
            output_filename = f"breathing_{job_id}.gif"
            output_path = OUTPUT_DIR / output_filename
            bg_path.write_bytes(bg_field["data"])

            layers: list[tuple[pathlib.Path, list[str], dict | None]] = []
            layer_layouts = _parse_layer_layouts(fields)
            for effect, field in layer_specs:
                layer_path = UPLOAD_DIR / f"gif_layer_{effect}_{job_id}.png"
                layer_path.write_bytes(field["data"])
                layers.append((layer_path, [effect], layer_layouts.get(effect)))

            foreground_path: pathlib.Path | None = None
            fg_field = fields.get("layer_foreground") or fields.get("foreground")
            if fg_field and isinstance(fg_field, dict) and fg_field.get("data"):
                foreground_path = UPLOAD_DIR / f"gif_fg_{job_id}.png"
                foreground_path.write_bytes(fg_field["data"])

            fg_layout = layer_layouts.get("foreground")
            meta = make_animated_gif(
                bg_path,
                layers,
                output_path,
                intensity=intensity,
                duration_sec=duration_sec,
                offset_x=offset_x,
                offset_y=offset_y,
                button_scale=button_scale,
                button_x=button_x,
                button_y=button_y,
                button_width=button_width,
                button_height=button_height,
                foreground_path=foreground_path,
                foreground_layout=fg_layout,
            )
            if foreground_path:
                print(
                    f"[GIF-MAKER] 前景层 layout={fg_layout} "
                    f"hasForeground={meta.get('hasForeground')}"
                )
            if button_width and button_height:
                print(
                    f"[GIF-MAKER] 按钮摆位 x={button_x} y={button_y} "
                    f"w={button_width} h={button_height} layer_layouts={list(layer_layouts.keys())} "
                    f"effects={meta.get('effects')}"
                )
            self._send_json({
                "ok": True,
                "output_file": output_filename,
                "download_url": f"/outputs/{output_filename}",
                **meta,
            })
        except Exception as e:
            print(f"[GIF-MAKER] 生成失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_crop_image(self):
        """按用户框选区域裁切为指定宽高"""
        try:
            if not self._auth_any():
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            img_field = fields.get("image")
            if not img_field or not isinstance(img_field, dict):
                self._send_json({"error": "请上传图片"})
                return
            try:
                output_w = int(str(fields.get("output_width", "0")).strip())
                output_h = int(str(fields.get("output_height", "0")).strip())
                crop_x = int(str(fields.get("crop_x", "0")).strip())
                crop_y = int(str(fields.get("crop_y", "0")).strip())
                crop_w = int(str(fields.get("crop_w", "0")).strip())
                crop_h = int(str(fields.get("crop_h", "0")).strip())
            except ValueError:
                self._send_json({"error": "尺寸或裁切参数格式无效"})
                return
            if output_w < 1 or output_h < 1:
                self._send_json({"error": "请填写有效的输出宽度和高度"})
                return
            if crop_w < 1 or crop_h < 1:
                self._send_json({"error": "请先框选裁切区域"})
                return
            job_id = uuid.uuid4().hex[:12]
            ext = pathlib.Path(img_field.get("filename") or "input.png").suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            input_path = UPLOAD_DIR / f"crop_src_{job_id}{ext}"
            output_filename = f"crop_{job_id}{ext if ext != '.webp' else '.png'}"
            output_path = OUTPUT_DIR / output_filename
            input_path.write_bytes(img_field["data"])
            meta = crop_image_to_size(
                input_path, output_path,
                crop_x, crop_y, crop_w, crop_h,
                output_w, output_h,
            )
            self._send_json({
                "ok": True,
                "output_file": output_filename,
                "download_url": f"/outputs/{output_filename}",
                "fileSize": output_path.stat().st_size,
                **meta,
            })
        except Exception as e:
            print(f"[CROP] 裁切失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_magic_cutout(self):
        """魔棒选区抠图：蒙版白色区域变为透明"""
        try:
            if not self._auth_any():
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            img_field = fields.get("image")
            mask_field = fields.get("mask")
            if not img_field or not isinstance(img_field, dict):
                self._send_json({"error": "请上传图片"})
                return
            if not mask_field or not isinstance(mask_field, dict):
                self._send_json({"error": "请提供选区蒙版"})
                return
            job_id = uuid.uuid4().hex[:12]
            ext = pathlib.Path(img_field.get("filename") or "input.png").suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            input_path = UPLOAD_DIR / f"cutout_src_{job_id}{ext}"
            mask_path = UPLOAD_DIR / f"cutout_mask_{job_id}.png"
            output_filename = f"cutout_{job_id}.png"
            output_path = OUTPUT_DIR / output_filename
            input_path.write_bytes(img_field["data"])
            mask_path.write_bytes(mask_field["data"])
            meta = apply_cutout_mask(input_path, mask_path, output_path)
            self._send_json({
                "ok": True,
                "output_file": output_filename,
                "download_url": f"/outputs/{output_filename}",
                "fileSize": output_path.stat().st_size,
                **meta,
            })
        except Exception as e:
            print(f"[CUTOUT] 抠图失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_system_info(self):
        _reload_runtime_env()
        if is_gate_enabled():
            project = self._token_project()
            if not project:
                self._send_json({"error": "未登录或登录已失效"}, status=401)
                return
        else:
            params = self._query_params()
            project = urllib.parse.unquote(params.get("project", [""])[0]).strip()
            if not project:
                self._send_json({"error": "请指定 project 参数"}, status=400)
                return
        ok, msg = check_lovart_reachable(project)
        creds = load_lovart_credentials_for_project(project)
        self._send_json({
            "project": project,
            "lovartReachable": ok,
            "lovartMessage": msg,
            "lovartKeyCount": len(creds),
            "imageBackend": normalize_image_backend(),
            "smartCutoutBackend": preferred_cutout_backend(),
            "smartCutoutHasRembg": has_rembg(),
            "smartCutoutAsync": True,
            "lovartBaseUrl": require_project_llm_config(project).lovart_base_url,
            "credentials_status": credentials_status(project),
            "available_models": get_available_models(project),
            "projectGateEnabled": is_gate_enabled(),
        })

    def _handle_smart_cutout_status(self):
        params = self._query_params()
        job_id = (params.get("job", [""])[0] or "").strip()
        if not job_id:
            self._send_json({"error": "缺少 job 参数"}, status=400)
            return
        job = _read_smart_cutout_job(job_id)
        if not job:
            self._send_json({"error": "任务不存在或已过期"}, status=404)
            return
        if job.get("status") == "running":
            job_path = SMART_CUTOUT_JOB_DIR / f"{job_id}.json"
            if job_path.exists():
                age = time.time() - job_path.stat().st_mtime
                if age > 480:
                    job = {
                        **job,
                        "status": "done",
                        "ok": False,
                        "error": (
                            f"任务超时（已等待 {int(age // 60)} 分钟）。"
                            "GPT Image 2 生成较慢，请稍后重试。"
                        ),
                    }
        self._send_json(job)

    def _handle_smart_cutout(self):
        """AI 抠图：参考图 + 说明，用 GPT Image 2 生图输出透明素材。"""
        try:
            _reload_runtime_env()
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            img_field = fields.get("image")
            if not img_field or not isinstance(img_field, dict):
                self._send_json({"error": "请上传图片"})
                return
            prompt = str(fields.get("prompt", "") or "").strip()
            try:
                roi_x = int(str(fields.get("roi_x", "0")).strip())
                roi_y = int(str(fields.get("roi_y", "0")).strip())
                roi_w = int(str(fields.get("roi_w", "0")).strip())
                roi_h = int(str(fields.get("roi_h", "0")).strip())
            except ValueError:
                self._send_json({"error": "选区参数格式无效"})
                return
            if roi_w < 12 or roi_h < 12:
                self._send_json({"error": "请先在图片上框选要提取的区域（选区太小）"})
                return
            trim = str(fields.get("trim", "1")).strip().lower() not in ("0", "false", "no")
            local_project = self._auth_project(
                str(fields.get("local_project", "") or fields.get("project", "")).strip()
            )
            if not local_project:
                return
            if not gpt_image_available_for_project(local_project):
                self._send_json({"error": "未配置 GPT 生图 Key，无法使用 AI 抠图"})
                return
            job_id = uuid.uuid4().hex[:12]
            ext = pathlib.Path(img_field.get("filename") or "input.png").suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            input_path = UPLOAD_DIR / f"smart_cutout_src_{job_id}{ext}"
            output_filename = f"smart_cutout_{job_id}.png"
            output_path = OUTPUT_DIR / output_filename
            input_path.write_bytes(img_field["data"])
            _write_smart_cutout_job(job_id, {
                "status": "running",
                "message": "GPT Image 2 正在生成，约需 1–3 分钟…",
            })
            thread = threading.Thread(
                target=_run_smart_cutout_job,
                args=(
                    job_id,
                    input_path,
                    output_path,
                    roi_x,
                    roi_y,
                    roi_w,
                    roi_h,
                    prompt,
                    trim,
                    local_project,
                ),
                daemon=True,
            )
            thread.start()
            self._send_json({
                "ok": True,
                "async": True,
                "job_id": job_id,
                "status_url": f"/api/smart-cutout/status?job={job_id}",
            })
        except Exception as e:
            print(f"[SMART-CUTOUT] 智能提取失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_layout_extend(self):
        """规范延展：框选 Logo/IP，按模板合成多尺寸。"""
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            auth_project = self._auth_project(
                str(fields.get("local_project", "") or fields.get("project", "")).strip()
            )
            if not auth_project:
                return
            img_field = fields.get("image")
            if not img_field or not isinstance(img_field, dict):
                self._send_json({"error": "请上传设计图"})
                return
            pack_id = str(fields.get("preset", "") or fields.get("pack_id", "") or "hll-banner-extend").strip()
            try:
                logo_x = int(str(fields.get("logo_x", "0")).strip())
                logo_y = int(str(fields.get("logo_y", "0")).strip())
                logo_w = int(str(fields.get("logo_w", "0")).strip())
                logo_h = int(str(fields.get("logo_h", "0")).strip())
                ip_x = int(str(fields.get("ip_x", "0")).strip())
                ip_y = int(str(fields.get("ip_y", "0")).strip())
                ip_w = int(str(fields.get("ip_w", "0")).strip())
                ip_h = int(str(fields.get("ip_h", "0")).strip())
            except ValueError:
                self._send_json({"error": "Logo/IP 框选参数格式无效"})
                return
            if logo_w < 8 or logo_h < 8 or ip_w < 8 or ip_h < 8:
                self._send_json({"error": "请分别框选 Logo 区域与 IP 主体区域"})
                return
            use_ai = str(fields.get("use_ai", "0")).strip().lower() in ("1", "true", "yes")
            ai_fn = None
            if use_ai:
                _reload_runtime_env()
                local_project = auth_project
                if load_lovart_credentials_for_project(local_project):

                    def ai_background_fn(src_img, meta):
                        tw, th = int(meta["width"]), int(meta["height"])
                        return run_lovart_extend_to_size(
                            src_img,
                            tw,
                            th,
                            prompt=layout_background_extend_prompt(tw, th),
                            ratio=bbox_to_ratio(tw, th),
                            upload_dir=UPLOAD_DIR,
                            img2img=lambda p, pr, r, m=None, aw=0, ah=0, refs=None, _lp=local_project, _tw=tw, _th=th: call_img2img_with_retry(
                                p,
                                pr,
                                ratio=r,
                                max_retries=1,
                                local_project=_lp,
                                lovart_task_kind="outpaint",
                                output_width=aw or _tw,
                                output_height=ah or _th,
                                mask_path=m,
                                reference_paths=[str(x) for x in (refs or [])],
                            ),
                            download_image=download_image,
                        )

                    ai_fn = ai_background_fn
                else:
                    print("[LAYOUT] 未配置 Lovart，AI 阔图回退为本地背景")

            job_id = uuid.uuid4().hex[:12]
            ext = pathlib.Path(img_field.get("filename") or "input.png").suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            input_path = UPLOAD_DIR / f"layout_src_{job_id}{ext}"
            input_path.write_bytes(img_field["data"])
            source_raw = fields.get("source_name", "") or img_field.get("filename", "")
            result = export_layout_extend(
                input_path,
                OUTPUT_DIR,
                job_id,
                pack_id,
                (logo_x, logo_y, logo_w, logo_h),
                (ip_x, ip_y, ip_w, ip_h),
                use_ai_background=bool(ai_fn),
                ai_background_fn=ai_fn,
                source_basename=str(source_raw),
            )
            try:
                input_path.unlink()
            except OSError:
                pass
            self._send_json({"ok": True, **result})
        except Exception as e:
            print(f"[LAYOUT-EXTEND] 规范延展失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_multi_size_export(self):
        """单图按产品版本预设尺寸导出（type=xdt 小灯塔 / type=hll 画啦啦）"""
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            project_name = str(fields.get("project", "")).strip()
            auth_project = self._resolve_project_for_request(project_name, fields)
            if not auth_project:
                return
            project_name = auth_project
            ptype = project_product_type(project_name)
            splash_subframe = str(fields.get("splash_subframe", "0")).strip().lower() in ("1", "true", "yes")
            splash_manual_only = str(fields.get("splash_manual_only", "0")).strip().lower() in ("1", "true", "yes")
            job_id = uuid.uuid4().hex[:12]
            source_raw = str(fields.get("source_name", "") or "").strip() or "开屏"

            if splash_manual_only:
                if normalize_product_type(ptype) != "xdt":
                    self._send_json({"error": "固定尺寸上传仅适用于小灯塔开屏延展"})
                    return
                manual_outputs, manual_sources = export_manual_splash_uploads(
                    fields,
                    UPLOAD_DIR,
                    OUTPUT_DIR,
                    job_id,
                    source_basename=source_raw,
                    product_type=ptype,
                )
                if not manual_outputs:
                    self._send_json({"error": "请至少上传一个固定尺寸图片"})
                    return
                result = build_manual_only_export_result(
                    manual_outputs,
                    OUTPUT_DIR,
                    job_id,
                    source_basename=source_raw,
                    source_outputs=manual_sources,
                )
                self._send_json({"ok": True, "type": ptype, **result})
                return

            img_field = fields.get("image")
            if not img_field or not isinstance(img_field, dict):
                self._send_json({"error": "请上传图片"})
                return
            filename = (img_field.get("filename") or "input.png").lower()
            if not any(filename.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                self._send_json({"error": "仅支持 PNG、JPG、WebP"})
                return
            ext = pathlib.Path(filename).suffix or ".png"
            input_path = UPLOAD_DIR / f"multi_src_{job_id}{ext}"
            input_path.write_bytes(img_field["data"])
            if not source_raw or source_raw == "开屏":
                source_raw = fields.get("source_name", "") or img_field.get("filename", "") or source_raw
            use_ai = str(fields.get("use_ai", "1")).strip().lower() in ("1", "true", "yes")
            use_crop = str(fields.get("use_crop", "0")).strip().lower() in ("1", "true", "yes")
            fit_mode = "crop" if use_crop else "extend"
            ai_canvas_fn = None
            local_project = None
            if use_ai:
                _reload_runtime_env()
                auth_project = self._auth_project(project_name)
                local_project = auth_project
                if local_project and gpt_image_available_for_project(local_project) and not splash_subframe:

                    def ai_canvas_fn(src_img, tw, th, _lp=local_project):
                        return run_gpt_extend_to_size(
                            src_img,
                            tw,
                            th,
                            prompt=splash_extend_prompt(tw, th),
                            ratio=bbox_to_ratio(tw, th),
                            upload_dir=UPLOAD_DIR,
                            img2img=lambda p, pr, r, m=None, aw=0, ah=0, refs=None, _lp=local_project: call_img2img_with_retry(
                                p,
                                pr,
                                ratio=r,
                                max_retries=3,
                                local_project=_lp,
                                image_backend="gpt:gpt-image-2",
                                output_width=aw,
                                output_height=ah,
                                mask_path=m,
                                reference_paths=[str(x) for x in (refs or [])],
                            ),
                            download_image=download_image,
                        )

                    print("[MULTI-SIZE] 开屏扩边使用 GPT Image 2（蒙版 Outpainting）")
                elif use_ai and not splash_subframe:
                    print("[MULTI-SIZE] 未配置 GPT 生图 Key，无法扩边")
                elif splash_subframe and not (local_project and gpt_image_available_for_project(local_project)):
                    print("[MULTI-SIZE] 未配置 GPT 生图 Key，无法拓展子画面")

            sizes_raw = fields.get("sizes", "")
            if isinstance(sizes_raw, bytes):
                sizes_raw = sizes_raw.decode("utf-8", errors="ignore")
            if sizes_raw:
                import json as _json
                requested_sizes = normalize_export_sizes(_json.loads(sizes_raw))
            else:
                self._send_json({"error": "请至少选择一个导出尺寸"})
                return

            if splash_subframe:
                if not (local_project and gpt_image_available_for_project(local_project)):
                    self._send_json({"error": "未配置 GPT 生图 Key（小灯塔 OPENAI_API_KEY_XDT），无法拓展子画面"})
                    return
                subframe_remark = str(
                    fields.get("splash_subframe_remark", "") or ""
                ).strip()
                print("[MULTI-SIZE] 开屏拓展子画面：GPT Image 2 直接生图（内置提示词）")
                result = export_splash_subframe_sizes(
                    input_path,
                    OUTPUT_DIR,
                    job_id,
                    sizes=requested_sizes,
                    source_basename=str(source_raw),
                    generate_at_size=lambda _src, tw, th, _ip=input_path, _lp=local_project, _rm=subframe_remark: generate_splash_subframe_image(
                        _ip, tw, th, _lp, remark=_rm or None
                    ),
                )
                history_tool = str(fields.get("history_tool", "") or "").strip()
                if not history_tool:
                    history_tool = _infer_subframe_history_tool(requested_sizes)
                try:
                    add_subframe_export_history(
                        tool=history_tool,
                        project=project_name,
                        sizes=requested_sizes,
                        images=result.get("images") or [],
                        remark=subframe_remark,
                    )
                except Exception as hist_err:
                    print(f"[HISTORY] 扩边记录失败: {hist_err}")
            else:
                result = export_multi_sizes(
                    input_path,
                    OUTPUT_DIR,
                    job_id,
                    config_path=sizes_config_path(ptype),
                    sizes=requested_sizes,
                    source_basename=str(source_raw),
                    use_ai=bool(ai_canvas_fn),
                    fit_mode=fit_mode,
                    ai_canvas_fn=ai_canvas_fn,
                )
                if not splash_subframe and normalize_product_type(ptype) == "xdt":
                    manual_outputs, _manual_sources = export_manual_splash_uploads(
                        fields,
                        UPLOAD_DIR,
                        OUTPUT_DIR,
                        job_id,
                        source_basename=str(source_raw),
                    )
                    if manual_outputs:
                        result = merge_multi_size_export_results(result, manual_outputs, OUTPUT_DIR)
                        print(f"[MULTI-SIZE] 并入 {len(manual_outputs)} 个手动上传开屏尺寸")
            try:
                input_path.unlink()
            except OSError:
                pass
            self._send_json({"ok": True, "type": ptype, **result})
        except Exception as e:
            print(f"[MULTI-SIZE] 导出失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _resolve_gif_to_svga_input(self, fields: dict) -> tuple[pathlib.Path | None, str | None]:
        """上传 GIF 或引用 outputs 下已生成的 GIF（呼吸动图一键转 SVGA）。"""
        source_raw = fields.get("source_output", "")
        if source_raw:
            safe = pathlib.Path(str(source_raw).strip()).name
            if not safe or safe != str(source_raw).strip() or ".." in safe:
                return None, "无效的文件名"
            if not safe.lower().endswith(".gif"):
                return None, "仅支持 .gif 格式"
            input_path = OUTPUT_DIR / safe
            if not input_path.is_file():
                return None, "找不到已生成的 GIF，请重新生成后再试"
            return input_path, None

        gif_field = fields.get("gif")
        if not gif_field or not isinstance(gif_field, dict):
            return None, "请上传 GIF 文件，或先生成呼吸 GIF"
        filename = (gif_field.get("filename") or "input.gif").lower()
        if not filename.endswith(".gif"):
            return None, "仅支持 .gif 格式"
        job_id = uuid.uuid4().hex[:12]
        input_path = UPLOAD_DIR / f"gif_{job_id}.gif"
        input_path.write_bytes(gif_field["data"])
        return input_path, None

    def _handle_gif_to_svga(self):
        """GIF 转 SVGA"""
        try:
            if not self._auth_any():
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传 GIF 文件"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            input_path, input_err = self._resolve_gif_to_svga_input(fields)
            if input_err:
                self._send_json({"error": input_err})
                return
            fps_raw = fields.get("fps", "")
            fps = None
            if fps_raw:
                try:
                    fps = int(str(fps_raw).strip())
                except ValueError:
                    self._send_json({"error": "帧率格式无效"})
                    return
                if fps not in SVGA_VALID_FPS:
                    self._send_json({
                        "error": f"帧率须为以下之一: {', '.join(map(str, SVGA_VALID_FPS))}",
                    })
                    return
            job_id = uuid.uuid4().hex[:12]
            output_filename = f"svga_{job_id}.svga"
            output_path = OUTPUT_DIR / output_filename
            max_bytes = None
            max_raw = fields.get("max_bytes", "")
            if max_raw:
                try:
                    max_bytes = int(str(max_raw).strip())
                except ValueError:
                    self._send_json({"error": "max_bytes 格式无效"})
                    return
            result = convert_gif_to_svga(input_path, output_path, fps=fps, max_bytes=max_bytes)
            if not result.get("sizePreserved", True):
                self._send_json({
                    "error": (
                        f"输出尺寸 {result.get('width')}×{result.get('height')} "
                        f"与原图 {result.get('originalWidth')}×{result.get('originalHeight')} 不一致"
                    ),
                })
                return
            if not result.get("underLimit", True):
                limit_mb = (result.get("maxBytes") or DEFAULT_MAX_BYTES) / (1024 * 1024)
                self._send_json({
                    "error": (
                        f"在保持原尺寸 {result.get('originalWidth')}×{result.get('originalHeight')} 的前提下，"
                        f"无法将 SVGA 压缩到 {limit_mb:.0f}MB 以内（当前约 {result.get('fileSize', 0) / 1024:.0f}KB）。"
                        "请减少 GIF 帧数或简化画面后重试。"
                    ),
                })
                return
            self._send_json({
                "ok": True,
                "output_file": result["output_filename"],
                "download_url": f"/outputs/{result['output_filename']}",
                "width": result["width"],
                "height": result["height"],
                "originalWidth": result.get("originalWidth"),
                "originalHeight": result.get("originalHeight"),
                "sizePreserved": result.get("sizePreserved", False),
                "totalFrames": result["totalFrames"],
                "fps": result["fps"],
                "version": result.get("version", "2.0.0"),
                "fileSize": result.get("fileSize"),
                "inputFileSize": result.get("inputFileSize"),
                "underLimit": result.get("underLimit", True),
                "framesReduced": result.get("framesReduced", False),
                "originalFrameCount": result.get("originalFrameCount"),
                "frameThinStep": result.get("frameThinStep", 1),
                "frameNote": (
                    f"为控制在 1MB 内，帧数由 {result.get('originalFrameCount')} 减至 {result.get('totalFrames')}"
                    if result.get("framesReduced")
                    else None
                ),
            })
        except Exception as e:
            print(f"[GIF2SVGA] 转换失败: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})


    def _handle_edit_image(self):
        """图片编辑 (image2image)"""
        try:
            self._handle_edit_image_impl()
        except Exception as e:
            print(f"[EDIT] 未捕获异常: {e}")
            import traceback
            traceback.print_exc()
            self._send_json({"error": f"修图失败: {str(e)}"})

    def _handle_edit_image_impl(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode('utf-8'))
        except Exception:
            self._send_json({"error": "无效请求格式"})
            return

        auth_project = self._auth_project((data.get('project') or '').strip())
        if not auth_project:
            return

        image_data = data.get('image', '')
        description = data.get('description', '')
        edit_type = str(data.get('editType', '') or '').strip()
        keep_elements = data.get('keepElements', '')
        local_project = auth_project
        regions = data.get('regions') or []
        reference_paths = _save_edit_reference_images_from_payload(data)
        image_backend_raw = str(data.get("image_backend", "") or "").strip() or "lovart"
        if not image_backend_allowed(local_project, image_backend_raw):
            self._send_json({"error": "当前项目组不可用该修图模型"}, status=400)
            return

        if not image_data:
            self._send_json({"error": "未上传图片"})
            return

        # 保存上传的图片
        import base64
        try:
            if ',' in image_data:
                header, data_part = image_data.split(',', 1)
                image_bytes = base64.b64decode(data_part)
            else:
                image_bytes = base64.b64decode(image_data)
        except:
            self._send_json({"error": "图片解码失败"})
            return

        input_filename = f"edit_input_{uuid.uuid4().hex}.png"
        input_path = UPLOAD_DIR / input_filename
        try:
            input_path.write_bytes(image_bytes)
        except Exception as e:
            self._send_json({"error": f"保存图片失败: {str(e)}"})
            return

        # 获取原图尺寸（客户端可传原始分辨率，避免上传压缩后输出变模糊）
        try:
            from PIL import Image
            with Image.open(input_path) as orig_img:
                orig_w, orig_h = orig_img.size
        except:
            orig_w, orig_h = 1024, 1024  # 默认尺寸

        client_orig_w = _parse_positive_int(data.get("original_width"))
        client_orig_h = _parse_positive_int(data.get("original_height"))
        target_w = client_orig_w if client_orig_w else orig_w
        target_h = client_orig_h if client_orig_h else orig_h

        # 计算原始比例，用于保持原图尺寸
        import math
        g = math.gcd(orig_w, orig_h)
        ratio = f"{orig_w//g}:{orig_h//g}"

        prompt = build_edit_prompt(
            description,
            edit_type,
            keep_elements,
            has_reference=bool(reference_paths),
        )

        output_filename = f"edit_output_{uuid.uuid4().hex}.png"
        output_path = OUTPUT_DIR / output_filename

        if regions:
            work_path, region_error = edit_image_regions(
                input_path,
                regions,
                edit_type,
                keep_elements,
                ratio,
                local_project=local_project,
                reference_paths=reference_paths,
                image_backend=image_backend_raw,
            )
            if region_error:
                self._send_json({"error": region_error})
                return
            shutil.copy2(work_path, output_path)
            try:
                work_path.unlink()
            except OSError:
                pass
        else:
            if not description:
                self._send_json({"error": "请描述修改需求或框选区域"})
                return

            image_url, error = call_img2img_with_retry(
                input_path,
                prompt,
                ratio=ratio,
                local_project=local_project,
                queue_priority=PRIORITY_HIGH,
                reference_paths=reference_paths,
                image_backend=image_backend_raw,
            )
            if not image_url:
                self._send_json({"error": error or "修图失败，请重试"})
                return

            try:
                download_image(image_url, output_path)
            except Exception as e:
                self._send_json({"error": f"下载图片失败: {str(e)}"})
                return

        # 确保输出与原始分辨率一致
        try:
            if regions:
                ensure_image_dimensions(output_path, target_w, target_h)
            else:
                from PIL import Image
                with Image.open(output_path) as result_img:
                    result_w, result_h = result_img.size
                    if (result_w, result_h) != (target_w, target_h):
                        scale = max(target_w / result_w, target_h / result_h)
                        new_w = int(result_w * scale)
                        new_h = int(result_h * scale)
                        temp = result_img.resize((new_w, new_h), Image.LANCZOS)
                        left = (new_w - target_w) // 2
                        top = (new_h - target_h) // 2
                        temp.crop((left, top, left + target_w, top + target_h)).save(output_path)
        except Exception as e:
            print(f"[EDIT] 尺寸恢复失败: {str(e)}")

        # 添加历史记录
        entry = build_history_entry(
            mode="edit",
            prompt=prompt,
            description=description,
            source="edit",
            project=auth_project,
            input_image=input_filename,
            output_image=output_filename,
            edit_type=edit_type,
        )
        try:
            add_history(entry)
        except Exception as e:
            print(f"[EDIT] 添加历史失败: {str(e)}")

        self._send_json({"success": True, "output_image": output_filename})

    def _send_html(self, html):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    socketserver.TCPServer.allow_reuse_address = True

    httpd = None
    listen_port = PORT
    for candidate in range(PORT, PORT + 32):
        try:
            httpd = ThreadingHTTPServer(("", candidate), Handler)
            listen_port = candidate
            break
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
                raise
            if candidate == PORT + 31:
                print(
                    f"\n❌ 端口 {PORT}–{PORT + 31} 均被占用。"
                    f"请关闭占用进程，或在 .env 中设置 PORT=其他端口。\n"
                )
                raise SystemExit(1) from e

    if listen_port != PORT:
        print(f"   注意: 端口 {PORT} 已被占用，已改用 {listen_port}\n")

    print(f"\n🚀 AI 视觉设计助手 v4 已启动!")
    print(f"   打开浏览器访问: http://localhost:{listen_port}")
    print(f"   项目目录: {BASE_DIR}")
    print(f"   项目组目录: {PROJECTS_DIR}")
    print(f"   生图后端: Lovart（按项目组 Key，见 LOVART_*_HLL / LOVART_*_XDT）")
    print(f"   Lovart API: {LOVART_BASE_URL}")
    for pname in ALLOWED_PROJECTS:
        creds = load_lovart_credentials_for_project(pname)
        print(f"   · {pname}: Lovart {len(creds)} 组 Key")
        try:
            require_project_llm_config(pname)
            print(f"     DeepSeek: 已配置 DEEPSEEK_API_KEY_{project_slug(pname)}")
        except ProjectCredentialsError:
            print(f"     DeepSeek: 未配置 DEEPSEEK_API_KEY_{project_slug(pname)}")
    print(f"   门禁: 打开页面需选择项目组并输入密码（PROJECT_PASSWORD_HLL / _XDT）")
    print(f"   功能: 需求解析 · 多图变体 · 项目组选择 · 风格参考\n")

    with httpd:
        httpd.serve_forever()
