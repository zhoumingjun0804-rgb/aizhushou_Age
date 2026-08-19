"""OpenAI 兼容网关 GPT 生图客户端（AgentHub / 官方等）。"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ssl_utils import make_ssl_context, open_http_request

_ssl_ctx = make_ssl_context()

GPT_IMAGE_MODELS = (
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1-mini",
)

GPT_SIZE_PRESETS = (
    (1024, 1024),
    (1536, 1024),
    (1024, 1536),
)

GPT_IMAGE2_MIN_PIXELS = 655_360
GPT_IMAGE2_MAX_PIXELS = 8_294_400
GPT_IMAGE2_MAX_EDGE = 3840
GPT_IMAGE2_GRID = 16
GPT_IMAGE2_OUTPUT_QUALITIES = ("low", "medium", "high", "auto")

GPT_MAX_REFERENCE_IMAGES = 4


class GptImageError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class GptAuth:
    bearer: str = ""
    app_key_query: str = ""
    api_key_header: str = ""


def _is_agenthub(base_url: str) -> bool:
    return "agenthub" in (base_url or "").lower()


def is_azure_gateway(base_url: str) -> bool:
    lower = (base_url or "").lower()
    return (
        "azure-open-ai" in lower
        or "/gptproto" in lower
        or ("61info.cn" in lower and "/openai" in lower)
    )


def _looks_like_hex_app_key(key: str) -> bool:
    cleaned = key.strip()
    return len(cleaned) >= 16 and all(c in "0123456789abcdef" for c in cleaned.lower())


def is_official_openai_api_key(key: str) -> bool:
    """platform.openai.com 创建的 Key 通常以 sk- 开头。"""
    return (key or "").strip().startswith("sk-")


def resolve_gpt_image_auth(
    api_key: str,
    fallback_bearer_key: str = "",
    base_url: str = "",
    provider: str = "official",
) -> GptAuth:
    """按生图 provider 解析鉴权：azure=api-key 头；official=Bearer；agenthub=sk-user+query。"""
    api_key = (api_key or "").strip()
    fallback_bearer_key = (fallback_bearer_key or "").strip()
    provider = (provider or "official").strip().lower()
    if not api_key:
        return GptAuth()
    if provider == "azure":
        return GptAuth(api_key_header=api_key)
    if provider == "agenthub" or _is_agenthub(base_url):
        if api_key.startswith(("sk-user-", "sk-agent-")):
            return GptAuth(bearer=api_key)
        if _looks_like_hex_app_key(api_key) and fallback_bearer_key:
            return GptAuth(bearer=fallback_bearer_key, app_key_query=api_key)
        return GptAuth(bearer=api_key)
    return GptAuth(bearer=api_key)


def validate_official_gpt_image_key(api_key: str, slug: str, project: str) -> str | None:
    key = (api_key or "").strip()
    if not key:
        return None
    if is_official_openai_api_key(key):
        return None
    if _looks_like_hex_app_key(key):
        return (
            f"{project} 的 OPENAI_API_KEY_{slug} 填的是 AgentHub appKey，不能用于官方 api.openai.com。"
            f"请从 platform.openai.com 创建 sk-proj-... Key，"
            f"或设置 OPENAI_IMAGE_PROVIDER_{slug}=agenthub 改走 AgentHub 生图。"
        )
    return (
        f"{project} 的 OPENAI_API_KEY_{slug} 格式不正确（官方 Key 应以 sk- 开头）。"
        f"请检查 platform.openai.com 的 API Key。"
    )


def resolve_gpt_auth(openai_key: str, deepseek_key: str = "", base_url: str = "") -> GptAuth:
    """官方 OpenAI：Key 直传 Bearer；AgentHub hex appKey：sk-user + ?api_key=。"""
    openai_key = (openai_key or "").strip()
    deepseek_key = (deepseek_key or "").strip()
    if not openai_key:
        return GptAuth(bearer="")
    if not _is_agenthub(base_url):
        return GptAuth(bearer=openai_key)
    if openai_key.startswith(("sk-user-", "sk-agent-")):
        return GptAuth(bearer=openai_key)
    if _looks_like_hex_app_key(openai_key) and deepseek_key:
        return GptAuth(bearer=deepseek_key, app_key_query=openai_key)
    return GptAuth(bearer=openai_key)


def append_auth_query(url: str, auth: GptAuth) -> str:
    return _append_query(url, {"api_key": auth.app_key_query} if auth.app_key_query else {})


def _append_query(url: str, params: dict[str, str]) -> str:
    filtered = {k: v for k, v in params.items() if v}
    if not filtered:
        return url
    parts = urlparse(url)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True))
    merged.update(filtered)
    return urlunparse(parts._replace(query=urlencode(merged)))


def resolve_gpt_image_model(model: str | None = None, tier: str | None = None) -> str:
    explicit = (model or "").strip()
    if explicit in GPT_IMAGE_MODELS:
        return explicit
    tier_map = {
        "fast": os.environ.get("OPENAI_IMAGE_MODEL_FAST", "gpt-image-1-mini"),
        "balanced": os.environ.get("OPENAI_IMAGE_MODEL_BALANCED", "gpt-image-1.5"),
        "quality": os.environ.get("OPENAI_IMAGE_MODEL_QUALITY", "gpt-image-2"),
    }
    tier_key = (tier or "balanced").strip().lower()
    resolved = tier_map.get(tier_key) or os.environ.get("OPENAI_IMAGE_MODEL_BALANCED", "gpt-image-1.5")
    if resolved in GPT_IMAGE_MODELS:
        return resolved
    return "gpt-image-1.5"


def map_dimensions_to_size(width: int, height: int) -> str:
    """按宽高比匹配 GPT 支持的固定尺寸，避免宽扁图误映射成正方形。"""
    width = max(1, int(width))
    height = max(1, int(height))
    target_log_ratio = math.log(width / height)
    best = GPT_SIZE_PRESETS[0]
    best_dist = float("inf")
    for w, h in GPT_SIZE_PRESETS:
        dist = abs(math.log(w / h) - target_log_ratio)
        if dist < best_dist:
            best_dist = dist
            best = (w, h)
    return f"{best[0]}x{best[1]}"


def _snap_gpt_image2_dim(value: int) -> int:
    value = max(GPT_IMAGE2_GRID, int(value))
    return max(GPT_IMAGE2_GRID, int(round(value / GPT_IMAGE2_GRID)) * GPT_IMAGE2_GRID)


def _gpt_image2_size_valid(w: int, h: int) -> bool:
    if w % GPT_IMAGE2_GRID or h % GPT_IMAGE2_GRID:
        return False
    if w > GPT_IMAGE2_MAX_EDGE or h > GPT_IMAGE2_MAX_EDGE:
        return False
    pixels = w * h
    if pixels < GPT_IMAGE2_MIN_PIXELS or pixels > GPT_IMAGE2_MAX_PIXELS:
        return False
    long_edge, short_edge = max(w, h), min(w, h)
    return (long_edge / short_edge) <= 3.0


def map_dimensions_to_gpt_image2_size(width: int, height: int) -> str:
    """gpt-image-2：优先使用用户选的线上尺寸；仅在不满足 API 约束时做最小修正。"""
    width = max(1, int(width))
    height = max(1, int(height))
    # 用户尺寸已合法时原样下发，避免先映射到固定预设再二次裁切
    if _gpt_image2_size_valid(width, height):
        return f"{width}x{height}"

    ratio = width / height
    scale = 1.0
    if width * height < GPT_IMAGE2_MIN_PIXELS:
        scale = math.sqrt(GPT_IMAGE2_MIN_PIXELS / (width * height))

    w = _snap_gpt_image2_dim(int(math.ceil(width * scale)))
    h = _snap_gpt_image2_dim(int(math.ceil(height * scale)))
    h = _snap_gpt_image2_dim(int(round(w / ratio)))

    for _ in range(64):
        if _gpt_image2_size_valid(w, h):
            return f"{w}x{h}"
        if w * h < GPT_IMAGE2_MIN_PIXELS:
            w = _snap_gpt_image2_dim(w + GPT_IMAGE2_GRID)
            h = _snap_gpt_image2_dim(int(round(w / ratio)))
            continue
        if w * h > GPT_IMAGE2_MAX_PIXELS or w > GPT_IMAGE2_MAX_EDGE or h > GPT_IMAGE2_MAX_EDGE:
            w = _snap_gpt_image2_dim(w - GPT_IMAGE2_GRID)
            h = _snap_gpt_image2_dim(int(round(w / ratio)))
            continue
        long_edge, short_edge = max(w, h), min(w, h)
        if long_edge / short_edge > 3.0:
            break
        w = _snap_gpt_image2_dim(w + GPT_IMAGE2_GRID)
        h = _snap_gpt_image2_dim(int(round(w / ratio)))

    return map_dimensions_to_size(width, height)


def is_gpt_image2_model(model: str | None) -> bool:
    return (model or "").strip() == "gpt-image-2"


def resolve_gpt_api_size(target_w: int, target_h: int, model: str | None = None) -> str:
    if is_gpt_image2_model(model):
        return map_dimensions_to_gpt_image2_size(target_w, target_h)
    return map_dimensions_to_size(target_w, target_h)


def resolve_gpt_image_output_quality(
    model: str | None = None,
    override: str | None = None,
) -> str | None:
    if not is_gpt_image2_model(model):
        return None
    raw = (
        (override or "").strip().lower()
        or (os.environ.get("OPENAI_IMAGE_OUTPUT_QUALITY") or "medium").strip().lower()
    )
    if raw in GPT_IMAGE2_OUTPUT_QUALITIES:
        return raw
    return "medium"


def parse_gpt_size_preset(size: str) -> tuple[int, int]:
    """'1024x1536' → (1024, 1536)。"""
    parts = (size or "").lower().split("x")
    if len(parts) != 2:
        return GPT_SIZE_PRESETS[0]
    return max(1, int(parts[0])), max(1, int(parts[1]))


def resolve_gpt_work_size(target_w: int, target_h: int, model: str | None = None) -> tuple[int, int]:
    """目标像素 → GPT API 实际工作尺寸（与 edits 请求 size 一致）。"""
    return parse_gpt_size_preset(resolve_gpt_api_size(target_w, target_h, model))


def _normalize_image_paths(image_paths) -> list[Path]:
    paths: list[Path] = []
    for item in image_paths or []:
        path = Path(item)
        if path.is_file():
            paths.append(path)
        if len(paths) >= GPT_MAX_REFERENCE_IMAGES:
            break
    return paths


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/png"


def _connection_error_message(exc: Exception) -> str | None:
    msg = str(exc)
    lower = msg.lower()
    if "tunnel connection failed" in lower or "504 gateway" in lower:
        return (
            "连接 GPT 生图网关时代理隧道超时。"
            "公司 Azure 网关（61info.cn）须直连：确认 OPENAI_IMAGE_PROVIDER=azure，"
            "且 .env 中 NO_PROXY 含 61info.cn（勿让 HTTP_PROXY 代理内网）。"
            f"（{msg}）"
        )
    if "eof occurred in violation of protocol" in lower or "ssl" in lower and "eof" in lower:
        return (
            "连接 GPT 生图网关时 SSL 握手失败（多为 HTTP_PROXY 误代理内网地址）。"
            "请确认 OPENAI_IMAGE_PROVIDER_*=azure、NO_PROXY 含 61info.cn，"
            "并核对 OPENAI_IMAGE_BASE_URL_* 与项目组（画啦啦/小灯塔）路径一致。"
            f"（{msg}）"
        )
    if "timed out" in lower or "timeout" in lower:
        return (
            "连接 GPT 生图网关超时（Azure 生图通常需 30–90 秒）。"
            "请确认已连公司内网/VPN，或稍后重试。"
            f"（{msg}）"
        )
    return None


def _friendly_error(status: int, payload: dict | str, url: str = "") -> str:
    text = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            text = str(err.get("message") or err.get("detail") or payload)
        else:
            text = str(payload.get("detail") or payload.get("message") or payload)
    else:
        text = str(payload)
    lower = text.lower()
    if "cloudflare" in lower or "cf-error" in lower:
        return (
            "GPT 请求被 Cloudflare 拦截（1010）：外网 HTTP_PROXY 打到了 gptproto.com。"
            "钛林生图须直连 https://liuyi-llm-risk.61info.cn/api/gptproto，"
            "不要走 Lovart 用的香港代理。"
        )
    if "country" in lower and ("not supported" in lower or "region" in lower or "territory" in lower):
        return (
            "OpenAI 官方 API 不支持当前出口 IP 所在地区。"
            "请在 .env 配置可访问 OpenAI 的 HTTP_PROXY/HTTPS_PROXY（或 OPENAI_IMAGE_PROXY），"
            "并确认 OPENAI_API_KEY_* 为 platform.openai.com 的 sk-proj-... Key。"
            f"（{text}）"
        )
    if status in (401, 403):
        if "subscription key" in lower or "wrong api endpoint" in lower:
            return (
                "GPT 生图网关拒绝了当前项目组的订阅 Key（已失效或未开通该接口）。"
                "小灯塔请更新 OPENAI_API_KEY_XDT；画啦啦请更新 OPENAI_API_KEY_HLL。"
                "前缀须为 https://liuyi-llm-risk.61info.cn/api/gptproto 。"
                f"（{text}）"
            )
        return f"GPT 生图 Key 无效或无权限：{text}"
    if status == 429 or "rate" in lower or "quota" in lower:
        return f"GPT 生图额度或频率受限：{text}"
    if "未对外提供服务" in text or "未对外提供服务" in str(payload):
        return (
            "钛林尚未对该 appId 开放 gptproto 服务（不是路径配错）。"
            "请在钛林开通 gpt-image-2 后再把新 Key 写入 OPENAI_API_KEY_XDT / _HLL。"
            f"（{text}）"
        )
    if status == 404:
        where = f"（请求 {url}）" if url else ""
        return (
            "GPT 生图网关路径错误（404）"
            f"{where}。"
            "新域名请使用钛林渠道前缀 /api/gptproto。"
            "请把 OPENAI_IMAGE_BASE_URL_XDT / _HLL 设为 "
            "https://liuyi-llm-risk.61info.cn/api/gptproto"
        )
    if status >= 500 or "timeout" in lower:
        return f"GPT 生图服务暂时不可用：{text}"
    if "inactive api key" in lower or "agent not found" in lower:
        return (
            "GPT 生图 appKey 无效或未激活。请在 AgentHub 确认应用为 ACTIVE，"
            "或将完整 sk-agent-... 写入 OPENAI_API_KEY_HLL。"
            f"（{text}）"
        )
    if "not configured in this platform" in lower:
        return (
            f"GPT 生图模型未在 AgentHub 开通：{text}。"
            "请联系管理员在平台配置 gpt-image 模型。"
        )
    return text or f"GPT 生图失败（HTTP {status}）"


def _auth_headers(auth: GptAuth, content_type: str) -> dict[str, str]:
    headers = {
        "Content-Type": content_type,
        "User-Agent": "Aizhushou-GPT/1.0",
        "Accept": "application/json",
    }
    if auth.api_key_header:
        # Azure OpenAI 用 api-key；钛林 gptproto 要求 Bearer 或 x-api-key
        headers["api-key"] = auth.api_key_header
        headers["Ocp-Apim-Subscription-Key"] = auth.api_key_header
        headers["x-api-key"] = auth.api_key_header
        headers["Authorization"] = f"Bearer {auth.api_key_header}"
    elif auth.bearer:
        headers["Authorization"] = f"Bearer {auth.bearer}"
    return headers


def _request_json(
    method: str,
    url: str,
    auth: GptAuth,
    payload: dict | None,
    timeout: int,
    *,
    use_proxy: bool = True,
) -> tuple[int, dict | str]:
    url = _append_query(url, {"api_key": auth.app_key_query} if auth.app_key_query else {})
    headers = _auth_headers(auth, "application/json")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with open_http_request(req, timeout=timeout, context=_ssl_ctx, use_proxy=use_proxy) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.getcode(), json.loads(body)
            except json.JSONDecodeError:
                return resp.getcode(), body
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return err.code, parsed
    except Exception as e:
        hint = _connection_error_message(e)
        if hint:
            raise GptImageError(hint) from e
        raise GptImageError(f"连接 GPT 生图网关失败: {e}") from e


def _encode_multipart_form(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8") + b"\r\n")
    for field_name, filename, data, content_type in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        chunks.append(data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def _request_multipart(
    url: str,
    auth: GptAuth,
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, bytes, str]],
    timeout: int,
    *,
    use_proxy: bool = True,
) -> tuple[int, dict | str]:
    url = _append_query(url, {"api_key": auth.app_key_query} if auth.app_key_query else {})
    body, boundary = _encode_multipart_form(fields, files)
    headers = _auth_headers(auth, f"multipart/form-data; boundary={boundary}")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with open_http_request(req, timeout=timeout, context=_ssl_ctx, use_proxy=use_proxy) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.getcode(), json.loads(raw)
            except json.JSONDecodeError:
                return resp.getcode(), raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return err.code, parsed
    except Exception as e:
        hint = _connection_error_message(e)
        if hint:
            raise GptImageError(hint) from e
        raise GptImageError(f"连接 GPT 生图网关失败: {e}") from e


def _extract_image_from_responses(
    result: dict | str,
    temp_dir: Path,
) -> tuple[Optional[str], Optional[str]] | None:
    if not isinstance(result, dict):
        return None
    for item in result.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("image_generation_call", "image"):
            b64 = item.get("result") or item.get("b64_json") or item.get("image_base64")
            if b64:
                temp_dir.mkdir(parents=True, exist_ok=True)
                out = temp_dir / f"gpt_{int(time.time() * 1000)}.png"
                out.write_bytes(base64.b64decode(b64))
                return f"file://{out.resolve()}", None
    return None


def _extract_image_ref(result: dict, temp_dir: Path) -> tuple[Optional[str], Optional[str]]:
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None, "GPT 生图未返回图片"
    first = data[0] if isinstance(data[0], dict) else {}
    url = (first.get("url") or "").strip()
    if url:
        return url, None
    b64 = first.get("b64_json")
    if b64:
        temp_dir.mkdir(parents=True, exist_ok=True)
        out = temp_dir / f"gpt_{int(time.time() * 1000)}.png"
        out.write_bytes(base64.b64decode(b64))
        return f"file://{out.resolve()}", None
    return None, "GPT 生图返回格式异常（无 url/b64_json）"


def _build_responses_input(prompt: str, image_paths: list[Path]) -> list[dict]:
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    for path in image_paths:
        mime = _guess_mime(path)
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime};base64,{b64}",
            }
        )
    return [{"role": "user", "content": content}]


class GptImageClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://agenthub.vipthink.cn",
        timeout: int = 120,
        temp_dir: Path | None = None,
        fallback_bearer_key: str = "",
        provider: str = "official",
    ):
        self.provider = (provider or "official").strip().lower()
        self.auth = resolve_gpt_image_auth(api_key, fallback_bearer_key, base_url, self.provider)
        # 官方 OpenAI 需代理；公司 Azure / AgentHub 内网直连
        self.use_proxy = self.provider == "official" and not is_azure_gateway(base_url)
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[: -len("/v1")]
        min_timeout = 300 if self.provider == "azure" else 30
        self.timeout = max(min_timeout, int(timeout))
        self.temp_dir = temp_dir or Path("outputs")

    def _gpt_quality_fields(self, model: str, output_quality: str | None = None) -> list[tuple[str, str]]:
        quality = resolve_gpt_image_output_quality(model, override=output_quality)
        if quality:
            return [("quality", quality)]
        return []

    def _generate_with_edits(
        self,
        prompt: str,
        model: str,
        size: str,
        image_paths: list[Path],
        retries: int,
        mask_path: Path | None = None,
        output_quality: str | None = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """官方 Image API：/v1/images/edits，支持多张参考图与扩边蒙版。"""
        url = f"{self.base_url}/v1/images/edits"
        fields = [
            ("model", model),
            ("prompt", prompt),
            ("size", size),
        ]
        fields.extend(self._gpt_quality_fields(model, output_quality))
        files: list[tuple[str, str, bytes, str]] = []
        for path in image_paths:
            files.append(("image[]", path.name, path.read_bytes(), _guess_mime(path)))
        if mask_path and mask_path.is_file():
            files.append(("mask", mask_path.name, mask_path.read_bytes(), "image/png"))

        last_error = "GPT 参考图生图失败"
        for attempt in range(max(1, retries)):
            status, result = _request_multipart(
                url, self.auth, fields, files, self.timeout, use_proxy=self.use_proxy
            )
            if status < 400:
                return _extract_image_ref(result if isinstance(result, dict) else {}, self.temp_dir)
            last_error = _friendly_error(status, result, url)
            if status in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            break
        return None, last_error

    def _generate_with_responses(
        self,
        prompt: str,
        model: str,
        image_paths: list[Path],
        retries: int,
    ) -> tuple[Optional[str], Optional[str]]:
        url = f"{self.base_url}/v1/responses"
        tool: dict = {"type": "image_generation"}
        if image_paths:
            tool["action"] = "auto"
        body = {
            "model": model,
            "input": _build_responses_input(prompt, image_paths) if image_paths else prompt,
            "tools": [tool],
        }
        last_error = "GPT 生图失败"
        for attempt in range(max(1, retries)):
            status, result = _request_json(
                "POST", url, self.auth, body, self.timeout, use_proxy=self.use_proxy
            )
            if status < 400:
                parsed = _extract_image_from_responses(result, self.temp_dir)
                if parsed:
                    return parsed
                return None, "GPT Responses 未返回图片"
            last_error = _friendly_error(status, result, url)
            if status in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            if status not in (404, 405):
                break
        return None, last_error

    def _generate_text_to_image(
        self,
        prompt: str,
        model: str,
        size: str,
        retries: int,
        output_quality: str | None = None,
    ) -> tuple[Optional[str], Optional[str]]:
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
        quality = resolve_gpt_image_output_quality(model, override=output_quality)
        if quality:
            payload["quality"] = quality
        if self.provider == "azure":
            payload["output_format"] = "png"
            payload["output_compression"] = 100
        endpoints = [f"{self.base_url}/v1/images/generations"]
        if "agenthub" in self.base_url.lower():
            endpoints.append(f"{self.base_url}/v1/responses")

        last_error = "GPT 生图失败"
        for url in endpoints:
            if url.endswith("/responses"):
                result = self._generate_with_responses(prompt, model, [], retries)
                if result[0] or (result[1] and "404" not in result[1]):
                    return result
                last_error = result[1] or last_error
                continue
            for attempt in range(max(1, retries)):
                status, result = _request_json(
                    "POST", url, self.auth, payload, self.timeout, use_proxy=self.use_proxy
                )
                if status < 400:
                    return _extract_image_ref(result if isinstance(result, dict) else {}, self.temp_dir)
                last_error = _friendly_error(status, result, url)
                if status in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                    time.sleep(2 ** attempt)
                    continue
                if status not in (404, 405):
                    break
        return None, last_error

    def generate_image(
        self,
        prompt: str,
        *,
        model: str,
        width: int = 1024,
        height: int = 1024,
        image_paths=None,
        mask_path: Path | None = None,
        retries: int = 3,
        prefer_responses: bool = False,
        output_quality: str | None = None,
    ) -> tuple[Optional[str], Optional[str]]:
        if not self.auth.bearer and not self.auth.api_key_header:
            return None, "未配置 OPENAI_API_KEY（或 OPENAI_APP_KEY）"

        refs = _normalize_image_paths(image_paths)
        # 始终按用户选择的线上尺寸请求 GPT，避免「固定预设生成 + 后台再裁切」
        size = resolve_gpt_api_size(width, height, model)
        quality = resolve_gpt_image_output_quality(model, override=output_quality)
        print(
            f"[GPT] request size={size} model={model} quality={quality or '-'} "
            f"refs={len(refs)} mask={bool(mask_path)}"
        )
        if refs:
            if prefer_responses and not mask_path:
                result = self._generate_with_responses(prompt, model, refs, retries)
                if result[0]:
                    return result
                err_lower = (result[1] or "").lower()
                if "unsupported" not in err_lower and "404" not in err_lower:
                    return result
            result = self._generate_with_edits(
                prompt, model, size, refs, retries,
                mask_path=mask_path, output_quality=output_quality,
            )
            if result[0]:
                return result
            if mask_path:
                return result
            if "404" in (result[1] or "") or "not configured" in (result[1] or "").lower():
                fallback = self._generate_with_responses(prompt, model, refs, retries)
                if fallback[0]:
                    return fallback
                return None, fallback[1] or result[1]
            return result

        return self._generate_text_to_image(
            prompt, model, size, retries, output_quality=output_quality,
        )


def model_uses_max_completion_tokens(model: str) -> bool:
    m = (model or "").lower().strip()
    return m.startswith("gpt-5") or (len(m) >= 2 and m[0] == "o" and m[1].isdigit())


def chat_completion_token_param(model: str, max_tokens: int) -> dict[str, int]:
    """GPT-5 / o-series 使用 max_completion_tokens，旧模型使用 max_tokens。"""
    if model_uses_max_completion_tokens(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def model_supports_temperature(model: str) -> bool:
    """GPT-5 / o-series 不支持自定义 temperature。"""
    return not model_uses_max_completion_tokens(model)


def build_chat_completion_payload(
    model: str,
    messages: list,
    *,
    temperature: float = 0.7,
    max_tokens: int = 1000,
) -> dict:
    payload: dict = {"model": model, "messages": messages}
    if model_supports_temperature(model):
        payload["temperature"] = temperature
    payload.update(chat_completion_token_param(model, max_tokens))
    return payload


def _chat_retryable_status(status: int, err_text: str) -> bool:
    if status in (524, 502, 503, 504, 429):
        return True
    lower = (err_text or "").lower()
    return "timeout" in lower or "524" in lower


def call_gpt_chat(
    messages: list,
    *,
    api_key: str,
    base_url: str,
    provider: str,
    model: str,
    fallback_bearer_key: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1000,
    timeout: int = 120,
    max_retries: int = 2,
) -> tuple[Optional[str], Optional[str]]:
    """GPT 润色 / chat：Azure=api-key；AgentHub=Bearer；official=Bearer+代理。"""
    auth = resolve_gpt_image_auth(api_key, fallback_bearer_key, base_url, provider)
    if not auth.bearer and not auth.api_key_header:
        return None, "未配置 GPT API Key"
    use_proxy = provider == "official" and not is_azure_gateway(base_url)
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    if provider == "agenthub":
        url = append_auth_query(url, auth)

    payload = build_chat_completion_payload(
        model, messages, temperature=temperature, max_tokens=max_tokens
    )
    last_error = "GPT chat 失败"
    for attempt in range(max(1, max_retries)):
        status, result = _request_json(
            "POST", url, auth, payload, timeout, use_proxy=use_proxy
        )
        if status < 400:
            if not isinstance(result, dict):
                return None, str(result)
            try:
                return result["choices"][0]["message"]["content"], None
            except (KeyError, IndexError, TypeError):
                return None, "GPT chat 返回格式异常"
        last_error = _friendly_error(status, result, url)
        if attempt + 1 >= max_retries or not _chat_retryable_status(status, last_error):
            break
        time.sleep(2 * (attempt + 1))
    return None, last_error
