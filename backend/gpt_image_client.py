"""OpenAI 兼容网关 GPT 生图客户端（AgentHub / 官方等）。"""
from __future__ import annotations

import base64
import json
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
    (1792, 1024),
    (1024, 1792),
)

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
    return "azure-open-ai" in lower or (
        "61info.cn" in lower and "/openai" in lower
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
    best = GPT_SIZE_PRESETS[0]
    best_dist = float("inf")
    for w, h in GPT_SIZE_PRESETS:
        dist = abs(w - width) + abs(h - height)
        if dist < best_dist:
            best_dist = dist
            best = (w, h)
    return f"{best[0]}x{best[1]}"


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


def _friendly_error(status: int, payload: dict | str) -> str:
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
    if "country" in lower and ("not supported" in lower or "region" in lower or "territory" in lower):
        return (
            "OpenAI 官方 API 不支持当前出口 IP 所在地区。"
            "请在 .env 配置可访问 OpenAI 的 HTTP_PROXY/HTTPS_PROXY（或 OPENAI_IMAGE_PROXY），"
            "并确认 OPENAI_API_KEY_* 为 platform.openai.com 的 sk-proj-... Key。"
            f"（{text}）"
        )
    if status in (401, 403):
        return f"GPT 生图 Key 无效或无权限：{text}"
    if status == 429 or "rate" in lower or "quota" in lower:
        return f"GPT 生图额度或频率受限：{text}"
    if "未对外提供服务" in text or "未对外提供服务" in str(payload):
        return (
            "当前项目组的 GPT Key 与网关路径不匹配。"
            "请核对 OPENAI_API_KEY_* 与 OPENAI_IMAGE_BASE_URL_* 是否为同一项目组（画啦啦/小灯塔各一套）。"
            f"（{text}）"
        )
    if status == 404:
        return (
            "GPT 生图网关路径错误（404）。请核对 OPENAI_IMAGE_BASE_URL_HLL / _XDT "
            "是否与项目组一致（如 azure-open-ai-hll-smart-draw / xdt-smart-draw）。"
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
    headers = {"Content-Type": content_type}
    if auth.api_key_header:
        headers["api-key"] = auth.api_key_header
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
        self.use_proxy = self.provider == "official"
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[: -len("/v1")]
        min_timeout = 120 if self.provider == "azure" else 30
        self.timeout = max(min_timeout, int(timeout))
        self.temp_dir = temp_dir or Path("outputs")

    def _generate_with_edits(
        self,
        prompt: str,
        model: str,
        size: str,
        image_paths: list[Path],
        retries: int,
    ) -> tuple[Optional[str], Optional[str]]:
        """官方 Image API：/v1/images/edits，支持多张参考图。"""
        url = f"{self.base_url}/v1/images/edits"
        fields = [
            ("model", model),
            ("prompt", prompt),
            ("size", size),
        ]
        files: list[tuple[str, str, bytes, str]] = []
        for path in image_paths:
            files.append(("image[]", path.name, path.read_bytes(), _guess_mime(path)))

        last_error = "GPT 参考图生图失败"
        for attempt in range(max(1, retries)):
            status, result = _request_multipart(
                url, self.auth, fields, files, self.timeout, use_proxy=self.use_proxy
            )
            if status < 400:
                return _extract_image_ref(result if isinstance(result, dict) else {}, self.temp_dir)
            last_error = _friendly_error(status, result)
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
            last_error = _friendly_error(status, result)
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
    ) -> tuple[Optional[str], Optional[str]]:
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
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
                last_error = _friendly_error(status, result)
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
        retries: int = 3,
    ) -> tuple[Optional[str], Optional[str]]:
        if not self.auth.bearer and not self.auth.api_key_header:
            return None, "未配置 OPENAI_API_KEY（或 OPENAI_APP_KEY）"

        size = map_dimensions_to_size(width, height)
        refs = _normalize_image_paths(image_paths)
        if refs:
            result = self._generate_with_edits(prompt, model, size, refs, retries)
            if result[0]:
                return result
            if "404" in (result[1] or "") or "not configured" in (result[1] or "").lower():
                fallback = self._generate_with_responses(prompt, model, refs, retries)
                if fallback[0]:
                    return fallback
                return None, fallback[1] or result[1]
            return result

        return self._generate_text_to_image(prompt, model, size, retries)


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
    timeout: int = 60,
) -> tuple[Optional[str], Optional[str]]:
    """GPT 润色 / chat：Azure=api-key；AgentHub=Bearer；official=Bearer+代理。"""
    auth = resolve_gpt_image_auth(api_key, fallback_bearer_key, base_url, provider)
    if not auth.bearer and not auth.api_key_header:
        return None, "未配置 GPT API Key"
    use_proxy = provider == "official"
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    if provider == "agenthub":
        url = append_auth_query(url, auth)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    status, result = _request_json("POST", url, auth, payload, timeout, use_proxy=use_proxy)
    if status >= 400:
        return None, _friendly_error(status, result)
    if not isinstance(result, dict):
        return None, str(result)
    try:
        return result["choices"][0]["message"]["content"], None
    except (KeyError, IndexError, TypeError):
        return None, "GPT chat 返回格式异常"
