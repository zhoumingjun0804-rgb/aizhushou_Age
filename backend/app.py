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

from lovart_client import (
    LovartClient,
    LovartError,
    is_lovart_limit_error,
    load_lovart_credentials,
    mask_access_key,
)
from comfyui_client import ComfyUIClient, ComfyUIClientError
from sd_client import StableDiffusionClient, SDClientError

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
PROJECTS_DIR = pathlib.Path(os.environ.get("PROJECTS_DIR", str(BASE_DIR / "projects")))
HISTORY_FILE = BASE_DIR / "history.json"


def _load_env_file():
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
        if key and key not in os.environ:
            os.environ[key] = value


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
LOVART_GENERATION_LOCK = threading.Lock()
COMFYUI_API_URL = os.environ.get("COMFYUI_API_URL", "http://127.0.0.1:8188").strip()
COMFYUI_CHECKPOINT = os.environ.get("COMFYUI_CHECKPOINT", "").strip()
SD_API_URL = os.environ.get("SD_API_URL", "http://127.0.0.1:7860").strip()
LOCAL_GENERATION_TIMEOUT = int(os.environ.get("LOCAL_GENERATION_TIMEOUT", "180"))
IMAGE_BACKEND = os.environ.get("IMAGE_BACKEND", "auto").strip().lower()

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

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


