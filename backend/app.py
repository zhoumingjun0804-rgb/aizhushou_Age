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
    load_lovart_credentials,
    mask_access_key,
)
from lovart_queue import (
    DuplicateHighJobError,
    LovartQueue,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    QueueFullError,
)
from comfyui_client import ComfyUIClient, ComfyUIClientError
from sd_client import StableDiffusionClient, SDClientError
from gif_to_svga.converter import (
    DEFAULT_MAX_BYTES,
    gif_to_svga as convert_gif_to_svga,
    VALID_FPS as SVGA_VALID_FPS,
)
from ai_outpaint import (
    layout_background_extend_prompt,
    run_ai_extend_to_size,
    splash_extend_prompt,
)
from multi_size_export import (
    export_multi_sizes,
    load_output_sizes,
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
    extract_subject_cutout,
    has_rembg,
    preferred_cutout_backend,
    postprocess_ai_cutout_png,
    save_extract_crop,
)
from gif_maker import make_breathing_gif


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
    global LOVART_CREDENTIALS, LOVART_ACCESS_KEY, LOVART_SECRET_KEY, LOVART_BASE_URL
    global LOVART_POLL_TIMEOUT, LOVART_MAX_CONCURRENCY, LOVART_TASK_RETRY
    global LOVART_TASK_RETRY_WAIT, LOVART_MODE, LOVART_QUALITY_HINT, IMAGE_BACKEND
    global LOVART_QUEUE_MAX, LOVART_JOB_TTL, LOVART_JOB_MAX_SECONDS, LOVART_ETA_AVG_SECONDS
    global lovart_queue
    global COMFYUI_API_URL, COMFYUI_CHECKPOINT, SD_API_URL, LOCAL_GENERATION_TIMEOUT
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    global QIANWEN_API_KEY, QIANWEN_BASE_URL, QIANWEN_MODEL
    global KIMI_API_KEY, KIMI_BASE_URL, KIMI_MODEL
    global DOUBAO_API_KEY, DOUBAO_BASE_URL, DOUBAO_MODEL, DOUBAO_VISION_MODEL
    _load_env_file(overwrite=True)
    LOVART_CREDENTIALS = load_lovart_credentials()
    LOVART_ACCESS_KEY = LOVART_CREDENTIALS[0][0] if LOVART_CREDENTIALS else ""
    LOVART_SECRET_KEY = LOVART_CREDENTIALS[0][1] if LOVART_CREDENTIALS else ""
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
    LOCAL_GENERATION_TIMEOUT = int(os.environ.get("LOCAL_GENERATION_TIMEOUT", "180"))
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    QIANWEN_API_KEY = os.environ.get("QIANWEN_API_KEY", "")
    QIANWEN_BASE_URL = os.environ.get("QIANWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    QIANWEN_MODEL = os.environ.get("QIANWEN_MODEL", "qwen-plus")
    KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
    KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshot-v1-8k")
    DOUBAO_API_KEY = os.environ.get("DOUBAO_API_KEY", "")
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
LOVART_CREDENTIALS = load_lovart_credentials()
LOVART_ACCESS_KEY = LOVART_CREDENTIALS[0][0] if LOVART_CREDENTIALS else ""
LOVART_SECRET_KEY = LOVART_CREDENTIALS[0][1] if LOVART_CREDENTIALS else ""
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
LOCAL_GENERATION_TIMEOUT = int(os.environ.get("LOCAL_GENERATION_TIMEOUT", "180"))
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

# ─── DeepSeek API 配置 ───────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def call_deepseek(messages, temperature=0.7, max_tokens=1000):
    """调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        return None, "未配置 DEEPSEEK_API_KEY"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
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


# ─── 通义千问 API 配置 ───────────────────────────────────────────
QIANWEN_API_KEY = os.environ.get("QIANWEN_API_KEY", "")
QIANWEN_BASE_URL = os.environ.get("QIANWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QIANWEN_MODEL = os.environ.get("QIANWEN_MODEL", "qwen-plus")


def call_qianwen(messages, temperature=0.7, max_tokens=1000):
    """调用通义千问 API（OpenAI 兼容格式）"""
    if not QIANWEN_API_KEY:
        return None, "未配置 QIANWEN_API_KEY"
    headers = {
        "Authorization": f"Bearer {QIANWEN_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": QIANWEN_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{QIANWEN_BASE_URL}/chat/completions",
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


# ─── Kimi API 配置 ───────────────────────────────────────────────
KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshot-v1-8k")


def call_kimi(messages, temperature=0.7, max_tokens=1000):
    """调用 Kimi API（OpenAI 兼容格式）"""
    if not KIMI_API_KEY:
        return None, "未配置 KIMI_API_KEY"
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": KIMI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{KIMI_BASE_URL}/chat/completions",
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


# ─── 豆包 API 配置 ───────────────────────────────────────────────
DOUBAO_API_KEY = os.environ.get("DOUBAO_API_KEY", "")
DOUBAO_BASE_URL = os.environ.get("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "doubao-pro-32k")
DOUBAO_VISION_MODEL = os.environ.get("DOUBAO_VISION_MODEL", "doubao-1-5-vision-pro-32k-250115")


def call_doubao(messages, temperature=0.7, max_tokens=1000):
    """调用豆包 API（OpenAI 兼容格式）"""
    if not DOUBAO_API_KEY:
        return None, "未配置 DOUBAO_API_KEY"
    headers = {
        "Authorization": f"Bearer {DOUBAO_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": DOUBAO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    req = urllib.request.Request(
        f"{DOUBAO_BASE_URL}/chat/completions",
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
    if len(history) > 200:
        history = history[:200]
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
    """列出所有项目组"""
    if not PROJECTS_DIR.exists():
        return []
    projects = []
    for p in sorted(PROJECTS_DIR.iterdir()):
        if p.is_dir() and not p.name.startswith('.'):
            meta = read_project_meta(p.name)
            assets = count_project_assets(p.name)
            projects.append({
                "name": p.name,
                "display_name": meta.get("display_name", p.name),
                "style_tags": meta.get("style_tags", []),
                "description": meta.get("description", ""),
                "lovart_project_id": meta.get("lovart_project_id", ""),
                "catalog": assets["catalog"],
                "product_type": project_product_type(p.name),
                "count": assets["imageCount"],
                "typeCount": assets["typeCount"],
            })
    return projects

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


def _sync_lovart_project_title(client: LovartClient, project_id: str, title: str) -> None:
    if not title:
        return
    try:
        current = client.get_project_name(project_id)
        if not current or current.lower() == "untitled":
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
    title = (meta.get("display_name") or local_project).strip()
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
    selected_list = _selected_images_from_fields(fields, project)
    image_paths = []
    if not selected_list and project:
        proj_dir = PROJECTS_DIR / project
        if proj_dir.exists():
            for img in sorted(proj_dir.iterdir()):
                if img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    image_paths.append(img)
                    if len(image_paths) >= 10:
                        break
    elif selected_list:
        image_paths = collect_reference_image_paths(selected_list, project)
    return image_paths

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


def _primary_llm_provider_label():
    base = (DEEPSEEK_BASE_URL or "").lower()
    if "dtok.ai" in base:
        return "dtok"
    return "deepseek"


def analyze_prompt_from_summary(summary, project_meta=None, regenerate=False):
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
    last_error = ""

    ai_prompt, error = call_deepseek(messages, temperature=temperature, max_tokens=500)
    if not error:
        print(f"[{_primary_llm_provider_label()}] AI 输出: {ai_prompt}")
        return (ai_prompt or "").strip(), "llm", _primary_llm_provider_label(), DEEPSEEK_MODEL, None
    last_error = error
    print(f"[DeepSeek Error] {error}, 尝试千问...")

    ai_prompt, error2 = call_qianwen(messages, temperature=temperature, max_tokens=500)
    if not error2:
        print(f"[千问] AI 输出: {ai_prompt}")
        return (ai_prompt or "").strip(), "llm", "qianwen", QIANWEN_MODEL, None
    last_error = error2
    print(f"[千问 Error] {error2}, 尝试Kimi...")

    ai_prompt, error3 = call_kimi(messages, temperature=temperature, max_tokens=500)
    if not error3:
        print(f"[Kimi] AI 输出: {ai_prompt}")
        return (ai_prompt or "").strip(), "llm", "kimi", KIMI_MODEL, None
    last_error = error3
    print(f"[Kimi Error] {error3}, 尝试豆包...")

    ai_prompt, error4 = call_doubao(messages, temperature=temperature, max_tokens=500)
    if not error4:
        print(f"[豆包] AI 输出: {ai_prompt}")
        return (ai_prompt or "").strip(), "llm", "doubao", DOUBAO_MODEL, None

    last_error = error4
    print(f"[豆包 Error] {error4}, 降级到简单拼接")
    fallback = expand_prompt_from_summary(summary, project_meta)
    detail = (last_error or "未知错误")[:160]
    warning = f"大模型不可用（{detail}），已使用本地规则拼接。请检查 .env 中的 Key 与模型名。"
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
):
    """调用 Lovart OpenAPI 生图；多 Key 时在并发/额度受限时自动切换。"""
    if not LOVART_CREDENTIALS:
        return None, "未配置 LOVART_ACCESS_KEY / LOVART_SECRET_KEY"

    timeout = max(poll_timeout, LOVART_POLL_TIMEOUT)
    last_error = None
    project_title = ""
    if local_project:
        meta = get_project_meta(local_project) or {}
        project_title = (meta.get("display_name") or local_project).strip()

    for cred_index, (access_key, secret_key) in enumerate(LOVART_CREDENTIALS):
        client = LovartClient(
            access_key=access_key,
            secret_key=secret_key,
            base_url=LOVART_BASE_URL,
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

            has_backup_key = cred_index + 1 < len(LOVART_CREDENTIALS)
            if is_lovart_limit_error(error) and has_backup_key:
                print(
                    f"[Lovart] Key {mask_access_key(access_key)} 并发或额度受限，"
                    f"切换到备用 Key ({cred_index + 2}/{len(LOVART_CREDENTIALS)})"
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


def check_lovart_reachable(timeout: int = 8) -> tuple[bool, str]:
    """快速探测 Lovart API 是否可达（用于生图/智能提取前置检查）。"""
    if not LOVART_CREDENTIALS:
        return False, "未配置 LOVART_ACCESS_KEY / LOVART_SECRET_KEY"
    ak, sk = LOVART_CREDENTIALS[0]
    client = LovartClient(
        access_key=ak,
        secret_key=sk,
        base_url=LOVART_BASE_URL,
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
    aliases = {
        "sd": "stable_diffusion",
        "stable-diffusion": "stable_diffusion",
        "stable diffusion": "stable_diffusion",
    }
    backend = aliases.get(backend, backend)
    if backend:
        return backend
    return _resolve_image_backend()


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
        return ratio or bbox_to_ratio(w, h), w, h
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
):
    backend = normalize_image_backend(image_backend)
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
    if backend == "lovart" and local_project and LOVART_CREDENTIALS:
        ak, sk = LOVART_CREDENTIALS[0]
        client = LovartClient(
            access_key=ak,
            secret_key=sk,
            base_url=LOVART_BASE_URL,
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


def _save_ref_images_from_fields(fields: dict) -> list:
    paths = []
    for i in range(3):
        ref_key = f"ref_image_{i}"
        ref_data = fields.get(ref_key)
        if ref_data and isinstance(ref_data, dict):
            file_ext = pathlib.Path(ref_data.get("filename", ".png")).suffix or ".png"
            ref_filename = f"ref_{uuid.uuid4().hex}{file_ext}"
            ref_path = UPLOAD_DIR / ref_filename
            ref_path.write_bytes(ref_data["data"])
            paths.append(ref_path)
    return paths


def build_generation_payload(fields: dict, kind: str) -> dict:
    """从 multipart 字段构建生图任务 payload。"""
    project = str(fields.get("project", "") or "").strip()
    count = int(str(fields.get("count", "3")).strip() or "3")
    client_id = str(fields.get("client_id", "") or "").strip()
    image_backend = normalize_image_backend(fields.get("image_backend"))
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
    image_paths.extend(str(p) for p in _save_ref_images_from_fields(fields))
    payload["image_paths"] = image_paths
    payload["input_filename"] = input_filename
    payload["mode"] = "img2img" if image_paths else "text2img"
    return payload


def execute_generation_job(job: dict) -> None:
    """队列 worker 执行生图任务。"""
    payload = job["payload"]
    job_id = job["job_id"]
    started = time.time()

    project = payload.get("project") or None
    lovart_err = lovart_project_required_error(project or "")
    if lovart_err:
        lovart_queue.fail_job(job_id, lovart_err)
        return

    prompt = payload.get("prompt", "")
    count = int(payload.get("count") or 3)
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
        )
        if image_url:
            output_filename = f"variant_{uuid.uuid4().hex}.png"
            output_path = OUTPUT_DIR / output_filename
            try:
                download_image(image_url, output_path)
                variants.append({"filename": output_filename, "error": None})
            except Exception as e:
                variants.append({"filename": None, "error": format_url_error(e)})
        else:
            variants.append({"filename": None, "error": error or "生成失败"})
        lovart_queue.set_progress(job_id, idx + 1, count)

    lovart_queue.set_variants(job_id, variants)
    with lovart_queue._jobs_lock:
        stored = lovart_queue._jobs.get(job_id)
        if stored and stored.get("status") == "running":
            stored["status"] = "done"
            stored["finished_at"] = time.time()

    if variants and variants[0].get("filename"):
        output_images = [v["filename"] for v in variants if v.get("filename")]
        entry = build_history_entry(
            mode=mode,
            prompt=prompt,
            description="",
            source="job",
            project=project or "",
            input_image=input_filename,
            output_images=output_images,
            variants_count=len(output_images),
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
            "【局部修改】只改选区内的指定内容；严格保持与原图相同的画风、配色、光影、质感与排版；"
            "不要整体重绘，不要改变图片尺寸、比例和选区外的任何内容。"
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


def ensure_image_dimensions(path, target_w, target_h):
    from PIL import Image

    with Image.open(path) as img:
        if img.size == (target_w, target_h):
            return
        img.resize((target_w, target_h), Image.LANCZOS).save(path)


def composite_region_blend(base_path, overlay_path, output_path, x, y, w, h, feather=12):
    """将修图结果仅粘贴回选区，边缘与原图羽化融合，选区外像素不变"""
    from PIL import Image, ImageDraw, ImageFilter

    base = Image.open(base_path).convert("RGBA")
    overlay = Image.open(overlay_path).convert("RGBA")

    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = min(base.width, x1 + int(w))
    y2 = min(base.height, y1 + int(h))
    actual_w, actual_h = x2 - x1, y2 - y1
    if actual_w <= 0 or actual_h <= 0:
        base.convert("RGB").save(output_path)
        return

    overlay = overlay.resize((actual_w, actual_h), Image.LANCZOS)
    original_patch = base.crop((x1, y1, x2, y2))

    feather = min(int(feather), actual_w // 4, actual_h // 4, 24)
    if feather < 2:
        mask = Image.new("L", (actual_w, actual_h), 255)
    else:
        mask = Image.new("L", (actual_w, actual_h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rectangle(
            [feather, feather, actual_w - feather - 1, actual_h - feather - 1],
            fill=255,
        )
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, feather // 2)))

    blended = Image.composite(overlay, original_patch, mask)
    result = base.copy()
    result.paste(blended, (x1, y1))
    result.convert("RGB").save(output_path)
    print(f"[BLEND] region ({x1},{y1}) {actual_w}x{actual_h} -> {output_path}")


def call_img2img_with_retry(
    input_path,
    prompt,
    ratio="1:1",
    max_retries=3,
    local_project=None,
    image_backend=None,
    queue_priority=PRIORITY_LOW,
    reference_paths=None,
):
    backend = normalize_image_backend(image_backend)
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
    """框选区域优先做真实抠图，失败时回退到 Lovart 兜底。"""
    from PIL import Image

    with Image.open(input_path) as im:
        img_w, img_h = im.size
    x, y, w, h = compute_extract_crop_bbox(img_w, img_h, roi_x, roi_y, roi_w, roi_h)
    crop_path = UPLOAD_DIR / f"ai_extract_crop_{uuid.uuid4().hex[:12]}.png"
    save_extract_crop(input_path, crop_path, x, y, w, h)
    try:
        try:
            out_w, out_h, cutout_backend = extract_subject_cutout(crop_path, output_path, trim=trim)
            return {
                "width": out_w,
                "height": out_h,
                "roiX": x,
                "roiY": y,
                "roiWidth": w,
                "roiHeight": h,
                "extractMode": "cutout",
                "imageBackend": cutout_backend,
                "usedPrompt": bool((prompt or "").strip()),
            }
        except Exception as cutout_err:
            if not LOVART_CREDENTIALS:
                raise ValueError(f"智能抠图失败：{cutout_err}")
            reachable, reach_err = check_lovart_reachable(timeout=8)
            if not reachable:
                raise ValueError(f"智能抠图失败：{cutout_err}；且无法连接 Lovart 兜底服务：{reach_err or '网络不可用'}")

        ai_prompt = build_ai_extract_prompt(prompt)
        ratio = bbox_to_ratio(w, h)
        image_url, error = call_img2img_with_retry(
            crop_path,
            ai_prompt,
            ratio=ratio,
            max_retries=2,
            local_project=local_project,
        )
        if not image_url:
            raise ValueError(error or "智能抠图失败，且 Lovart 兜底未返回结果")

        raw_path = UPLOAD_DIR / f"ai_extract_raw_{uuid.uuid4().hex[:12]}.png"
        download_image(image_url, raw_path)
        try:
            out_w, out_h = postprocess_ai_cutout_png(raw_path, output_path, trim=trim)
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
            "extractMode": "ai_fallback",
            "imageBackend": "lovart",
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
        desc = (region.get("description") or "").strip()
        if w < 8 or h < 8 or not desc:
            continue

        crop_path = UPLOAD_DIR / f"edit_crop_{uuid.uuid4().hex}.png"
        crop_image(work_path, crop_path, x, y, w, h)

        with Image.open(crop_path) as cropped:
            cw, ch = cropped.size

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
        )
        if not image_url:
            return None, f"选区 {idx + 1} 修图失败: {error or '未知错误'}"

        edited_crop = UPLOAD_DIR / f"edit_crop_out_{uuid.uuid4().hex}.png"
        download_image(image_url, edited_crop)
        resize_image_file(edited_crop, cw, ch)

        merged_path = OUTPUT_DIR / f"edit_merged_{uuid.uuid4().hex}.png"
        composite_region_blend(work_path, edited_crop, merged_path, x, y, cw, ch)
        shutil.move(merged_path, work_path)

        for tmp in (crop_path, edited_crop):
            try:
                tmp.unlink()
            except OSError:
                pass

    ensure_image_dimensions(work_path, orig_w, orig_h)
    return work_path, None


# ─── 表单解析 ────────────────────────────────────────────────────
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
_html_cache = {"mtime": 0.0, "content": ""}


def get_html_page():
    """生产环境缓存模板；DEV_RELOAD=1 时每次请求重新读取（改 UI 无需重启）。"""
    if not HTML_TEMPLATE.exists():
        return "<html><body><h1>缺少 templates/index.html</h1></body></html>"
    dev = os.environ.get("DEV_RELOAD", "").strip().lower() in ("1", "true", "yes")
    if dev:
        return HTML_TEMPLATE.read_text(encoding="utf-8")
    mtime = HTML_TEMPLATE.stat().st_mtime
    if _html_cache["content"] and _html_cache["mtime"] == mtime:
        return _html_cache["content"]
    content = HTML_TEMPLATE.read_text(encoding="utf-8")
    _html_cache["mtime"] = mtime
    _html_cache["content"] = content
    return content




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

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Client-Id')

    def do_OPTIONS(self):
        path = self._normalized_path()
        if path in ('/api/edit-image', '/edit-image', '/parse', '/api/analyze',
                    '/generate-variants', '/generate-with-prompt', '/upscale', '/api/gif-to-svga',
                    '/api/multi-size-export', '/api/crop-image', '/api/magic-cutout',
                    '/api/smart-cutout', '/api/make-breathing-gif', '/api/layout-extend',
                    '/api/generation/jobs'):
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
        elif path.startswith('/outputs/'):
            self._serve_file(OUTPUT_DIR, path.split('/')[-1])
        elif path.startswith('/projects/') and '/images/' in path:
            parts = path.split('/')
            if len(parts) >= 5:
                project = urllib.parse.unquote(parts[2])
                filename = urllib.parse.unquote(parts[4].split('?')[0])
                self._serve_file(project_refs_dir(project), filename)
            else:
                self.send_response(404)
                self.end_headers()
        elif path == '/projects':
            self._send_json({"projects": list_projects()})
        elif path.startswith('/projects/') and path.endswith('/images'):
            parts = path.split('/')
            project = urllib.parse.unquote(parts[2])
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
            self._send_json({"items": filter_history_items(load_history())})
        elif path == '/api/output-sizes':
            params = self._query_params()
            project = params.get("project", [""])[0]
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
            job_id = path[len('/api/generation/jobs/'):].strip('/')
            self._handle_generation_job_get(job_id)
        elif path == '/api/generation/jobs':
            params = self._query_params()
            client_id = (params.get("client_id", [""])[0] or "").strip()
            if not client_id:
                self._send_json({"error": "缺少 client_id"}, status=400)
                return
            self._send_json({"jobs": lovart_queue.list_jobs(client_id)})
        elif path == '/api/smart-cutout/status':
            self._handle_smart_cutout_status()
        elif path == '/api/layout-extend/presets':
            self._send_json({"presets": list_layout_presets()})
        elif path == '/api/design-types':
            params = self._query_params()
            project = urllib.parse.unquote(params.get("project", [""])[0])
            if not project:
                self._send_json({"error": "请指定 project 参数"})
                return
            self._send_json({
                "project": project,
                "catalog": detect_project_catalog(project),
                "product_type": project_product_type(project),
                "designTypes": list_design_types_for_project(project),
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self._normalized_path()
        post_routes = {
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

        text = data.get('text', '').strip()
        project_name = data.get('project')

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
        project = fields.get('project', '')

        try:
            summary = json.loads(summary_json)
        except:
            summary = {}

        # 获取项目元数据
        project_meta = get_project_meta(project) if project else None
        regenerate = str(fields.get("regenerate", "0")).strip().lower() in ("1", "true", "yes")

        ai_prompt, source, provider, model, warning = analyze_prompt_from_summary(
            summary,
            project_meta,
            regenerate=regenerate,
        )

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
            kind = str(fields.get("kind", "variants") or "variants").strip()
            client_id = str(fields.get("client_id", "") or "").strip()
            if not client_id:
                hdr = self.headers.get("X-Client-Id", "").strip()
                client_id = hdr
            if not client_id:
                self._send_json({"error": "缺少 client_id"}, status=400)
                return

            payload = build_generation_payload(fields, kind)
            payload["client_id"] = client_id
            backend = normalize_image_backend(payload.get("image_backend"))

            if backend != "lovart":
                job_id = uuid.uuid4().hex[:12]
                job = {
                    "job_id": job_id,
                    "client_id": client_id,
                    "kind": kind,
                    "status": "running",
                    "priority": PRIORITY_HIGH,
                    "payload": payload,
                    "progress": {"current": 0, "total": int(payload.get("count") or 3)},
                }
                with lovart_queue._jobs_lock:
                    lovart_queue._jobs[job_id] = job
                execute_generation_job(job)
                with lovart_queue._jobs_lock:
                    stored = lovart_queue._jobs.get(job_id, {})
                variants = stored.get("variants")
                if stored.get("status") == "failed":
                    self._send_json({"error": stored.get("error") or "生成失败"}, status=500)
                    return
                self._send_json(
                    {
                        "ok": True,
                        "sync": True,
                        "variants": variants or [],
                        "prompt": payload.get("prompt"),
                    }
                )
                return

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
        project = fields.get('project', '')
        count = int(fields.get('count', '3'))
        ratio = fields.get('ratio', '1:1')
        if not prompt:
            self._send_json({"error": "请提供关键词"})
            return

        product_type = project_product_type(project) if project else normalize_product_type(str(fields.get("type", "")))
        lovart_err = lovart_project_required_error(project)
        if lovart_err:
            self._send_json({"error": lovart_err})
            return

        image_paths = _build_image_paths_from_selection(fields, project)

        # 处理用户上传的参考图（最多3张）
        for i in range(3):
            ref_key = f'ref_image_{i}'
            ref_data = fields.get(ref_key)
            if ref_data and isinstance(ref_data, dict):
                file_ext = pathlib.Path(ref_data.get('filename', '.png')).suffix or '.png'
                ref_filename = f"ref_{uuid.uuid4().hex}{file_ext}"
                ref_path = UPLOAD_DIR / ref_filename
                ref_path.write_bytes(ref_data['data'])
                image_paths.append(ref_path)

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
        project = fields.get('project', '')
        count = int(fields.get('count', '3'))
        ratio = fields.get('ratio', '1:1')
        uploaded_file = fields.get('file')

        try:
            summary = json.loads(summary_json)
        except:
            summary = {}

        # 获取项目元数据
        project_meta = get_project_meta(project) if project else None

        product_type = project_product_type(project) if project else normalize_product_type(str(fields.get("type", "")))
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

        # 处理用户上传的参考图（最多3张）
        for i in range(3):
            ref_key = f'ref_image_{i}'
            ref_data = fields.get(ref_key)
            if ref_data and isinstance(ref_data, dict):
                file_ext = pathlib.Path(ref_data.get('filename', '.png')).suffix or '.png'
                ref_filename = f"ref_{uuid.uuid4().hex}{file_ext}"
                ref_path = UPLOAD_DIR / ref_filename
                ref_path.write_bytes(ref_data['data'])
                image_paths.append(ref_path)

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
                    'gif': 'image/gif', 'webp': 'image/webp', 'svga': 'application/octet-stream'}.get(
                ext.lstrip('.'), 'application/octet-stream')
            self.send_header('Content-type', mime)
            self.send_header('Content-Length', str(filepath.stat().st_size))
            self.end_headers()
            self.wfile.write(filepath.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_make_breathing_gif(self):
        """静态底图 + 按钮图层 → 呼吸动效 GIF"""
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "请使用 multipart 上传图片"})
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            boundary = content_type.split("boundary=")[-1].encode()
            fields = parse_multipart(body, boundary)
            bg_field = fields.get("background")
            btn_field = fields.get("button")
            if not bg_field or not isinstance(bg_field, dict):
                self._send_json({"error": "请上传底图（不动）"})
                return
            if not btn_field or not isinstance(btn_field, dict):
                self._send_json({"error": "请上传按钮图（要动的图层）"})
                return
            intensity = str(fields.get("intensity", "medium")).strip() or "medium"
            if intensity not in ("weak", "medium", "strong"):
                intensity = "medium"
            try:
                duration_sec = float(str(fields.get("duration_sec", "1.6")).strip())
                offset_x = int(str(fields.get("offset_x", "0")).strip())
                offset_y = int(str(fields.get("offset_y", "0")).strip())
            except ValueError:
                self._send_json({"error": "参数格式无效"})
                return
            job_id = uuid.uuid4().hex[:12]
            bg_path = UPLOAD_DIR / f"gif_bg_{job_id}.png"
            btn_path = UPLOAD_DIR / f"gif_btn_{job_id}.png"
            output_filename = f"breathing_{job_id}.gif"
            output_path = OUTPUT_DIR / output_filename
            bg_path.write_bytes(bg_field["data"])
            btn_path.write_bytes(btn_field["data"])
            meta = make_breathing_gif(
                bg_path, btn_path, output_path,
                intensity=intensity,
                duration_sec=duration_sec,
                offset_x=offset_x,
                offset_y=offset_y,
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
        self._send_json({
            "lovartKeyCount": len(LOVART_CREDENTIALS),
            "imageBackend": normalize_image_backend(),
            "smartCutoutBackend": preferred_cutout_backend(),
            "smartCutoutHasRembg": has_rembg(),
            "smartCutoutAsync": True,
            "lovartBaseUrl": LOVART_BASE_URL,
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
                            "可能是 Lovart 网络不通或生成过慢，请稍后重试。"
                        ),
                    }
        self._send_json(job)

    def _handle_smart_cutout(self):
        """框选区域主体抠图为透明 PNG（优先本地抠图，必要时 Lovart 兜底）。"""
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
            local_project = str(fields.get("local_project", "") or "").strip() or None
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
                "message": "正在识别主体并去除背景，通常 5～20 秒…",
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
                if LOVART_CREDENTIALS:
                    local_project = (
                        str(fields.get("local_project", "") or fields.get("project", "")).strip() or None
                    )

                    def ai_background_fn(src_img, meta):
                        tw, th = int(meta["width"]), int(meta["height"])
                        return run_ai_extend_to_size(
                            src_img,
                            tw,
                            th,
                            prompt=layout_background_extend_prompt(tw, th),
                            ratio=bbox_to_ratio(tw, th),
                            upload_dir=UPLOAD_DIR,
                            img2img=lambda p, pr, r: call_img2img_with_retry(
                                p, pr, ratio=r, max_retries=1, local_project=local_project
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
            ptype = (
                project_product_type(project_name)
                if project_name
                else normalize_product_type(str(fields.get("type", "")))
            )
            img_field = fields.get("image")
            if not img_field or not isinstance(img_field, dict):
                self._send_json({"error": "请上传图片"})
                return
            filename = (img_field.get("filename") or "input.png").lower()
            if not any(filename.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                self._send_json({"error": "仅支持 PNG、JPG、WebP"})
                return
            job_id = uuid.uuid4().hex[:12]
            ext = pathlib.Path(filename).suffix or ".png"
            input_path = UPLOAD_DIR / f"multi_src_{job_id}{ext}"
            input_path.write_bytes(img_field["data"])
            source_raw = fields.get("source_name", "") or img_field.get("filename", "")
            use_ai = str(fields.get("use_ai", "1")).strip().lower() in ("1", "true", "yes")
            ai_canvas_fn = None
            if use_ai:
                _reload_runtime_env()
                if LOVART_CREDENTIALS:
                    local_project = project_name or None

                    def ai_canvas_fn(src_img, tw, th):
                        return run_ai_extend_to_size(
                            src_img,
                            tw,
                            th,
                            prompt=splash_extend_prompt(tw, th),
                            ratio=bbox_to_ratio(tw, th),
                            upload_dir=UPLOAD_DIR,
                            img2img=lambda p, pr, r: call_img2img_with_retry(
                                p, pr, ratio=r, max_retries=1, local_project=local_project
                            ),
                            download_image=download_image,
                        )
                else:
                    print("[MULTI-SIZE] 未配置 Lovart，将使用裁切满图")

            result = export_multi_sizes(
                input_path,
                OUTPUT_DIR,
                job_id,
                config_path=sizes_config_path(ptype),
                source_basename=str(source_raw),
                use_ai=bool(ai_canvas_fn),
                ai_canvas_fn=ai_canvas_fn,
            )
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

        image_data = data.get('image', '')
        description = data.get('description', '')
        edit_type = data.get('editType', '文案修改')
        keep_elements = data.get('keepElements', '')
        local_project = (data.get('project') or '').strip() or None
        regions = data.get('regions') or []
        reference_paths = _save_edit_reference_images_from_payload(data)

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

        # 获取原图尺寸
        try:
            from PIL import Image
            with Image.open(input_path) as orig_img:
                orig_w, orig_h = orig_img.size
        except:
            orig_w, orig_h = 1024, 1024  # 默认尺寸

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
            )
            if not image_url:
                self._send_json({"error": error or "修图失败，请重试"})
                return

            try:
                download_image(image_url, output_path)
            except Exception as e:
                self._send_json({"error": f"下载图片失败: {str(e)}"})
                return

        # 确保输出与原图尺寸一致（局部修图已在合成时保持，整图修图需缩放对齐）
        try:
            if regions:
                ensure_image_dimensions(output_path, orig_w, orig_h)
            else:
                from PIL import Image
                with Image.open(output_path) as result_img:
                    result_w, result_h = result_img.size
                    if (result_w, result_h) != (orig_w, orig_h):
                        scale = max(orig_w / result_w, orig_h / result_h)
                        new_w = int(result_w * scale)
                        new_h = int(result_h * scale)
                        temp = result_img.resize((new_w, new_h), Image.LANCZOS)
                        left = (new_w - orig_w) // 2
                        top = (new_h - orig_h) // 2
                        temp.crop((left, top, left + orig_w, top + orig_h)).save(output_path)
        except Exception as e:
            print(f"[EDIT] 尺寸恢复失败: {str(e)}")

        # 添加历史记录
        entry = build_history_entry(
            mode="edit",
            prompt=prompt,
            description=description,
            source="edit",
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
    key_count = len(LOVART_CREDENTIALS)
    print(f"   生图后端: Lovart（当前仅此）")
    print(f"   Lovart API: {LOVART_BASE_URL}（已配置 {key_count} 组 Key，受限时自动切换）")
    if not LOVART_CREDENTIALS:
        print("   提示: 未配置 LOVART_ACCESS_KEY / LOVART_SECRET_KEY，生图将不可用")
    if not any([DEEPSEEK_API_KEY, QIANWEN_API_KEY, KIMI_API_KEY, DOUBAO_API_KEY]):
        print("   提示: 未配置大模型 API Key，关键词分析将使用本地规则拼接")
    print(f"   功能: 需求解析 · 多图变体 · 项目组选择 · 风格参考\n")

    with httpd:
        httpd.serve_forever()
