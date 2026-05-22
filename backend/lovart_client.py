"""Lovart OpenAPI 客户端（AK/SK 签名）。"""
import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional, Tuple

from ssl_utils import make_ssl_context


class LovartError(Exception):
    def __init__(self, message: str, code: int = 0):
        self.message = message
        self.code = code
        super().__init__(message)


def load_lovart_credentials() -> list[tuple[str, str]]:
    """从环境变量加载多组 Lovart AK/SK（主键 + LOVART_ACCESS_KEY_2 等）。"""
    pairs: list[tuple[str, str]] = []

    def _append(ak: str, sk: str) -> None:
        ak, sk = ak.strip(), sk.strip()
        if ak and sk:
            pairs.append((ak, sk))

    _append(os.environ.get("LOVART_ACCESS_KEY", ""), os.environ.get("LOVART_SECRET_KEY", ""))

    for index in range(2, 11):
        ak = os.environ.get(f"LOVART_ACCESS_KEY_{index}", "")
        sk = os.environ.get(f"LOVART_SECRET_KEY_{index}", "")
        if not ak and not sk:
            continue
        _append(ak, sk)

    bulk_aks = os.environ.get("LOVART_ACCESS_KEYS", "").strip()
    bulk_sks = os.environ.get("LOVART_SECRET_KEYS", "").strip()
    if bulk_aks and bulk_sks:
        aks = [part.strip() for part in bulk_aks.split(",") if part.strip()]
        sks = [part.strip() for part in bulk_sks.split(",") if part.strip()]
        for ak, sk in zip(aks, sks):
            _append(ak, sk)

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def is_lovart_limit_error(message: str) -> bool:
    """并发已满、额度或频率受限时可切换下一组 Key。"""
    if not message:
        return False
    markers = (
        "concurrent task limit",
        "rate limit",
        "quota",
        "too many requests",
        "并发",
        "上限",
        "额度",
        "频率",
    )
    lower = message.lower()
    return any(marker in lower for marker in markers)


def mask_access_key(access_key: str) -> str:
    if len(access_key) <= 10:
        return f"{access_key[:4]}…"
    return f"{access_key[:6]}…{access_key[-4:]}"


