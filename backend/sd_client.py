"""Stable Diffusion WebUI（Automatic1111）API 客户端。"""
import base64
import json
import os
import pathlib
import ssl
import tempfile
import urllib.error
import urllib.request
import uuid
from typing import Optional, Tuple


class SDClientError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class StableDiffusionClient:
    def __init__(self, base_url: Optional[str] = None, timeout: int = 300):
        self.base_url = (base_url or os.environ.get("SD_API_URL", "http://127.0.0.1:7860")).rstrip("/")
        self.timeout = timeout
        self._ssl_ctx = ssl.create_default_context()

    def _request(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SDClientError(f"Stable Diffusion API 错误: {e.read().decode()}") from e
        except urllib.error.URLError as e:
            raise SDClientError(f"无法连接 Stable Diffusion API ({self.base_url}): {e.reason}") from e

    def _save_base64_image(self, image_b64: str) -> str:
        image_bytes = base64.b64decode(image_b64)
        temp_path = pathlib.Path(tempfile.gettempdir()) / f"sd_{uuid.uuid4().hex}.png"
        temp_path.write_bytes(image_bytes)
        return f"file://{temp_path}"

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        image_paths=None,
        negative_prompt: str = "",
    ) -> Tuple[Optional[str], Optional[str]]:
        steps = int(os.environ.get("SD_STEPS", "24"))
        cfg_scale = float(os.environ.get("SD_CFG_SCALE", "7"))
        sampler = os.environ.get("SD_SAMPLER", "Euler a")

        if image_paths:
            init_path = pathlib.Path(image_paths[0])
            init_b64 = base64.b64encode(init_path.read_bytes()).decode("ascii")
            payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or "low quality, blurry, watermark",
                "init_images": [init_b64],
                "width": width,
                "height": height,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "sampler_name": sampler,
                "denoising_strength": float(os.environ.get("SD_DENOISING_STRENGTH", "0.55")),
            }
            path = "/sdapi/v1/img2img"
        else:
            payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or "low quality, blurry, watermark",
                "width": width,
                "height": height,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "sampler_name": sampler,
            }
            path = "/sdapi/v1/txt2img"

        result = self._request(path, payload)
        images = result.get("images") or []
        if not images:
            return None, "Stable Diffusion 未返回图片"
        return self._save_base64_image(images[0]), None
