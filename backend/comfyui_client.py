"""ComfyUI API 客户端。"""
import json
import os
import pathlib
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional, Tuple


class ComfyUIClientError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ComfyUIClient:
    def __init__(self, base_url: Optional[str] = None, timeout: int = 300, poll_interval: int = 2):
        self.base_url = (base_url or os.environ.get("COMFYUI_API_URL", "http://127.0.0.1:8188")).rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._ssl_ctx = ssl.create_default_context()

    def _request(self, method: str, path: str, body=None, params=None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise ComfyUIClientError(f"ComfyUI API 错误: {e.read().decode()}") from e
        except urllib.error.URLError as e:
            raise ComfyUIClientError(f"无法连接 ComfyUI ({self.base_url}): {e.reason}") from e

    def _build_workflow(self, prompt: str, width: int, height: int, seed: int) -> dict:
        checkpoint = os.environ.get("COMFYUI_CHECKPOINT", "").strip()
        if not checkpoint:
            raise ComfyUIClientError("未配置 COMFYUI_CHECKPOINT（ComfyUI 模型文件名）")

        negative = os.environ.get("COMFYUI_NEGATIVE_PROMPT", "low quality, blurry, watermark")
        return {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": int(os.environ.get("COMFYUI_STEPS", "20")),
                    "cfg": float(os.environ.get("COMFYUI_CFG", "8")),
                    "sampler_name": os.environ.get("COMFYUI_SAMPLER", "euler"),
                    "scheduler": "normal",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["4", 1]},
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "ai-design", "images": ["8", 0]},
            },
        }

    def _wait_for_output(self, prompt_id: str) -> dict:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            history = self._request("GET", f"/history/{prompt_id}")
            if prompt_id in history:
                entry = history[prompt_id]
                if entry.get("outputs"):
                    return entry
            time.sleep(self.poll_interval)
        raise ComfyUIClientError(f"ComfyUI 生成超时（已等待 {self.timeout} 秒）")

    def _download_output(self, image_info: dict) -> str:
        params = {
            "filename": image_info["filename"],
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        }
        url = f"{self.base_url}/view?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
            image_bytes = resp.read()
        temp_path = pathlib.Path(tempfile.gettempdir()) / f"comfyui_{uuid.uuid4().hex}.png"
        temp_path.write_bytes(image_bytes)
        return f"file://{temp_path}"

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        image_paths=None,
    ) -> Tuple[Optional[str], Optional[str]]:
        if image_paths:
            return None, "ComfyUI 当前仅支持文生图，请先去掉参考图或改用 Lovart / Stable Diffusion"

        seed = int(time.time() * 1000) % 2_147_483_647
        workflow = self._build_workflow(prompt, width, height, seed)
        client_id = uuid.uuid4().hex
        result = self._request("POST", "/prompt", body={"prompt": workflow, "client_id": client_id})
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            return None, "ComfyUI 未返回 prompt_id"

        history = self._wait_for_output(prompt_id)
        for node_output in history.get("outputs", {}).values():
            images = node_output.get("images") or []
            if images:
                return self._download_output(images[0]), None
        return None, "ComfyUI 未返回图片"