def filter_history_items(items):
    """仅返回本地 outputs 仍存在的图片，避免历史缩略图 404。"""
    filtered = []
    for item in items:
        entry = dict(item)
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
            meta_file = p / "project.json"
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text('utf-8'))
                except:
                    pass
            images = [f for f in p.iterdir() if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif')]
            projects.append({
                "name": p.name,
                "display_name": meta.get("display_name", p.name),
                "style_tags": meta.get("style_tags", []),
                "description": meta.get("description", ""),
                "count": len(images)
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


def ensure_lovart_project(local_project: str, client: LovartClient) -> str:
    """本地项目组（如「画啦啦」）绑定并复用同一个 Lovart project_id。"""
    if not local_project:
        return ""

    proj_dir = PROJECTS_DIR / local_project
    if not proj_dir.is_dir():
        return ""

    meta = get_project_meta(local_project) or {}
    title = (meta.get("display_name") or local_project).strip()
    existing = (meta.get("lovart_project_id") or "").strip()

    if existing:
        saved_id = client.save_project(project_id=existing, title=title)
        if saved_id:
            if saved_id != existing:
                save_project_meta(local_project, lovart_project_id=saved_id)
            return saved_id
        print(f"[Lovart] 项目组 {local_project} 原绑定失效，将创建新项目")

    new_id = client.save_project(title=title)
    if new_id:
        save_project_meta(
            local_project,
            lovart_project_id=new_id,
            display_name=title,
        )
        print(f"[Lovart] 项目组「{title}」已绑定 Lovart 项目 {new_id[:12]}…")
    return new_id or ""

def get_project_images(project_name):
    """获取项目的所有图片"""
    proj_dir = PROJECTS_DIR / project_name
    if not proj_dir.exists():
        return []
    images = []
    for f in sorted(proj_dir.iterdir()):
        if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
            images.append(f.name)
    return images


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
    if backend == "auto":
        if LOVART_CREDENTIALS:
            return "lovart"
        return "dreamina"
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


def ratio_to_size(ratio: str):
    mapping = {
        "1:1": (1024, 1024),
        "16:9": (1344, 768),
        "9:16": (768, 1344),
        "4:3": (1152, 864),
        "3:4": (864, 1152),
        "4:5": (896, 1120),
        "2:3": (832, 1248),
        "21:9": (1536, 640),
    }
    return mapping.get(ratio, (1024, 1024))


def _normalize_lovart_error(message):
    if not message:
        return message
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

        for attempt in range(LOVART_TASK_RETRY):
            with LOVART_GENERATION_LOCK:
                try:
                    image_url, error = client.generate_image(
                        prompt=prompt,
                        image_paths=image_paths,
                        ratio=ratio,
                        timeout=timeout,
                        mode=LOVART_MODE,
                        quality_hint=LOVART_QUALITY_HINT,
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


def call_comfyui(mode, prompt, image_paths=None, ratio="1:1", poll_timeout=90):
    if mode == "img2img" and image_paths:
        return None, "ComfyUI 当前仅支持文生图，请先去掉参考图或改用 Lovart / Stable Diffusion"
    if not COMFYUI_CHECKPOINT:
        return None, "未配置 COMFYUI_CHECKPOINT（ComfyUI 模型文件名）"

    width, height = ratio_to_size(ratio)
    client = ComfyUIClient(
        base_url=COMFYUI_API_URL,
        timeout=max(poll_timeout, LOCAL_GENERATION_TIMEOUT),
    )
    try:
        return client.generate_image(prompt, width, height, image_paths=image_paths)
    except ComfyUIClientError as e:
        return None, e.message


def call_stable_diffusion(mode, prompt, image_paths=None, ratio="1:1", poll_timeout=90):
    width, height = ratio_to_size(ratio)
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
    image_backend=None,
    local_project=None,
    lovart_project_id=None,
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
        )
    if backend == "comfyui":
        return call_comfyui(mode, prompt, image_paths=image_paths, ratio=ratio, poll_timeout=poll_timeout)
    if backend == "stable_diffusion":
        return call_stable_diffusion(mode, prompt, image_paths=image_paths, ratio=ratio, poll_timeout=poll_timeout)
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
    image_backend=None,
    local_project=None,
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
            image_backend=backend,
            local_project=local_project,
            lovart_project_id=lovart_project_id,
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


def download_image(url, save_path):
    if url.startswith("file://"):
        shutil.copy2(url[7:], save_path)
        print(f"[DL] Copied local file -> {save_path}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ssl_ctx, timeout=120) as resp:
        data = resp.read()
        with open(save_path, 'wb') as f:
            f.write(data)
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


def build_edit_prompt(description, edit_type="", keep_elements="", region_only=False):
    prompt = description or ""
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
    image_backend,
    ratio="1:1",
    max_retries=3,
    local_project=None,
):
    last_error = None
    for attempt in range(max_retries):
        try:
            image_url, error = call_image_generator(
                mode="img2img",
                prompt=prompt,
                image_paths=[str(input_path)],
                model_version="4.6",
                image_backend=image_backend,
                ratio=ratio,
                local_project=local_project,
            )
            if image_url:
                return image_url, None
            last_error = error
            print(f"[EDIT] 第{attempt + 1}次尝试失败: {error}")
        except Exception as e:
            last_error = str(e)
            print(f"[EDIT] 第{attempt + 1}次尝试异常: {last_error}")
    return None, last_error


def edit_image_regions(base_path, regions, edit_type, keep_elements, image_backend, ratio, local_project=None):
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
        region_prompt = build_edit_prompt(desc, edit_type, keep_elements, region_only=True)
        image_url, error = call_img2img_with_retry(
            crop_path, region_prompt, image_backend, ratio=crop_ratio, local_project=local_project
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
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 视觉设计助手 v4</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
               background: #0f0c29; min-height: 100vh; padding: 20px; overflow-x: hidden; }
        body::before { content: ''; position: fixed; top: -50%; left: -50%; width: 200%; height: 200%;
               background: linear-gradient(45deg, #0f0c29, #302b63, #24243e, #0f0c29, #1a1a3e, #302b63);
               z-index: -1; }
        .container { max-width: 1000px; margin: 0 auto; position: relative; }
        .card { background: rgba(255,255,255,0.04); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
                border-radius: 20px; padding: 28px; border: 1px solid rgba(255,255,255,0.08);
                box-shadow: 0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06); margin-bottom: 20px; }
        h1 { text-align: center; margin-bottom: 6px; font-size: 24px; font-weight: 700;
             background: linear-gradient(135deg, #a78bfa, #818cf8, #6ee7b7);
             -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
        .subtitle { text-align: center; color: rgba(255,255,255,0.45); font-size: 13px; margin-bottom: 16px; }
        
        /* 历史记录按钮（与 Tab 同一行） */
        .history-btn { flex-shrink: 0; margin-left: auto;
                       background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
                       padding: 10px 16px; cursor: pointer; font-size: 13px; font-weight: 500; color: rgba(255,255,255,0.7);
                       transition: all 0.25s; backdrop-filter: blur(10px); white-space: nowrap; }
        .history-btn:hover { border-color: rgba(167,139,250,0.5); color: #a78bfa; background: rgba(167,139,250,0.1); }
        
        /* 历史记录侧边抽屉 */
        .history-drawer { position: fixed; top: 0; right: -400px; width: 380px; height: 100vh;
                          background: rgba(20,18,50,0.95); backdrop-filter: blur(24px);
                          border-left: 1px solid rgba(255,255,255,0.08);
                          box-shadow: -4px 0 32px rgba(0,0,0,0.4);
                          transition: right 0.35s cubic-bezier(0.4,0,0.2,1); z-index: 1000; overflow: hidden; }
        .history-drawer.open { right: 0; }
        .drawer-header { padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.08); 
                         display: flex; justify-content: space-between; align-items: center; }
        .drawer-title { font-size: 16px; font-weight: 600; color: rgba(255,255,255,0.9); }
        .drawer-close { background: none; border: none; font-size: 24px; cursor: pointer; color: rgba(255,255,255,0.4); }
        .drawer-close:hover { color: rgba(255,255,255,0.9); }
        .drawer-content { padding: 12px; overflow-y: auto; height: calc(100vh - 60px); }
        .drawer-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                          background: rgba(0,0,0,0.5); backdrop-filter: blur(4px); z-index: 999; display: none; }
        .drawer-overlay.open { display: block; }
        
        .history-item { display: flex; gap: 12px; padding: 12px; border-radius: 12px;
                        margin-bottom: 8px; cursor: pointer; transition: all 0.2s;
                        border: 1px solid rgba(255,255,255,0.04); }
        .history-item:hover { background: rgba(255,255,255,0.06); border-color: rgba(167,139,250,0.3); }
        .history-thumb { width: 64px; height: 64px; object-fit: cover; border-radius: 8px; flex-shrink: 0; }
        .history-img-wrap { position: relative; flex-shrink: 0; }
        .history-download { position: absolute; bottom: 4px; right: 4px; background: rgba(0,0,0,0.6); color: white; 
                           width: 22px; height: 22px; border-radius: 50%; text-align: center; 
                           line-height: 22px; font-size: 12px; text-decoration: none; }
        .history-download:hover { background: rgba(167,139,250,0.8); }
        .history-info { flex: 1; min-width: 0; }
        .history-time { font-size: 11px; color: rgba(255,255,255,0.35); margin-bottom: 4px; }
        .history-prompt { font-size: 13px; color: rgba(255,255,255,0.75); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .history-meta { font-size: 11px; color: #a78bfa; margin-top: 4px; }
        
        /* 全屏图片查看 */
        .fullscreen-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                               background: rgba(0,0,0,0.92); backdrop-filter: blur(8px); z-index: 2000; display: none;
                               justify-content: center; align-items: center; cursor: zoom-out; }
        .fullscreen-overlay.open { display: flex; }
        .fullscreen-img { max-width: 90vw; max-height: 90vh; object-fit: contain; border-radius: 12px;
                          box-shadow: 0 0 60px rgba(167,139,250,0.15); }
        .fullscreen-close { position: absolute; top: 20px; right: 20px; color: rgba(255,255,255,0.7); 
                             font-size: 32px; cursor: pointer; background: none; border: none; }
        .fullscreen-close:hover { color: white; }
        .fullscreen-download { position: absolute; top: 20px; right: 70px; color: white; 
                               font-size: 14px; cursor: pointer; background: rgba(167,139,250,0.4); 
                               padding: 8px 16px; border-radius: 20px; text-decoration: none;
                               border: 1px solid rgba(167,139,250,0.5); }
        .fullscreen-download:hover { background: rgba(167,139,250,0.6); }
        .fullscreen-nav { position: absolute; top: 50%; transform: translateY(-50%);
                          color: white; font-size: 40px; cursor: pointer; background: rgba(0,0,0,0.3);
                          border: none; border-radius: 50%; width: 50px; height: 50px;
                          display: flex; align-items: center; justify-content: center;
                          z-index: 2002; pointer-events: auto; }
        .fullscreen-nav:hover { background: rgba(167,139,250,0.5); }
        .fullscreen-nav.hidden { display: none !important; }
        .edit-side img { cursor: pointer; }
        .fullscreen-prev { left: 20px; }
        .fullscreen-next { right: 20px; }
        .fullscreen-counter { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%);
                              color: white; font-size: 14px; background: rgba(0,0,0,0.5);
                              padding: 6px 16px; border-radius: 20px; }
        
        /* 项目选择 */
        .project-section { margin-bottom: 16px; }
        .project-bar { display: flex; align-items: center; gap: 16px; padding: 12px; flex-wrap: wrap;
                       background: rgba(255,255,255,0.04); border-radius: 12px;
                       border: 1px solid rgba(255,255,255,0.06); }
        .project-bar-field { flex: 1; min-width: 220px; display: flex; align-items: center; gap: 10px; }
        .project-bar label { font-size: 13px; font-weight: 600; color: rgba(255,255,255,0.7); min-width: 72px; flex-shrink: 0; }
        .project-bar select { flex: 1; padding: 8px 12px; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
                               font-size: 13px; background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.8); cursor: pointer; }
        .project-refs-wrap { margin-top: 10px; }
        .project-refs-hint { font-size: 12px; color: rgba(255,255,255,0.4); margin-top: 8px; padding: 0 4px; }
        .project-bar select:focus { outline: none; border-color: rgba(167,139,250,0.5); box-shadow: 0 0 12px rgba(167,139,250,0.15); }
        .project-bar select option { background: #1a1a3e; color: #eee; }
        .project-info { font-size: 12px; color: rgba(255,255,255,0.4); margin-top: 6px; padding-left: 82px; }
        
        .project-images-grid { display: none; flex-wrap: wrap; gap: 8px; margin-top: 8px; padding: 12px; 
            background: rgba(255,255,255,0.03); border-radius: 10px; border: 1px solid rgba(167,139,250,0.2); }
        .project-images-grid.active { display: flex; }
        .project-images-grid .img-thumb-wrap { position: relative; cursor: pointer; }
        .project-images-grid .img-thumb { width: 56px; height: 56px; border-radius: 8px; object-fit: cover;
            border: 2px solid transparent; transition: all 0.2s; }
        .project-images-grid .img-thumb-wrap:hover .img-thumb { border-color: rgba(167,139,250,0.5); transform: scale(1.05); }
        .project-images-grid .img-thumb-wrap.selected .img-thumb { border-color: #a78bfa; box-shadow: 0 0 10px rgba(167,139,250,0.6); }
        .project-images-grid .img-thumb-wrap.selected::after { content: '✓'; position: absolute; top: 50%; left: 50%; 
            transform: translate(-50%, -50%); background: #a78bfa; color: #1a1a3e; border-radius: 50%;
            width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: bold; }
        .project-images-grid .select-hint { font-size: 11px; color: rgba(255,255,255,0.4); width: 100%; text-align: center; }
        
        /* 上传参考图 */
        .upload-ref-section { margin-top: 12px; padding: 14px;
            background: rgba(255,255,255,0.05); border-radius: 12px;
            border: 1px solid rgba(168,85,247,0.3); }
        .upload-ref-label { display: flex; align-items: center; gap: 6px;
            font-size: 13px; color: rgba(255,255,255,0.7); margin-bottom: 10px; }
        .upload-ref-input { width: 100%; padding: 10px;
            background: rgba(255,255,255,0.08);
            border: 1px dashed rgba(168,85,247,0.5);
            border-radius: 8px; color: rgba(255,255,255,0.8);
            font-size: 13px; }
        .uploaded-previews { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
        .uploaded-thumb-wrap { position: relative; }
        .uploaded-thumb { width: 60px; height: 60px; border-radius: 8px;
            object-fit: cover; border: 2px solid rgba(168,85,247,0.5); }
        
        /* 需求表单 */
        .requirement-form { margin-bottom: 16px; padding: 16px; background: rgba(255,255,255,0.03); border-radius: 14px;
                            border: 1px solid rgba(255,255,255,0.05); }
        .requirement-form h3 { font-size: 14px; color: rgba(255,255,255,0.75); margin-bottom: 12px; }
        .form-row { display: flex; gap: 12px; margin-bottom: 10px; }
        .form-item { flex: 1; }
        .form-item.full-width { flex: 2; }
        .form-item label { font-size: 12px; color: rgba(255,255,255,0.5); display: block; margin-bottom: 3px; font-weight: 500; }
        .form-item label .required { color: #f87171; }
        .form-item select, .form-item input[type="text"] { width: 100%; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
                               padding: 8px 10px; font-size: 13px; background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.85); }
        .form-item select:focus, .form-item input[type="text"]:focus { outline: none; border-color: rgba(167,139,250,0.5); box-shadow: 0 0 12px rgba(167,139,250,0.15); }
        .form-item select option { background: #1a1a3e; color: #eee; }
        .form-item input::placeholder { color: rgba(255,255,255,0.25); }
        .size-inputs { display: flex; align-items: center; gap: 6px; }
        .size-inputs input[type="number"] { width: 70px; padding: 6px; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; font-size: 13px;
                                            background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.85); }
        .size-inputs input[type="number"]:focus { outline: none; border-color: rgba(167,139,250,0.5); }
        .size-inputs span { color: rgba(255,255,255,0.3); }
        .ratio-hint { font-size: 11px; color: #a78bfa; font-weight: 500; }
        
        /* 分析按钮 */
        .analyze-btn { width: 100%; padding: 14px; font-size: 15px;
                       border: none; border-radius: 12px; cursor: pointer;
                       background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%);
                       color: white; font-weight: 600; margin-top: 16px;
                       box-shadow: 0 4px 16px rgba(139,92,246,0.3);
                       transition: transform 0.2s, box-shadow 0.2s; }
        .analyze-btn:hover { box-shadow: 0 6px 24px rgba(139,92,246,0.5); transform: translateY(-2px); }
        .analyze-btn:active { transform: translateY(0); }
        .analyze-btn:disabled { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.3); cursor: not-allowed; transform: none; box-shadow: none; }
        
        /* 关键词展示区域 */
        .keyword-section { margin-top: 16px; padding: 16px; background: rgba(139,92,246,0.06); border-radius: 14px;
                           border: 1px solid rgba(139,92,246,0.25); }
        .keyword-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .keyword-header span:first-child { font-size: 14px; font-weight: 600; color: #a78bfa; }
        .keyword-hint { font-size: 12px; color: rgba(255,255,255,0.4); }
        .keyword-textarea { width: 100%; min-height: 80px; padding: 12px; border: 1px solid rgba(255,255,255,0.1); border-radius: 10px;
                            font-size: 14px; line-height: 1.6; resize: vertical; background: rgba(255,255,255,0.04);
                            color: rgba(255,255,255,0.85);
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
        .keyword-textarea:focus { outline: none; border-color: rgba(167,139,250,0.5); box-shadow: 0 0 12px rgba(167,139,250,0.15); }
        
        /* 生成按钮 */
        .generate-btn { width: 100%; padding: 14px; font-size: 15px;
                        border: none; border-radius: 12px; cursor: pointer;
                        background: linear-gradient(135deg, #34d399 0%, #6ee7b7 50%, #a78bfa 100%);
                        color: #0f0c29; font-weight: 700; margin-top: 12px;
                        box-shadow: 0 4px 16px rgba(52,211,153,0.3);
                        transition: transform 0.2s, box-shadow 0.2s; }
        .generate-btn:hover { box-shadow: 0 6px 24px rgba(52,211,153,0.5); transform: translateY(-2px); }
        .generate-btn:active { transform: translateY(0); }
        .generate-btn:disabled { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.3); cursor: not-allowed; transform: none; box-shadow: none; }
        
        /* 加载状态 */
        .loading-card { display: none; text-align: center; padding: 24px; }
        .spinner { display: inline-block; width: 20px; height: 20px; border: 2px solid rgba(255,255,255,0.1);
                   border-top-color: #a78bfa; border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .loading-text { margin-top: 10px; color: rgba(255,255,255,0.5); font-size: 14px; }
        
        /* 变体展示 */
        .variants-section { display: none; margin-top: 16px; }
        .variants-title { font-size: 15px; font-weight: 600; color: rgba(255,255,255,0.75); margin-bottom: 10px; }
        .variants-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }
        .variant-card { background: rgba(255,255,255,0.04); border-radius: 12px; overflow: hidden;
                        cursor: pointer; border: 2px solid rgba(255,255,255,0.06);
                        transition: all 0.25s; }
        .variant-card:hover { transform: translateY(-3px); border-color: rgba(167,139,250,0.3); box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
        .variant-card.selected { border-color: rgba(167,139,250,0.7); box-shadow: 0 0 20px rgba(167,139,250,0.2); }
        .variant-card img { width: 100%; height: 180px; object-fit: cover; display: block; cursor: zoom-in; }
        .variant-card .label { padding: 8px; text-align: center; font-size: 12px; color: rgba(255,255,255,0.5); }
        .variant-card.selected .label { color: #a78bfa; font-weight: 600; }
        
        /* 操作按钮 */
        .actions { display: none; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
        .action-btn { padding: 10px 20px; border: none; border-radius: 10px;
                      font-size: 13px; font-weight: 600; cursor: pointer; transition: 0.25s; }
        .btn-primary { background: linear-gradient(135deg, #8b5cf6, #6366f1); color: white; box-shadow: 0 2px 10px rgba(139,92,246,0.3); }
        .btn-primary:hover { box-shadow: 0 4px 16px rgba(139,92,246,0.5); transform: translateY(-1px); }
        .btn-green { background: linear-gradient(135deg, #34d399, #6ee7b7); color: #0f0c29; font-weight: 700; box-shadow: 0 2px 10px rgba(52,211,153,0.3); }
        .btn-green:hover { box-shadow: 0 4px 16px rgba(52,211,153,0.5); transform: translateY(-1px); }
        .btn-outline { background: transparent; color: rgba(255,255,255,0.7); border: 1px solid rgba(255,255,255,0.15); }
        .btn-outline:hover { border-color: rgba(167,139,250,0.4); color: #a78bfa; background: rgba(167,139,250,0.08); }
        
        .credit { text-align: center; color: rgba(255,255,255,0.3); margin-bottom: 10px; font-size: 13px;
                  font-weight: 500; letter-spacing: 1px; }
        
        /* Tab 导航 */
        .tab-nav { display: flex; align-items: center; gap: 8px; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.1); }
        .tab-btn { flex: 1; padding: 12px 20px; border: none; border-radius: 10px; cursor: pointer;
                   font-size: 15px; font-weight: 600; transition: all 0.2s; background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.5); }
        .tab-btn:hover { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.7); }
        .tab-btn.active { background: linear-gradient(135deg, #8b5cf6, #6366f1); color: white; box-shadow: 0 4px 16px rgba(139,92,246,0.4); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        /* 修图区域 */
        .edit-section { margin-top: 20px; padding: 20px; background: rgba(255,255,255,0.05); border-radius: 12px; }
        .edit-section h3 { color: white; font-size: 16px; margin-bottom: 16px; }
        .edit-type-select { width: 100%; padding: 12px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); 
                        border-radius: 8px; color: white; font-size: 14px; margin-bottom: 16px; }
        .edit-type-select option { background: #1a1a3e; color: #eee; }
        .edit-upload-zone { border: 2px dashed rgba(167,139,250,0.4); border-radius: 12px; padding: 30px; text-align: center; cursor: pointer; transition: 0.2s; }
        .edit-upload-zone:hover { border-color: rgba(167,139,250,0.7); background: rgba(167,139,250,0.05); }
        .edit-upload-zone.dragover { border-color: #a78bfa; background: rgba(167,139,250,0.1); }
        .edit-preview-wrap { margin-top: 16px; text-align: center; position: relative; display: inline-block; }
        .edit-preview-wrap img { max-width: 100%; max-height: 300px; border-radius: 8px; border: 2px solid rgba(167,139,250,0.3); }
        .edit-preview-wrap .edit-remove { position: absolute; top: 8px; right: 8px; width: 28px; height: 28px; border-radius: 50%;
                            background: rgba(239,68,68,0.9); color: white; border: none; cursor: pointer; font-size: 18px; line-height: 1; }
        .edit-desc textarea { width: 100%; min-height: 100px; padding: 12px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15);
                         border-radius: 8px; color: white; font-size: 14px; resize: vertical; font-family: inherit; margin-bottom: 12px; }
        .edit-desc textarea:focus { outline: none; border-color: #667eea; }
        .edit-results { margin-top: 24px; padding: 20px; background: rgba(255,255,255,0.05); border-radius: 12px; }
        .edit-results-title { font-size: 18px; font-weight: 600; color: white; margin-bottom: 16px; text-align: center; }
        .edit-comparison { display: flex; align-items: center; justify-content: center; gap: 20px; flex-wrap: wrap; }
        .edit-side { flex: 1; min-width: 200px; max-width: 350px; text-align: center; }
        .edit-side img { width: 100%; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
        .edit-side-label { font-size: 14px; color: rgba(255,255,255,0.6); margin: 10px 0; }
        .edit-arrow { font-size: 32px; color: #667eea; font-weight: bold; }
        .edit-result-actions { display: flex; gap: 12px; justify-content: center; margin-top: 20px; }
        
        /* 选区工具 */
        .edit-canvas-wrap { position: relative; display: inline-block; margin-top: 16px; }
        .edit-canvas-wrap img { display: block; max-width: 100%; max-height: 350px; border-radius: 8px; }
        .edit-canvas-wrap .canvas-overlay { position: absolute; top: 0; left: 0; cursor: crosshair; }
        .edit-canvas-wrap .selection-rect {
            position: absolute; top: 0; left: 0; border: 2px dashed #a78bfa; background: rgba(167,139,250,0.15);
            pointer-events: none; display: none; box-sizing: border-box;
        }
        .edit-canvas-wrap .selection-rect.active { display: block; }
        .edit-toolbar { display: flex; gap: 8px; justify-content: center; margin-top: 10px; flex-wrap: wrap; }
        .edit-toolbar button { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer;
            font-size: 13px; background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.8); transition: 0.2s; }
        .edit-toolbar button:hover { background: rgba(167,139,250,0.3); }
        .edit-toolbar button.active { background: #a78bfa; color: white; }
        .selection-info { text-align: center; font-size: 12px; color: rgba(255,255,255,0.5); margin-top: 8px; }
        .edit-regions-list { margin-top: 16px; display: flex; flex-direction: column; gap: 10px; }
        .edit-region-item { padding: 12px; background: rgba(167,139,250,0.08); border: 1px solid rgba(167,139,250,0.25); border-radius: 8px; }
        .edit-region-item .region-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .edit-region-item .region-label { font-size: 13px; color: #a78bfa; font-weight: 500; }
        .edit-region-item .region-remove { background: none; border: none; color: rgba(255,255,255,0.5); cursor: pointer; font-size: 16px; padding: 0 4px; }
        .edit-region-item .region-remove:hover { color: #ef4444; }
        .edit-region-item textarea { width: 100%; min-height: 60px; padding: 8px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
            border-radius: 6px; color: white; font-size: 13px; resize: vertical; font-family: inherit; }
        .edit-region-item textarea:focus { outline: none; border-color: #667eea; }
        .edit-canvas-wrap .edit-remove { position: absolute; top: 8px; right: 8px; z-index: 10; }
    </style>
</head>
<body>
    <p class="credit">✦ AI 视觉设计助手 v4 ✦</p>
    <div class="container">
        <!-- Tab 导航 -->
        <div class="tab-nav">
            <button class="tab-btn active" onclick="switchTab('generate')">🖼️ 生图</button>
            <button class="tab-btn" onclick="switchTab('edit')">✏️ 修图</button>
            <button type="button" class="history-btn" onclick="toggleHistory()">📋 历史记录</button>
        </div>
        
        <div class="card tab-content active" id="generateTab">
            <h1>🎨 AI 视觉设计助手</h1>
            <p class="subtitle">填写需求，一键生成多张变体</p>
            
            <!-- 项目组 + 设计类型 -->
            <div class="project-section">
                <div class="project-bar">
                    <div class="project-bar-field">
                        <label>📁 项目组</label>
                        <select id="projectSelect" onchange="onProjectOrDesignTypeChange()">
                            <option value="">请选择项目组</option>
                        </select>
                    </div>
                    <div class="project-bar-field">
                        <label>设计类型</label>
                        <select id="designType" onchange="onDesignTypeChange()">
                            <option value="">请选择设计类型</option>
                            <option value="海报">海报</option>
                            <option value="传单">传单</option>
                            <option value="Banner">Banner</option>
                            <option value="朋友圈图">朋友圈图</option>
                            <option value="公众号封面">公众号封面</option>
                            <option value="PPT封面">PPT封面</option>
                            <option value="其他">其他</option>
                        </select>
                    </div>
                </div>
                <p class="project-refs-hint" id="projectRefsHint">请先选择项目组和设计类型，再挑选参考图</p>
                <div class="project-refs-wrap" id="projectRefsWrap" style="display: none;">
                    <div class="project-info" id="projectInfo"></div>
                    <div class="project-images-grid" id="projectImagesGrid"></div>
                </div>
            </div>
            
            <!-- 上传参考图 -->
            <div class="upload-ref-section">
                <div class="upload-ref-label">
                    <span>🖼️ 上传参考图（可选）</span>
                    <span style="opacity:0.6;font-size:12px;">最多3张，不影响生成</span>
                </div>
                <input type="file" id="refImagesInput" class="upload-ref-input" accept="image/*" multiple>
                <div class="uploaded-previews" id="uploadedPreviews"></div>
            </div>
            
            <!-- 需求表单 -->
            <div class="requirement-form">
                <h3>📝 填写设计需求</h3>
                
                <div class="form-row">
                    <div class="form-item full-width">
                        <label>主标题 <span class="required">*</span></label>
                        <input type="text" id="mainTitle" placeholder="如：暑期班火热招生中">
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-item">
                        <label>副标题</label>
                        <input type="text" id="subTitle" placeholder="如：限时优惠 前50名8折">
                    </div>
                    <div class="form-item">
                        <label>画面描述</label>
                        <input type="text" id="visualDesc" placeholder="如：蓝天白云，卡通儿童奔跑">
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-item">
                        <label>风格</label>
                        <select id="styleSelect" onchange="toggleCustomStyle()">
                            <option value="">默认</option>
                            <option value="简约">简约</option>
                            <option value="卡通">卡通</option>
                            <option value="中国风">中国风</option>
                            <option value="科技感">科技感</option>
                            <option value="可爱">可爱</option>
                            <option value="商务">商务</option>
                            <option value="复古">复古</option>
                            <option value="潮流">潮流</option>
                            <option value="custom">自定义</option>
                        </select>
                    </div>
                    <div class="form-item" id="customStyleInput" style="display:none;">
                        <label>自定义风格</label>
                        <input type="text" id="customStyle" placeholder="如：赛博朋克、国潮">
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-item full-width">
                        <label>排版参考</label>
                        <input type="text" id="layoutRef" placeholder="如：标题居中顶部，正文底部左对齐">
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-item">
                        <label>输出尺寸</label>
                        <select id="ratioSelect" onchange="toggleCustomSize()">
                            <option value="1:1">1:1 (方形)</option>
                            <option value="16:9">16:9 (宽屏)</option>
                            <option value="9:16">9:16 (竖版)</option>
                            <option value="4:3">4:3 (横版)</option>
                            <option value="3:4">3:4 (竖版)</option>
                            <option value="custom">自定义</option>
                        </select>
                    </div>
                    <div class="form-item" id="customSizeInputs" style="display:none;">
                        <label>宽 × 高 (px)</label>
                        <div class="size-inputs">
                            <input type="number" id="customWidth" placeholder="宽" min="100" max="4096">
                            <span>×</span>
                            <input type="number" id="customHeight" placeholder="高" min="100" max="4096">
                            <span id="matchedRatio" class="ratio-hint"></span>
                        </div>
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-item full-width">
                        <label>补充备注</label>
                        <input type="text" id="extraNotes" placeholder="如：品牌色 #FF6B6B，LOGO 左上角">
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-item full-width">
                        <label>生图模型</label>
                        <select id="imageBackendSelect">
                            <option value="lovart">Lovart 龙虾</option>
                            <option value="comfyui">ComfyUI</option>
                            <option value="stable_diffusion">Stable Diffusion</option>
                        </select>
                    </div>
                </div>
                
                <!-- 分析需求按钮 -->
                <button class="analyze-btn" onclick="analyzeKeyword()">🔍 AI 分析关键词</button>
                
                <!-- 关键词展示区域（默认隐藏） -->
                <div class="keyword-section" id="keywordSection" style="display:none">
                    <div class="keyword-header">
                        <span>📝 AI 分析的关键词</span>
                        <span class="keyword-hint">可手动修改后生成</span>
                    </div>
                    <textarea id="keywordInput" class="keyword-textarea" placeholder="点击上方按钮生成关键词..."></textarea>
                    <button class="generate-btn" onclick="generateWithKeyword()">✨ 生成魔法图</button>
                </div>
            </div>
            
            <!-- 加载中 -->
            <div class="loading-card" id="loadingCard">
                <div class="spinner"></div>
                <p class="loading-text">AI 正在创作变体中...约需 1-2 分钟</p>
            </div>
            
            <!-- 变体展示 -->
            <div class="variants-section" id="variantsSection">
                <div class="variants-title">🎨 选择满意的一张</div>
                <div class="variants-grid" id="variantsGrid"></div>
            </div>
            
            <!-- 操作按钮 -->
            <div class="actions" id="actionsSection">
                <button class="action-btn btn-primary" onclick="downloadSelected()">💾 下载选中</button>
                <button class="action-btn btn-green" onclick="upscaleSelected('2k')">🔍 2K 放大</button>
                <button class="action-btn btn-outline" onclick="resetAll()">🔄 重新开始</button>
            </div>
        </div>
        
        <!-- 修图标签页内容 -->
        <div class="tab-content" id="editTab">
            <div class="edit-section">
                <h3>✏️ 图片局部修改</h3>
                
                <!-- 修图类型 -->
                <select class="edit-type-select" id="editType">
                    <option value="文案修改">📝 文案修改</option>
                    <option value="颜色调整">🎨 颜色调整</option>
                    <option value="元素替换">🔄 元素替换</option>
                    <option value="布局调整">📐 布局调整</option>
                    <option value="风格转换">✨ 风格转换</option>
                    <option value="背景替换">🏞️ 背景替换</option>
                </select>
                
                <!-- 上传图片 -->
                <div class="edit-upload-zone" id="editUploadZone" onclick="document.getElementById('editImageInput').click()">
                    <p style="color: rgba(255,255,255,0.6);">🖼️ 点击上传要修改的图片</p>
                    <p style="color: rgba(255,255,255,0.4); font-size: 12px; margin-top: 8px;">支持 PNG、JPG、WebP</p>
                </div>
                <input type="file" id="editImageInput" accept="image/*" style="display: none;" onchange="previewEditImage(event)">
                
                <!-- 图片预览与选区绘制 -->
                <div class="edit-canvas-wrap" id="editCanvasWrap" style="display: none;">
                    <img id="editPreviewImg" alt="待修图预览">
                    <canvas class="canvas-overlay" id="editSelectionCanvas"></canvas>
                    <div class="selection-rect" id="editSelectionRect"></div>
                    <button class="edit-remove" type="button" onclick="removeEditImage()" title="移除图片">×</button>
                </div>
                <div class="edit-toolbar" id="editToolbar" style="display: none;">
                    <button type="button" onclick="setDrawMode('rect')" id="btnRect" class="active">▢ 框选区域</button>
                    <button type="button" onclick="confirmCurrentSelection()" id="btnConfirmRegion">✓ 确认选区</button>
                    <button type="button" onclick="clearCurrentSelection()">✕ 清除当前</button>
                </div>
                <div class="selection-info" id="selectionInfo" style="display: none;">框选要修改的区域并确认；仅修改选区内内容，原图尺寸与选区外样式保持不变</div>
                <div class="edit-regions-list" id="editRegionsList"></div>
                
                <!-- 修改描述 -->
                <div class="edit-desc" style="margin-top: 16px;">
                    <label style="font-size: 13px; color: rgba(255,255,255,0.7); margin-bottom: 8px; display: block;">📝 修改要求</label>
                    <textarea id="editDescription" placeholder="例如：把文字改成'欢庆六一'，保持其他部分不变（框选区域后也可在各选区中单独填写）"></textarea>
                </div>
                
                <!-- 保留元素 -->
                <div class="edit-desc" style="margin-top: 12px;">
                    <label style="font-size: 13px; color: rgba(255,255,255,0.7); margin-bottom: 8px; display: block;">🎯 需要保留的元素（可选）</label>
                    <input type="text" id="keepElements" placeholder="例如：背景颜色、人物姿态" 
                           style="width: 100%; padding: 10px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); 
                           border-radius: 8px; color: white; font-size: 14px;">
                </div>
                
                <!-- 开始修图按钮 -->
                <button class="generate-btn" onclick="startEditImage()" style="margin-top: 20px;">✨ AI 智能修图</button>
                
                <!-- 加载状态 -->
                <div class="loading-card" id="editLoadingCard" style="display: none;">
                    <div class="spinner"></div>
                    <p class="loading-text">AI 正在修图，预计需要 1-2 分钟...</p>
                </div>
                
                <!-- 结果展示 -->
                <div class="edit-results" id="editResultsSection" style="display: none;">
                    <div class="edit-results-title">🎉 修图完成！</div>
                    <div class="edit-comparison">
                        <div class="edit-side">
                            <div class="edit-side-label">原图</div>
                            <img id="editOriginalImg" onclick="openEditComparisonFullscreen(0)" title="点击查看大图">
                        </div>
                        <div class="edit-arrow">→</div>
                        <div class="edit-side">
                            <div class="edit-side-label">修改后</div>
                            <img id="editResultImg" onclick="openEditComparisonFullscreen(1)" title="点击查看大图">
                        </div>
                    </div>
                    <div class="edit-result-actions">
                        <button class="action-btn btn-primary" onclick="downloadEditResult()">💾 下载结果</button>
                        <button class="action-btn btn-outline" onclick="resetEdit()">🔄 重新修图</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    </div>
    
    <!-- 历史记录侧边抽屉 -->
    <div class="drawer-overlay" id="drawerOverlay" onclick="toggleHistory()"></div>
    <div class="history-drawer" id="historyDrawer">
        <div class="drawer-header">
            <span class="drawer-title">📋 历史记录</span>
            <button class="drawer-close" onclick="toggleHistory()">×</button>
        </div>
        <div class="drawer-content" id="historyList"></div>
    </div>
    
    <!-- 全屏图片查看 -->
    <div class="fullscreen-overlay" id="fullscreenOverlay" onclick="closeFullscreen()">
        <button class="fullscreen-close" onclick="event.stopPropagation(); closeFullscreen()">×</button>
        <a class="fullscreen-download" id="fullscreenDownload" download href="#" onclick="event.stopPropagation()">⬇ 下载图片</a>
        <img class="fullscreen-img" id="fullscreenImg">
        <button class="fullscreen-nav fullscreen-prev" onclick="event.stopPropagation(); prevImage()">‹</button>
        <button class="fullscreen-nav fullscreen-next" onclick="event.stopPropagation(); nextImage()">›</button>
        <div class="fullscreen-counter" id="fullscreenCounter"></div>
    </div>
    
<script>
var currentProject = null;
var selectedProjectImages = [];
var selectedVariant = null;
var variants = [];
var editImageData = null;

// Tab 切换
function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById(tab + 'Tab').classList.add('active');
}

// 初始化
window.onload = function() {
    updateProjectRefsVisibility();
    initImageBackendSelect();
    loadProjects();
    loadHistory();
};

function initImageBackendSelect() {
    var sel = document.getElementById('imageBackendSelect');
    if (!sel) return;
    var saved = localStorage.getItem('imageBackendSelect');
    if (saved) sel.value = saved;
    sel.addEventListener('change', function() {
        localStorage.setItem('imageBackendSelect', sel.value);
    });
}

function getSelectedImageBackend() {
    var sel = document.getElementById('imageBackendSelect');
    return sel ? sel.value : 'lovart';
}

var DESIGN_TYPE_RATIO_DEFAULTS = {
    '海报': '9:16',
    '传单': '9:16',
    '朋友圈图': '1:1',
    '公众号封面': '16:9',
    'Banner': '16:9',
    'PPT封面': '16:9'
};

function shouldShowProjectRefs() {
    var project = document.getElementById('projectSelect').value;
    var designType = document.getElementById('designType').value;
    return !!(project && designType);
}

function updateProjectRefsVisibility() {
    var show = shouldShowProjectRefs();
    var wrap = document.getElementById('projectRefsWrap');
    var hint = document.getElementById('projectRefsHint');
    if (wrap) wrap.style.display = show ? 'block' : 'none';
    if (hint) hint.style.display = show ? 'none' : 'block';
    if (!show) clearProjectRefs();
}

function clearProjectRefs() {
    currentProject = null;
    selectedProjectImages = [];
    var info = document.getElementById('projectInfo');
    var grid = document.getElementById('projectImagesGrid');
    if (info) info.textContent = '';
    if (grid) {
        grid.innerHTML = '';
        grid.classList.remove('active');
    }
}

function onDesignTypeChange() {
    applyDefaultRatioForDesignType();
    var designType = document.getElementById('designType').value;
    if (designType === '其他') {
        document.getElementById('styleSelect').value = 'custom';
        toggleCustomStyle();
    }
    onProjectOrDesignTypeChange();
}

function onProjectOrDesignTypeChange() {
    updateProjectRefsVisibility();
    if (!shouldShowProjectRefs()) return;
    selectProject();
}

function applyDefaultRatioForDesignType() {
    var designType = document.getElementById('designType').value;
    if (!designType) return;
    var ratio = DESIGN_TYPE_RATIO_DEFAULTS[designType];
    if (!ratio) return;
    var ratioSel = document.getElementById('ratioSelect');
    var hasOption = Array.prototype.some.call(ratioSel.options, function(opt) {
        return opt.value === ratio;
    });
    if (!hasOption) return;
    ratioSel.value = ratio;
    toggleCustomSize();
}

// 加载项目列表
async function loadProjects() {
    try {
        var res = await fetch('/projects');
        var data = await res.json();
        var sel = document.getElementById('projectSelect');
        sel.innerHTML = '<option value="">请选择项目组</option>';
        (data.projects || []).forEach(function(p) {
            var opt = document.createElement('option');
            opt.value = p.name;
            opt.textContent = p.display_name + ' (' + p.count + '张参考)';
            opt.dataset.tags = (p.style_tags || []).join(', ');
            opt.dataset.desc = p.description || '';
            sel.appendChild(opt);
        });
    } catch(e) {
        console.error('加载项目失败', e);
    }
}

// 选择项目
function selectProject() {
    if (!shouldShowProjectRefs()) {
        clearProjectRefs();
        return;
    }
    var sel = document.getElementById('projectSelect');
    var opt = sel.selectedOptions[0];
    var grid = document.getElementById('projectImagesGrid');
    selectedProjectImages = [];
    
    if (opt && opt.value) {
        currentProject = {
            name: opt.value,
            tags: opt.dataset.tags,
            desc: opt.dataset.desc
        };
        document.getElementById('projectInfo').textContent = 
            (currentProject.tags ? '风格标签：' + currentProject.tags + '。' : '') +
            (currentProject.desc || '');
        // 加载项目图片
        loadProjectImages(opt.value, grid);
    } else {
        clearProjectRefs();
    }
}

// 加载项目图片
async function loadProjectImages(projectName, grid) {
    try {
        var res = await fetch('/projects/' + encodeURIComponent(projectName) + '/images');
        var data = await res.json();
        var images = data.images || [];
        
        if (images.length === 0) {
            grid.innerHTML = '<span class="select-hint">该项目暂无参考图</span>';
            grid.classList.add('active');
            return;
        }
        
        grid.innerHTML = '<span class="select-hint">点击选择参考图（最多10张）：</span>';
        grid.classList.add('active');
        
        images.forEach(function(imgName) {
            var wrap = document.createElement('div');
            wrap.className = 'img-thumb-wrap';
            wrap.dataset.name = imgName;
            wrap.onclick = function() { toggleProjectImage(this, projectName, imgName); };
            
            var img = document.createElement('img');
            img.className = 'img-thumb';
            img.src = '/projects/' + projectName + '/images/' + imgName;
            img.onerror = function() { this.style.display = 'none'; };
            
            wrap.appendChild(img);
            grid.appendChild(wrap);
        });
    } catch(e) {
        console.error('加载项目图片失败', e);
    }
}

// 切换项目图片选择状态
function toggleProjectImage(el, projectName, imgName) {
    var idx = selectedProjectImages.indexOf(projectName + '/' + imgName);
    if (idx >= 0) {
        selectedProjectImages.splice(idx, 1);
        el.classList.remove('selected');
    } else {
        if (selectedProjectImages.length >= 10) {
            alert('最多选择10张参考图');
            return;
        }
        selectedProjectImages.push(projectName + '/' + imgName);
        el.classList.add('selected');
    }
}

// 上传参考图处理
var uploadedRefImages = [];
document.getElementById('refImagesInput').addEventListener('change', function(e) {
    var files = e.target.files;
    var maxFiles = 3;
    var remaining = maxFiles - uploadedRefImages.length;
    if (files.length > remaining) {
        alert('最多上传' + maxFiles + '张参考图，已选择' + files.length + '张，剩余' + remaining + '张名额');
    }
    for (var i = 0; i < Math.min(files.length, remaining); i++) {
        (function(file) {
            var reader = new FileReader();
            reader.onload = function(evt) {
                uploadedRefImages.push(evt.target.result);
                renderUploadedPreviews();
            };
            reader.readAsDataURL(file);
        })(files[i]);
    }
    e.target.value = '';
});

function renderUploadedPreviews() {
    var container = document.getElementById('uploadedPreviews');
    container.innerHTML = '';
    for (var i = 0; i < uploadedRefImages.length; i++) {
        (function(idx, dataUrl) {
            var wrap = document.createElement('div');
            wrap.className = 'uploaded-thumb-wrap';
            var img = document.createElement('img');
            img.className = 'uploaded-thumb';
            img.src = dataUrl;
            var btn = document.createElement('div');
            btn.className = 'uploaded-thumb-remove';
            btn.textContent = '×';
            btn.onclick = function() {
                uploadedRefImages.splice(idx, 1);
                renderUploadedPreviews();
            };
            wrap.appendChild(img);
            wrap.appendChild(btn);
            container.appendChild(wrap);
        })(i, uploadedRefImages[i]);
    }
}

// 自定义尺寸切换
function toggleCustomSize() {
    var sel = document.getElementById('ratioSelect');
    var customInputs = document.getElementById('customSizeInputs');
    if (sel.value === 'custom') {
        customInputs.style.display = 'block';
    } else {
        customInputs.style.display = 'none';
    }
}

// 自定义风格切换
function toggleCustomStyle() {
    var sel = document.getElementById('styleSelect');
    var customInput = document.getElementById('customStyleInput');
    if (sel.value === 'custom') {
        customInput.style.display = 'block';
    } else {
        customInput.style.display = 'none';
    }
}

// 获取风格值
function getStyleValue() {
    var sel = document.getElementById('styleSelect');
    if (sel.value === 'custom') {
        return document.getElementById('customStyle').value.trim();
    }
    return sel.value;
}

// 匹配最接近的预设比例
var RATIO_MAP = [
    {ratio: '21:9', value: 21/9},
    {ratio: '16:9', value: 16/9},
    {ratio: '4:3', value: 4/3},
    {ratio: '3:2', value: 3/2},
    {ratio: '1:1', value: 1},
    {ratio: '4:5', value: 4/5},
    {ratio: '3:4', value: 3/4},
    {ratio: '2:3', value: 2/3},
    {ratio: '9:16', value: 9/16}
];

function updateMatchedRatio() {
    var w = parseInt(document.getElementById('customWidth').value) || 0;
    var h = parseInt(document.getElementById('customHeight').value) || 0;
    var hint = document.getElementById('matchedRatio');
    
    if (w > 0 && h > 0) {
        var inputRatio = w / h;
        var closest = RATIO_MAP.reduce(function(prev, curr) {
            return Math.abs(curr.value - inputRatio) < Math.abs(prev.value - inputRatio) ? curr : prev;
        });
        hint.textContent = '→ 匹配 ' + closest.ratio;
        hint.dataset.ratio = closest.ratio;
    } else {
        hint.textContent = '';
        hint.dataset.ratio = '';
    }
}

document.getElementById('customWidth').addEventListener('input', updateMatchedRatio);
document.getElementById('customHeight').addEventListener('input', updateMatchedRatio);

// 分析关键词
async function analyzeKeyword() {
    var designType = document.getElementById('designType').value;
    var mainTitle = document.getElementById('mainTitle').value.trim();
    
    if (!mainTitle) {
        alert('请填写主标题');
        return;
    }
    
    var summary = {
        '设计类型': designType,
        '主标题': mainTitle,
        '副标题': document.getElementById('subTitle').value.trim(),
        '画面描述': document.getElementById('visualDesc').value.trim(),
        '排版参考': document.getElementById('layoutRef').value.trim(),
        '风格': getStyleValue(),
        '补充备注': document.getElementById('extraNotes').value.trim()
    };
    
    document.getElementById('loadingCard').style.display = 'block';
    document.querySelector('.loading-text').textContent = 'AI 正在分析关键词...';
    document.querySelector('.analyze-btn').disabled = true;
    
    try {
        var formData = new FormData();
        formData.append('summary', JSON.stringify(summary));
        formData.append('project', currentProject ? currentProject.name : '');
        
        var res = await fetch('/api/analyze', { method: 'POST', body: formData });
        var data = await res.json();
        
        if (data.error) {
            alert('分析失败: ' + data.error);
            return;
        }
        
        // 显示关键词区域，填充内容
        document.getElementById('keywordSection').style.display = 'block';
        document.getElementById('keywordInput').value = data.prompt || '';
        
        // 隐藏分析按钮，显示关键词区域
        document.querySelector('.analyze-btn').style.display = 'none';
        
    } catch(e) {
        alert('分析请求失败: ' + e.message);
    }
    
    document.getElementById('loadingCard').style.display = 'none';
    document.querySelector('.analyze-btn').disabled = false;
}

// 使用关键词生成图片
async function generateWithKeyword() {
    var prompt = document.getElementById('keywordInput').value.trim();
    
    if (!prompt) {
        alert('请先分析关键词或手动填写');
        return;
    }
    
    // 处理尺寸
    var ratioSel = document.getElementById('ratioSelect');
    var ratio = ratioSel.value;
    if (ratio === 'custom') {
        var hint = document.getElementById('matchedRatio');
        ratio = hint.dataset.ratio || '1:1';
    }
    
    document.getElementById('loadingCard').style.display = 'block';
    document.querySelector('.loading-text').textContent = 'AI 正在创作变体中...约需 1-2 分钟';
    document.querySelector('.generate-btn').disabled = true;
    
    var formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('project', currentProject ? currentProject.name : '');
    formData.append('selected_project_images', JSON.stringify(selectedProjectImages));
    formData.append('count', '3');
    formData.append('ratio', ratio);
    formData.append('image_backend', getSelectedImageBackend());
    // 添加上传的参考图
    for (var i = 0; i < uploadedRefImages.length; i++) {
        formData.append('ref_image_' + i, uploadedRefImages[i]);
    }
    
    try {
        var res = await fetch('/generate-with-prompt', { method: 'POST', body: formData });
        var data = await res.json();
        
        if (data.error) {
            alert('生成失败: ' + data.error);
            return;
        }
        
        variants = data.variants || [];
        renderVariants(variants);
        
        document.getElementById('variantsSection').style.display = 'block';
        document.getElementById('actionsSection').style.display = 'flex';
        loadHistory();
        
    } catch(e) {
        alert('生成请求失败: ' + e.message);
    }
    
    document.getElementById('loadingCard').style.display = 'none';
    document.querySelector('.generate-btn').disabled = false;
}

// 生成变体（保留旧函数兼容）
async function generateVariants() {
    var designType = document.getElementById('designType').value;
    var mainTitle = document.getElementById('mainTitle').value.trim();
    
    if (!mainTitle) {
        alert('请填写主标题');
        return;
    }
    
    var summary = {
        '设计类型': designType,
        '主标题': mainTitle,
        '副标题': document.getElementById('subTitle').value.trim(),
        '画面描述': document.getElementById('visualDesc').value.trim(),
        '排版参考': document.getElementById('layoutRef').value.trim(),
        '风格': getStyleValue(),
        '补充备注': document.getElementById('extraNotes').value.trim()
    };
    
    // 处理尺寸
    var ratioSel = document.getElementById('ratioSelect');
    var ratio = ratioSel.value;
    if (ratio === 'custom') {
        var hint = document.getElementById('matchedRatio');
        ratio = hint.dataset.ratio || '1:1';
    }
    
    document.getElementById('loadingCard').style.display = 'block';
    document.querySelector('.generate-btn').disabled = true;
    
    var formData = new FormData();
    formData.append('summary', JSON.stringify(summary));
    formData.append('project', currentProject ? currentProject.name : '');
    formData.append('selected_project_images', JSON.stringify(selectedProjectImages));
    formData.append('count', '3');
    formData.append('ratio', ratio);
    formData.append('image_backend', getSelectedImageBackend());
    
    try {
        var res = await fetch('/generate-variants', { method: 'POST', body: formData });
        var data = await res.json();
        
        if (data.error) {
            alert('生成失败: ' + data.error);
            return;
        }
        
        variants = data.variants || [];
        renderVariants(variants);
        
        document.getElementById('variantsSection').style.display = 'block';
        document.getElementById('actionsSection').style.display = 'flex';
        loadHistory();
        
    } catch(e) {
        alert('生成请求失败: ' + e.message);
    }
    
    document.getElementById('loadingCard').style.display = 'none';
    document.querySelector('.generate-btn').disabled = false;
}

// 渲染变体
function renderVariants(vars) {
    var grid = document.getElementById('variantsGrid');
    var valid = (vars || []).filter(function(v) { return v && v.filename; });

    if (!valid.length) {
        var errorMsg = (vars || []).map(function(v) { return v && v.error; }).filter(Boolean)[0] || '没有成功生成任何图片';
        grid.innerHTML = '<div class="variant-error" style="grid-column:1/-1;padding:16px;border-radius:12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.35);color:#fecaca;line-height:1.6;">' +
            '生成失败：' + errorMsg +
            '</div>';
        return;
    }

    var allFilenames = valid.map(function(v) { return '/outputs/' + v.filename; });
    var allUrlsEnc = encodeURIComponent(JSON.stringify(allFilenames));
    grid.innerHTML = valid.map(function(v, i) {
        return '<div class="variant-card" onclick="selectVariant(' + i + ')" id="variant-' + i + '">' +
            '<img src="/outputs/' + v.filename + '" loading="lazy" data-images="' + allUrlsEnc + '" data-index="' + i + '" onclick="event.stopPropagation(); openHistoryThumb(this)">' +
            '<div class="label">魔法图 ' + (i + 1) + '</div>' +
            '</div>';
    }).join('');
    variants = valid;
}

// 选择变体
function selectVariant(idx) {
    selectedVariant = idx;
    document.querySelectorAll('.variant-card').forEach(function(el, i) {
        el.classList.toggle('selected', i === idx);
    });
}

// 下载选中
function downloadSelected() {
    if (selectedVariant === null) {
        alert('请先选择一张图片');
        return;
    }
    var v = variants[selectedVariant];
    window.open('/outputs/' + v.filename, '_blank');
}

// 放大选中
async function upscaleSelected(resolution) {
    if (selectedVariant === null) {
        alert('请先选择一张图片');
        return;
    }
    var v = variants[selectedVariant];
    
    document.getElementById('loadingCard').style.display = 'block';
    document.querySelector('.loading-text').textContent = '放大中 ' + resolution + '...';
    
    try {
        var res = await fetch('/upscale', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({filename: v.filename, resolution: resolution})
        });
        var data = await res.json();
        
        if (data.error) {
            alert('放大失败: ' + data.error);
        } else {
            variants[selectedVariant].filename = data.output_image;
            renderVariants(variants);
            selectVariant(selectedVariant);
            alert(resolution + ' 放大完成！');
            loadHistory();
        }
    } catch(e) {
        alert('放大请求失败: ' + e.message);
    }
    
    document.getElementById('loadingCard').style.display = 'none';
    document.querySelector('.loading-text').textContent = 'AI 正在创作变体中...约需 1-2 分钟';
}

// 修图选区
var editRegions = [];
var editRegionIdCounter = 0;
var isDrawing = false;
var startX, startY, selectionRect = null;

function previewEditImage(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        editImageData = e.target.result;
        const img = document.getElementById('editPreviewImg');
        img.onload = function() { initEditCanvas(); };
        img.src = e.target.result;
        document.getElementById('editCanvasWrap').style.display = 'inline-block';
        document.getElementById('editUploadZone').style.display = 'none';
        document.getElementById('editToolbar').style.display = 'flex';
        document.getElementById('selectionInfo').style.display = 'block';
        editRegions = [];
        editRegionIdCounter = 0;
        document.getElementById('editRegionsList').innerHTML = '';
        clearCurrentSelection();
    };
    reader.readAsDataURL(file);
}

function initEditCanvas() {
    const img = document.getElementById('editPreviewImg');
    const canvas = document.getElementById('editSelectionCanvas');
    if (!img || !canvas) return;
    const w = img.clientWidth;
    const h = img.clientHeight;
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    canvas.onmousedown = startSelection;
    canvas.onmousemove = drawSelection;
    canvas.onmouseup = endSelection;
    canvas.onmouseleave = endSelection;
    renderConfirmedRegions();
}

function getEventPos(e) {
    const canvas = document.getElementById('editSelectionCanvas');
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}

function startSelection(e) {
    e.preventDefault();
    const pos = getEventPos(e);
    startX = pos.x;
    startY = pos.y;
    isDrawing = true;
    selectionRect = { x: startX, y: startY, w: 0, h: 0 };
}

function drawSelection(e) {
    if (!isDrawing) return;
    const pos = getEventPos(e);
    const x = Math.min(startX, pos.x);
    const y = Math.min(startY, pos.y);
    const w = Math.abs(pos.x - startX);
    const h = Math.abs(pos.y - startY);
    const rectEl = document.getElementById('editSelectionRect');
    rectEl.style.left = x + 'px';
    rectEl.style.top = y + 'px';
    rectEl.style.width = w + 'px';
    rectEl.style.height = h + 'px';
    if (w > 5 && h > 5) {
        rectEl.classList.add('active');
        selectionRect = { x, y, w, h };
        updateSelectionInfo(x, y, w, h);
    }
}

function endSelection(e) {
    isDrawing = false;
}

function clearCurrentSelection() {
    const rectEl = document.getElementById('editSelectionRect');
    if (rectEl) {
        rectEl.classList.remove('active');
        rectEl.style.width = '0';
        rectEl.style.height = '0';
    }
    selectionRect = null;
    updateSelectionInfo(0, 0, 0, 0);
}

function updateSelectionInfo(x, y, w, h) {
    const info = document.getElementById('selectionInfo');
    if (!info) return;
    if (w > 0 && h > 0) {
        info.textContent = '当前选区: ' + Math.round(x) + ',' + Math.round(y) + ' ' + Math.round(w) + 'x' + Math.round(h) + ' — 点击「确认选区」添加';
    } else {
        info.textContent = '框选要修改的区域并确认；仅修改选区内内容，原图尺寸与选区外样式保持不变';
    }
}

function displayToNaturalCoords(rect) {
    const img = document.getElementById('editPreviewImg');
    const scaleX = img.naturalWidth / img.clientWidth;
    const scaleY = img.naturalHeight / img.clientHeight;
    return {
        x: Math.round(rect.x * scaleX),
        y: Math.round(rect.y * scaleY),
        w: Math.round(rect.w * scaleX),
        h: Math.round(rect.h * scaleY)
    };
}

function naturalToDisplayCoords(coords) {
    const img = document.getElementById('editPreviewImg');
    const scaleX = img.clientWidth / img.naturalWidth;
    const scaleY = img.clientHeight / img.naturalHeight;
    return {
        x: coords.x * scaleX,
        y: coords.y * scaleY,
        w: coords.w * scaleX,
        h: coords.h * scaleY
    };
}

function confirmCurrentSelection() {
    if (!selectionRect || selectionRect.w < 10 || selectionRect.h < 10) {
        alert('请先在图片上框选足够大的区域');
        return;
    }
    const natural = displayToNaturalCoords(selectionRect);
    const id = 'region_' + (++editRegionIdCounter);
    editRegions.push({
        id: id,
        x: natural.x,
        y: natural.y,
        w: natural.w,
        h: natural.h,
        description: ''
    });
    renderConfirmedRegions();
    clearCurrentSelection();
}

function renderConfirmedRegions() {
    const wrap = document.getElementById('editCanvasWrap');
    if (!wrap) return;
    wrap.querySelectorAll('.region-marker').forEach(function(el) { el.remove(); });
    editRegions.forEach(function(region, idx) {
        const disp = naturalToDisplayCoords(region);
        const marker = document.createElement('div');
        marker.className = 'selection-rect region-marker active';
        marker.style.left = disp.x + 'px';
        marker.style.top = disp.y + 'px';
        marker.style.width = disp.w + 'px';
        marker.style.height = disp.h + 'px';
        marker.style.borderColor = '#34d399';
        marker.style.background = 'rgba(52,211,153,0.12)';
        marker.title = '选区 ' + (idx + 1);
        wrap.appendChild(marker);
    });
    const list = document.getElementById('editRegionsList');
    list.innerHTML = '';
    editRegions.forEach(function(region, idx) {
        const item = document.createElement('div');
        item.className = 'edit-region-item';
        item.innerHTML = '<div class="region-header"><span class="region-label">选区 ' + (idx + 1) + ' (' + region.w + '×' + region.h + ')</span>' +
            '<button type="button" class="region-remove" onclick="removeEditRegion(\'' + region.id + '\')" title="删除选区">×</button></div>' +
            '<textarea id="desc_' + region.id + '" placeholder="描述此区域的修改要求，例如：把文字改成「欢庆六一」"></textarea>';
        list.appendChild(item);
        const ta = item.querySelector('textarea');
        ta.value = region.description || '';
        ta.oninput = function() { region.description = ta.value; };
    });
}

function removeEditRegion(id) {
    editRegions = editRegions.filter(function(r) { return r.id !== id; });
    renderConfirmedRegions();
}

function setDrawMode(mode) {
    document.getElementById('btnRect').classList.add('active');
}

function getEditPayloadRegions() {
    const globalDesc = document.getElementById('editDescription').value.trim();
    if (editRegions.length > 0) {
        return editRegions.map(function(r) {
            var descEl = document.getElementById('desc_' + r.id);
            var desc = (descEl ? descEl.value : r.description || '').trim() || globalDesc;
            return { x: r.x, y: r.y, w: r.w, h: r.h, description: desc };
        }).filter(function(r) { return r.description; });
    }
    if (selectionRect && selectionRect.w >= 10 && selectionRect.h >= 10) {
        const natural = displayToNaturalCoords(selectionRect);
        if (globalDesc) {
            return [{ x: natural.x, y: natural.y, w: natural.w, h: natural.h, description: globalDesc }];
        }
    }
    return [];
}

function removeEditImage() {
    editImageData = null;
    editRegions = [];
    document.getElementById('editCanvasWrap').style.display = 'none';
    document.getElementById('editToolbar').style.display = 'none';
    document.getElementById('selectionInfo').style.display = 'none';
    document.getElementById('editRegionsList').innerHTML = '';
    document.getElementById('editUploadZone').style.display = 'block';
    document.getElementById('editImageInput').value = '';
    document.getElementById('editResultsSection').style.display = 'none';
    clearCurrentSelection();
}

function compressImageDataUrl(dataUrl, maxDim, quality) {
    return new Promise(function(resolve, reject) {
        var img = new Image();
        img.onload = function() {
            var w = img.naturalWidth || img.width;
            var h = img.naturalHeight || img.height;
            var scale = Math.min(1, maxDim / Math.max(w, h, 1));
            var cw = Math.max(1, Math.round(w * scale));
            var ch = Math.max(1, Math.round(h * scale));
            var canvas = document.createElement('canvas');
            canvas.width = cw;
            canvas.height = ch;
            canvas.getContext('2d').drawImage(img, 0, 0, cw, ch);
            resolve(canvas.toDataURL('image/jpeg', quality));
        };
        img.onerror = function() { reject(new Error('图片加载失败')); };
        img.src = dataUrl;
    });
}

// 开始修图
async function startEditImage() {
    if (!editImageData) {
        alert('请先上传要修改的图片');
        return;
    }
    
    const editType = document.getElementById('editType').value;
    const editDesc = document.getElementById('editDescription').value.trim();
    const keepElements = document.getElementById('keepElements').value.trim();
    const regions = getEditPayloadRegions();
    
    if (regions.length === 0 && !editDesc) {
        alert('请框选要修改的区域并填写修改要求，或在下方填写全局修改描述');
        return;
    }
    
    document.getElementById('editLoadingCard').style.display = 'block';
    document.getElementById('editResultsSection').style.display = 'none';
    document.getElementById('editOriginalImg').src = editImageData;
    
    try {
        var imageToSend = editImageData;
        try {
            imageToSend = await compressImageDataUrl(editImageData, 2048, 0.88);
        } catch (e) {
            console.warn('图片压缩跳过', e);
        }
        const payload = {
            image: imageToSend,
            editType: editType,
            description: editDesc,
            keepElements: keepElements,
            image_backend: localStorage.getItem('imageBackendSelect') || 'lovart',
            project: currentProject ? currentProject.name : ''
        };
        if (regions.length > 0) {
            payload.regions = regions;
        }
        const response = await fetch('/api/edit-image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (response.status === 404) {
            alert('修图接口未找到(404)，请确认已用 ./start.sh 重启服务，并访问终端里显示的端口');
            return;
        }
        const data = await response.json();
        
        if (data.success) {
            document.getElementById('editResultImg').src = '/outputs/' + data.output_image + '?t=' + Date.now();
            document.getElementById('editResultsSection').style.display = 'block';
            currentEditResult = data.output_image;
            editImageData = document.getElementById('editResultImg').src;
            loadHistory();
        } else {
            alert('修图失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('请求失败: ' + e.message);
    } finally {
        document.getElementById('editLoadingCard').style.display = 'none';
    }
}

var currentEditResult = null;

// 下载修图结果
function downloadEditResult() {
    if (!currentEditResult) return;
    const link = document.createElement('a');
    link.href = '/outputs/' + currentEditResult;
    link.download = 'edit_result_' + Date.now() + '.png';
    link.click();
}

// 重置修图
function resetEdit() {
    removeEditImage();
    document.getElementById('editDescription').value = '';
    document.getElementById('keepElements').value = '';
    currentEditResult = null;
}

// 重置
function resetAll() {
    document.getElementById('variantsSection').style.display = 'none';
    document.getElementById('actionsSection').style.display = 'none';
    document.getElementById('mainTitle').value = '';
    document.getElementById('subTitle').value = '';
    document.getElementById('visualDesc').value = '';
    document.getElementById('layoutRef').value = '';
    document.getElementById('extraNotes').value = '';
    selectedVariant = null;
    variants = [];
}

// 历史记录侧边栏开关
function toggleHistory() {
    document.getElementById('historyDrawer').classList.toggle('open');
    document.getElementById('drawerOverlay').classList.toggle('open');
}

// 全屏查看图片
// 全屏查看 - 支持键盘左右键切换
var fullscreenImages = [];
var fullscreenLabels = [];
var fullscreenIndex = 0;

function normalizeImageUrls(images) {
    if (!images) return [];
    var list = Array.isArray(images) ? images : [images];
    return list.map(function(img) {
        if (!img) return '';
        if (img.indexOf('http') === 0 || img.indexOf('/') === 0 || img.indexOf('data:') === 0) return img;
        return '/outputs/' + img;
    }).filter(Boolean);
}

function openHistoryThumb(el) {
    if (!el || !el.getAttribute('data-images')) return;
    try {
        var images = JSON.parse(decodeURIComponent(el.getAttribute('data-images')));
        var index = parseInt(el.getAttribute('data-index'), 10) || 0;
        openFullscreen(images, index);
    } catch (e) {
        console.error('历史图片打开失败', e);
    }
}

function openEditComparisonFullscreen(startIndex) {
    var orig = document.getElementById('editOriginalImg');
    var result = document.getElementById('editResultImg');
    var images = [];
    var labels = [];
    if (orig && orig.src) {
        images.push(orig.src);
        labels.push('原图');
    }
    if (result && result.src) {
        images.push(result.src);
        labels.push('修改后');
    }
    if (!images.length) return;
    openFullscreen(images, startIndex || 0, labels);
}

function openFullscreen(images, startIndex, labels) {
    if (typeof images === 'string') {
        try {
            var parsed = JSON.parse(images);
            images = Array.isArray(parsed) ? parsed : [images];
        } catch (e) {
            images = [images];
        }
    }
    fullscreenImages = normalizeImageUrls(images);
    fullscreenLabels = labels || [];
    fullscreenIndex = startIndex || 0;
    if (!fullscreenImages.length) return;
    updateFullscreenImage();
    document.getElementById('fullscreenOverlay').classList.add('open');
    document.addEventListener('keydown', handleFullscreenKey);
}

function updateFullscreenNav() {
    var multi = fullscreenImages.length > 1;
    var prevBtn = document.querySelector('.fullscreen-prev');
    var nextBtn = document.querySelector('.fullscreen-next');
    if (prevBtn) prevBtn.classList.toggle('hidden', !multi);
    if (nextBtn) nextBtn.classList.toggle('hidden', !multi);
}

function updateFullscreenImage() {
    var currentImg = fullscreenImages[fullscreenIndex];
    document.getElementById('fullscreenImg').src = currentImg;
    var downloadBtn = document.getElementById('fullscreenDownload');
    downloadBtn.href = currentImg;
    var name = currentImg.split('/').pop().split('?')[0];
    downloadBtn.download = name || 'image.png';
    var counter = document.getElementById('fullscreenCounter');
    if (counter) {
        var label = fullscreenLabels[fullscreenIndex] || '';
        counter.textContent = (fullscreenIndex + 1) + ' / ' + fullscreenImages.length +
            (label ? ' · ' + label : '');
    }
    updateFullscreenNav();
}

function handleFullscreenKey(e) {
    if (e.key === 'ArrowLeft') {
        fullscreenIndex = (fullscreenIndex - 1 + fullscreenImages.length) % fullscreenImages.length;
        updateFullscreenImage();
    } else if (e.key === 'ArrowRight') {
        fullscreenIndex = (fullscreenIndex + 1) % fullscreenImages.length;
        updateFullscreenImage();
    } else if (e.key === 'Escape') {
        closeFullscreen();
    }
}

function closeFullscreen() {
    document.getElementById('fullscreenOverlay').classList.remove('open');
    document.removeEventListener('keydown', handleFullscreenKey);
}

function prevImage() {
    fullscreenIndex = (fullscreenIndex - 1 + fullscreenImages.length) % fullscreenImages.length;
    updateFullscreenImage();
}

function nextImage() {
    fullscreenIndex = (fullscreenIndex + 1) % fullscreenImages.length;
    updateFullscreenImage();
}

// 加载历史
async function loadHistory() {
    try {
        var res = await fetch('/history');
        var data = await res.json();
        renderHistory(data.items || []);
    } catch(e) {}
}

function renderHistory(items) {
    var el = document.getElementById('historyList');
    if (!items.length) {
        el.innerHTML = '<div style="text-align:center;color:#999;padding:30px;">暂无记录</div>';
        return;
    }
    var visible = items.filter(function(item) {
        var imgs = item.output_images || (item.output_image ? [item.output_image] : []);
        return imgs.length > 0;
    });
    el.innerHTML = visible.slice(0, 30).map(function(item) {
        var time = new Date(item.timestamp).toLocaleString('zh-CN', {hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
        var mode = item.mode === 'edit' ? '✏️局部修图' : (item.mode === 'text2img' ? '✨文字生图' : '📷图片改图');
        var variants = item.variants_count > 1 ? ' · ' + item.variants_count + '张' : '';
        var images = item.output_images || (item.output_image ? [item.output_image] : []);
        var imageUrls = images.map(function(img) { return '/outputs/' + String(img).split('?')[0]; });
        var thumbsHtml = imageUrls.length ? imageUrls.map(function(imgSrc, i) {
            return '<img class="history-thumb" src="' + imgSrc + '" loading="lazy" ' +
                'data-images="' + encodeURIComponent(JSON.stringify(imageUrls)) + '" data-index="' + i + '" ' +
                'onclick="event.stopPropagation(); openHistoryThumb(this)" style="cursor:pointer;">';
        }).join('') : '<div style="width:64px;height:64px;border-radius:8px;background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;font-size:11px;color:rgba(255,255,255,0.35);">无图</div>';
        var downloadsHtml = imageUrls.map(function(imgSrc, i) {
            var name = images[i] || ('image_' + i + '.png');
            return '<a class="history-download" href="' + imgSrc + '" download="' + name + '" onclick="event.stopPropagation()">⬇</a>';
        }).join('');
        var clickAttr = imageUrls.length ? ' onclick="openHistoryThumb(this.querySelector(\'.history-thumb\'))"' : '';
        return '<div class="history-item"' + clickAttr + '>' +
            '<div class="history-img-wrap" style="display:flex;gap:4px;flex-wrap:wrap;position:relative;">' +
            thumbsHtml +
            downloadsHtml +
            '</div>' +
            '<div class="history-info">' +
            '<div class="history-time">' + time + '</div>' +
            '<div class="history-prompt">' + (item.prompt || '').substring(0, 40) + (item.prompt && item.prompt.length > 40 ? '...' : '') + '</div>' +
            '<div class="history-meta">' + mode + variants + '</div>' +
            '</div></div>';
    }).join('');
}
</script>
</body>
</html>"""


# ─── HTTP Handler ────────────────────────────────────────────────
class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):

    def _normalized_path(self):
        path = urllib.parse.urlparse(self.path).path
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')
        return path

    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        path = self._normalized_path()
        if path in ('/api/edit-image', '/edit-image', '/parse', '/api/analyze',
                    '/generate-variants', '/generate-with-prompt', '/upscale'):
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        path = self._normalized_path()
        if path == '/' or path == '/index.html':
            self._send_html(HTML_PAGE)
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
                proj_dir = PROJECTS_DIR / project
                self._serve_file(proj_dir, filename)
            else:
                self.send_response(404)
                self.end_headers()
        elif path == '/projects':
            self._send_json({"projects": list_projects()})
        elif path.startswith('/projects/') and path.endswith('/images'):
            project = urllib.parse.unquote(path.split('/')[2])
            self._send_json({"images": get_project_images(project)})
        elif path == '/history':
            self._send_json({"items": filter_history_items(load_history())})
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

        # 构建 DeepSeek 提示词
        system_prompt = """你是专业的视觉设计助手，擅长将设计需求转化为适合AI绘图的提示词。

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

        # 构建用户消息
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
        
        # 添加项目风格标签
        if project_meta and project_meta.get("style_tags"):
            user_parts.append(f"品牌风格要求：{', '.join(project_meta['style_tags'])}")
        
        user_parts.append("请结合主标题与副标题共同扩写画面关键词，避免只重复标题文字。")
        user_message = "\n".join(user_parts) if user_parts else "请帮我生成通用的设计提示词"
        
        # 调用 DeepSeek API
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # 五级 fallback: DeepSeek → 千问 → Kimi → 豆包 → 简单拼接
        ai_prompt, error = call_deepseek(messages, temperature=0.7, max_tokens=500)
        
        if error:
            print(f"[DeepSeek Error] {error}, 尝试千问...")
            ai_prompt, error2 = call_qianwen(messages, temperature=0.7, max_tokens=500)
            
            if error2:
                print(f"[千问 Error] {error2}, 尝试Kimi...")
                ai_prompt, error3 = call_kimi(messages, temperature=0.7, max_tokens=500)
                
                if error3:
                    print(f"[Kimi Error] {error3}, 尝试豆包...")
                    ai_prompt, error4 = call_doubao(messages, temperature=0.7, max_tokens=500)
                    
                    if error4:
                        print(f"[豆包 Error] {error4}, 降级到简单拼接")
                        ai_prompt = expand_prompt_from_summary(summary, project_meta)
                    else:
                        print(f"[豆包] AI 输出: {ai_prompt}")
                else:
                    print(f"[Kimi] AI 输出: {ai_prompt}")
            else:
                print(f"[千问] AI 输出: {ai_prompt}")
        else:
            print(f"[DeepSeek] AI 输出: {ai_prompt}")
        
        print(f"[用户输入] {user_message[:100]}...")
        self._send_json({"prompt": ai_prompt})

    def _handle_generate_with_prompt(self):
        """使用已有的prompt直接生成图片"""
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' in content_type:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            boundary = content_type.split('boundary=')[-1].encode()
            fields = parse_multipart(body, boundary)
        else:
            fields = {}

        prompt = fields.get('prompt', '')
        project = fields.get('project', '')
        count = int(fields.get('count', '3'))
        ratio = fields.get('ratio', '1:1')
        image_backend = fields.get('image_backend', '')

        if not prompt:
            self._send_json({"error": "请提供关键词"})
            return

        # 添加项目参考图（只添加选中的图片）
        image_paths = []
        selected_imgs = fields.get('selected_project_images')
        if selected_imgs:
            try:
                selected_list = json.loads(selected_imgs) if isinstance(selected_imgs, str) else selected_imgs
            except:
                selected_list = []
        else:
            selected_list = []
        
        # 如果没有选中的图片，则自动添加项目中的所有图片（兼容旧方式）
        if not selected_list and project:
            proj_dir = PROJECTS_DIR / project
            if proj_dir.exists():
                for img in sorted(proj_dir.iterdir()):
                    if img.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                        image_paths.append(img)
                        if len(image_paths) >= 10:
                            break
        elif selected_list:
            # 只添加选中的图片
            for img_ref in selected_list[:10]:  # 最多10张
                if '/' in img_ref:
                    proj_name, img_name = img_ref.split('/', 1)
                    img_path = PROJECTS_DIR / proj_name / img_name
                else:
                    img_path = PROJECTS_DIR / project / img_ref
                if img_path.exists():
                    image_paths.append(img_path)
        
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
                image_backend=image_backend,
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
                        variants.append({"filename": None, "error": str(e)})
                else:
                    variants.append({"filename": None, "error": r.get("error", "生成失败")})

            # 保存历史
            if variants and variants[0].get("filename"):
                # 保存所有生成的图片到 output_images
                output_images = [v["filename"] for v in variants if v.get("filename")]
                entry = {
                    "id": uuid.uuid4().hex[:8],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "mode": mode,
                    "prompt": prompt,
                    "project": project,
                    "output_images": output_images,
                    "variants_count": len(output_images),
                }
                add_history(entry)

            self._send_json({"variants": variants, "prompt": prompt})

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_generate_variants(self):
        """生成多张变体"""
        content_type = self.headers.get('Content-Type', '')

        if 'multipart/form-data' in content_type:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            boundary = content_type.split('boundary=')[-1].encode()
            fields = parse_multipart(body, boundary)
        else:
            fields = {}

        summary_json = fields.get('summary', '{}')
        project = fields.get('project', '')
        count = int(fields.get('count', '3'))
        ratio = fields.get('ratio', '1:1')
        image_backend = fields.get('image_backend', '')
        uploaded_file = fields.get('file')

        try:
            summary = json.loads(summary_json)
        except:
            summary = {}

        # 获取项目元数据
        project_meta = get_project_meta(project) if project else None

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

        # 添加项目参考图（只添加选中的图片）
        selected_imgs = fields.get('selected_project_images')
        if selected_imgs:
            try:
                selected_list = json.loads(selected_imgs) if isinstance(selected_imgs, str) else selected_imgs
            except:
                selected_list = []
        else:
            selected_list = []
        
        if not selected_list and project:
            proj_dir = PROJECTS_DIR / project
            if proj_dir.exists():
                for img in sorted(proj_dir.iterdir()):
                    if img.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                        image_paths.append(img)
                        if len(image_paths) >= 10:
                            break
        elif selected_list:
            for img_ref in selected_list[:10]:
                if '/' in img_ref:
                    proj_name, img_name = img_ref.split('/', 1)
                    img_path = PROJECTS_DIR / proj_name / img_name
                else:
                    img_path = PROJECTS_DIR / project / img_ref
                if img_path.exists():
                    image_paths.append(img_path)
        
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
                image_backend=image_backend,
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
                        variants.append({"filename": None, "error": str(e)})
                else:
                    variants.append({"filename": None, "error": r.get("error", "生成失败")})

            # 保存历史
            if variants and variants[0].get("filename"):
                # 保存所有生成的图片到 output_images
                output_images = [v["filename"] for v in variants if v.get("filename")]
                entry = {
                    "id": uuid.uuid4().hex[:8],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "mode": mode,
                    "prompt": prompt,
                    "project": project,
                    "input_image": input_filename,
                    "output_images": output_images,
                    "variants_count": len(output_images),
                }
                add_history(entry)

            self._send_json({"variants": variants, "prompt": prompt})

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json({"error": str(e)})

    def _handle_upscale(self):
        """图片放大"""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode('utf-8'))
        except:
            self._send_json({"error": "无效请求"})
            return

        filename = data.get('filename', '')
        resolution = data.get('resolution', '2k')

        if not filename:
            self._send_json({"error": "未指定图片"})
            return

        image_path = OUTPUT_DIR / filename
        if not image_path.exists():
            self._send_json({"error": "图片不存在"})
            return

        try:
            dreamina_cmd = _dreamina_command_path()
            if not dreamina_cmd:
                self._send_json({"error": f"未找到即梦 CLI: {DREAMINA_BIN}，请安装 dreamina 或设置 DREAMINA_BIN"})
                return

            cmd = [dreamina_cmd, "image_upscale", "--image", str(image_path),
                   "--resolution_type", resolution, "--poll", "90"]

            print(f"[CMD] {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                self._send_json({"error": result.stderr or "放大失败"})
                return

            try:
                resp = json.loads(result.stdout.strip())
                images = resp.get("result_json", {}).get("images", [])
                if not images:
                    self._send_json({"error": "放大未返回图片"})
                    return
                image_url = images[0].get("image_url", "")
            except:
                self._send_json({"error": "解析放大结果失败"})
                return

            output_filename = f"upscale_{uuid.uuid4().hex}.png"
            output_path = OUTPUT_DIR / output_filename
            download_image(image_url, output_path)

            entry = {
                "id": uuid.uuid4().hex[:8],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "mode": "upscale",
                "prompt": f"放大到 {resolution}",
                "input_image": filename,
                "output_image": output_filename,
                "upscale": resolution,
            }
            add_history(entry)

            self._send_json({"output_image": output_filename})

        except subprocess.TimeoutExpired:
            self._send_json({"error": "放大超时"})
        except Exception as e:
            self._send_json({"error": str(e)})

    def _serve_file(self, directory, filename):
        filepath = directory / filename.split('?')[0]
        if filepath.exists():
            self.send_response(200)
            ext = filepath.suffix.lower()
            mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'gif': 'image/gif', 'webp': 'image/webp'}.get(ext, 'application/octet-stream')
            self.send_header('Content-type', mime)
            self.send_header('Content-Length', str(filepath.stat().st_size))
            self.end_headers()
            self.wfile.write(filepath.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()

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
        image_backend = data.get('image_backend', '')
        local_project = (data.get('project') or '').strip() or None
        regions = data.get('regions') or []

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

        prompt = build_edit_prompt(description, edit_type, keep_elements)

        output_filename = f"edit_output_{uuid.uuid4().hex}.png"
        output_path = OUTPUT_DIR / output_filename

        if regions:
            work_path, region_error = edit_image_regions(
                input_path,
                regions,
                edit_type,
                keep_elements,
                image_backend,
                ratio,
                local_project=local_project,
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
                input_path, prompt, image_backend, ratio=ratio, local_project=local_project
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
        entry = {
            "id": uuid.uuid4().hex[:8],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": "edit",
            "prompt": prompt,
            "input_image": input_filename,
            "output_image": output_filename,
            "edit_type": edit_type
        }
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
    dreamina_cmd = _dreamina_command_path()
    image_backend = _resolve_image_backend()
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
    print(f"   服务端默认生图后端: {image_backend}")
    print(f"   页面可切换生图模型: Lovart / ComfyUI / Stable Diffusion")
    if image_backend == "lovart":
        key_count = len(LOVART_CREDENTIALS)
        print(f"   Lovart API: {LOVART_BASE_URL}（已配置 {key_count} 组 Key，受限时自动切换）")
    elif image_backend == "dreamina":
        print(f"   即梦 CLI: {dreamina_cmd or DREAMINA_BIN}")
        if not dreamina_cmd:
            print("   提示: 未检测到 dreamina，生图/放大功能需要先安装并配置 DREAMINA_BIN")
    if COMFYUI_CHECKPOINT:
        print(f"   ComfyUI: {COMFYUI_API_URL} / {COMFYUI_CHECKPOINT}")
    else:
        print("   ComfyUI: 未配置 COMFYUI_CHECKPOINT")
    print(f"   Stable Diffusion: {SD_API_URL}")
    if not any([DEEPSEEK_API_KEY, QIANWEN_API_KEY, KIMI_API_KEY, DOUBAO_API_KEY]):
        print("   提示: 未配置大模型 API Key，关键词分析将使用本地规则拼接")
    print(f"   功能: 需求解析 · 多图变体 · 项目组选择 · 风格参考\n")

    with httpd:
        httpd.serve_forever()