class LovartClient:
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        base_url: str = "https://lgw.lovart.ai",
        timeout: int = 120,
        poll_interval: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.prefix = "/v1/openapi"
        self._ssl_ctx = make_ssl_context()

    def _sign(self, method: str, path: str) -> dict:
        ts = str(int(time.time()))
        sig = hmac.new(
            self.secret_key.encode(),
            f"{method}\n{path}\n{ts}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Access-Key": self.access_key,
            "X-Timestamp": ts,
            "X-Signature": sig,
            "X-Signed-Method": method,
            "X-Signed-Path": path,
        }

    def _request(self, method: str, path: str, body=None, params=None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = json.dumps(body).encode() if body is not None else None
        headers = self._sign(method, path)
        headers["Content-Type"] = "application/json"
        headers["User-Agent"] = "Mozilla/5.0 LovartClient/1.0"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            try:
                payload = json.loads(err)
                msg = payload.get("message", payload.get("error", str(e)))
                details = payload.get("details", "")
                if details:
                    msg = f"{msg}: {details}"
                raise LovartError(msg, e.code)
            except (json.JSONDecodeError, KeyError):
                raise LovartError(f"HTTP {e.code}: {err}", e.code) from e
        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            raise LovartError(f"连接 Lovart 失败: {e}") from e

        if isinstance(result, dict) and result.get("code", 0) != 0:
            raise LovartError(result.get("message", "Lovart 返回错误"), result.get("code", -1))

        return result.get("data", result) if isinstance(result, dict) else result

    def save_project(
        self,
        project_id: str = "",
        project_type: int = 3,
        title: str = "",
    ) -> str:
        """仅用于创建新项目（project_id 为空）。复用已有项目请用 validate + send，勿重复 save。"""
        body = {
            "project_id": "",
            "canvas": "",
            "project_cover_list": [],
            "pic_count": 0,
            "project_type": project_type,
        }
        if title:
            body["project_name"] = title
        result = self._request("POST", f"{self.prefix}/project/save", body=body)
        return result.get("project_id", "")

    def rename_project(self, project_id: str, name: str) -> dict:
        return self._request(
            "POST",
            f"{self.prefix}/project/save",
            body={"action": "rename", "project_id": project_id, "project_name": name},
        )

    def validate_project(self, project_id: str) -> bool:
        try:
            result = self._request(
                "GET",
                f"{self.prefix}/project/validate",
                params={"project_id": project_id},
            )
            return bool(result.get("valid"))
        except LovartError:
            return False

    def get_project_name(self, project_id: str) -> str:
        try:
            result = self._request(
                "GET",
                f"{self.prefix}/project/validate",
                params={"project_id": project_id},
            )
            return (result.get("project_name") or "").strip()
        except LovartError:
            return ""

    def create_project(self, project_type: int = 3, title: str = "") -> str:
        project_id = self.save_project(project_type=project_type, title=title)
        if project_id and title:
            try:
                current = self.get_project_name(project_id)
                if not current or current.lower() == "untitled":
                    self.rename_project(project_id, title)
            except LovartError:
                pass
        return project_id

    def upload_file(self, local_path: str) -> str:
        with open(local_path, "rb") as f:
            file_data = f.read()

        filename = os.path.basename(local_path)
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

        path = f"{self.prefix}/file/upload"
        headers = self._sign("POST", path)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["User-Agent"] = "LovartClient/1.0"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise LovartError(f"上传参考图失败: {e.read().decode()}", e.code) from e

        if result.get("code") != 0:
            raise LovartError(result.get("message", "上传参考图失败"))
        return result["data"]["url"]

    def send(self, prompt: str, project_id: str, attachments=None, mode: str = "fast") -> str:
        body = {"prompt": prompt, "project_id": project_id, "mode": mode}
        if attachments:
            body["attachments"] = attachments
        return self._request("POST", f"{self.prefix}/chat", body=body)["thread_id"]

    def get_status(self, thread_id: str) -> dict:
        return self._request("GET", f"{self.prefix}/chat/status", params={"thread_id": thread_id})

    def get_result(self, thread_id: str) -> dict:
        return self._request("GET", f"{self.prefix}/chat/result", params={"thread_id": thread_id})

    def set_mode(self, unlimited: bool) -> dict:
        return self._request("POST", f"{self.prefix}/mode/set", body={"unlimited": unlimited})

    def _result_has_image(self, result: dict) -> bool:
        for item in result.get("items", []):
            for artifact in item.get("artifacts", []):
                if artifact.get("type") == "image" and artifact.get("content"):
                    return True
        return False

    def poll(self, thread_id: str, timeout: int = 120) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get_status(thread_id).get("status")
            if status == "abort":
                return "abort"
            if status == "done":
                time.sleep(5)
                status = self.get_status(thread_id).get("status")
                if status in ("done", "abort"):
                    return status
            time.sleep(self.poll_interval)

        try:
            result = self.get_result(thread_id)
            if self._result_has_image(result):
                return "done"
        except LovartError:
            pass
        return "timeout"

    def extract_image_url(self, result: dict) -> Tuple[Optional[str], Optional[str]]:
        for item in result.get("items", []):
            for artifact in item.get("artifacts", []):
                if artifact.get("type") == "image" and artifact.get("content"):
                    return artifact["content"], None

        texts = [
            (item.get("text") or "").strip()
            for item in result.get("items", [])
            if item.get("text")
        ]
        if texts:
            return None, texts[0]
        return None, result.get("warning") or "Lovart 未返回图片"

    def generate_image(
        self,
        prompt: str,
        image_paths=None,
        ratio: str = "1:1",
        timeout: int = 120,
        mode: str = "fast",
        quality_hint: str = "",
        project_id: Optional[str] = None,
        project_title: str = "",
    ) -> Tuple[Optional[str], Optional[str]]:
        resolved_id = (project_id or "").strip()
        if not resolved_id:
            resolved_id = self.create_project(title=project_title)
        if not resolved_id:
            return None, "Lovart 创建项目失败"
        project_id = resolved_id

        try:
            self.set_mode(unlimited=False)
        except LovartError:
            pass

        attachments = []
        for image_path in image_paths or []:
            attachments.append(self.upload_file(str(image_path)))

        quality = quality_hint or os.environ.get(
            "LOVART_QUALITY_HINT",
            "适合手机屏幕与网页展示，宽度约1200到1536像素，细节清晰但不必4K",
        )
        full_prompt = f"请生成一张{quality}，比例为 {ratio} 的设计图。{prompt}"
        if attachments:
            full_prompt = f"请参考附件图片的风格与构图，{full_prompt}"

        thread_id = self.send(full_prompt, project_id, attachments=attachments or None, mode=mode)
        status = self.poll(thread_id, timeout=timeout)
        if status == "abort":
            return None, "Lovart 生成已中止"
        if status == "timeout":
            return None, f"Lovart 生成超时（已等待 {timeout} 秒，可在 .env 调大 LOVART_POLL_TIMEOUT）"

        result = self.get_result(thread_id)
        return self.extract_image_url(result)
