#!/usr/bin/env python3
"""Check whether an OpenAI-compatible gateway can generate images."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def get_config(env_file_values: dict[str, str]) -> tuple[str, str]:
    env = os.environ
    api_key = (
        env.get("OPENAI_API_KEY")
        or env_file_values.get("OPENAI_API_KEY")
        or env.get("DEEPSEEK_API_KEY")
        or env_file_values.get("DEEPSEEK_API_KEY")
        or ""
    ).strip()
    base_url = (
        env.get("OPENAI_BASE_URL")
        or env_file_values.get("OPENAI_BASE_URL")
        or env.get("DEEPSEEK_BASE_URL")
        or env_file_values.get("DEEPSEEK_BASE_URL")
        or "https://api.openai.com"
    ).strip()
    return api_key, normalize_base_url(base_url)


def normalize_base_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[: -len("/v1")]
    if not cleaned.startswith("http"):
        raise ValueError(f"BASE_URL 看起来不合法: {base_url}")
    return cleaned


def request_json(method: str, url: str, api_key: str, payload: dict | None, timeout: int) -> tuple[int, dict | str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def summarize_error(payload: dict | str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            code = err.get("code")
            if msg and code:
                return f"{code}: {msg}"
            if msg:
                return str(msg)
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


def contains_model(model_list_payload: dict | str, model_name: str) -> bool:
    if not isinstance(model_list_payload, dict):
        return False
    data = model_list_payload.get("data")
    if not isinstance(data, list):
        return False
    for item in data:
        if isinstance(item, dict) and str(item.get("id", "")).strip() == model_name:
            return True
    return False


def run_check(api_key: str, base_url: str, model: str, timeout: int) -> int:
    if not api_key:
        print("FAIL: 未找到 API Key。请配置 OPENAI_API_KEY（或当前兼容读取 DEEPSEEK_API_KEY）。")
        return 2

    models_url = f"{base_url}/v1/models"
    print(f"[1/2] 检查模型列表: {models_url}")
    model_status, model_payload = request_json("GET", models_url, api_key, None, timeout)
    if model_status >= 400:
        print(f"FAIL: 模型列表请求失败 ({model_status})")
        print(f"原因: {summarize_error(model_payload)}")
        return 1

    model_exists = contains_model(model_payload, model)
    if model_exists:
        print(f"PASS: 模型列表包含 `{model}`")
    else:
        print(f"WARN: 模型列表未发现 `{model}`（仍继续做实际生图探测）")

    images_url = f"{base_url}/v1/images/generations"
    print(f"[2/2] 发起最小生图请求: {images_url}")
    image_payload = {
        "model": model,
        "prompt": "A simple blue circle icon on white background",
        "size": "1024x1024",
    }
    image_status, image_result = request_json("POST", images_url, api_key, image_payload, timeout)

    if image_status >= 400:
        print(f"FAIL: 生图请求失败 ({image_status})")
        print(f"原因: {summarize_error(image_result)}")
        print("\n结论: 当前 Key 或网关未开通图片模型权限。")
        return 1

    if not isinstance(image_result, dict):
        print("FAIL: 生图返回格式异常（非 JSON）")
        print(f"响应: {image_result}")
        return 1

    data = image_result.get("data")
    if not isinstance(data, list) or not data:
        print("FAIL: 生图返回中缺少 data")
        print(f"响应: {json.dumps(image_result, ensure_ascii=False)}")
        return 1

    first = data[0] if isinstance(data[0], dict) else {}
    has_url = bool(first.get("url"))
    has_b64 = bool(first.get("b64_json"))
    if has_b64:
        # Validate b64 field shape without writing files.
        try:
            base64.b64decode(first["b64_json"][:64] + "==", validate=False)
        except Exception:
            pass

    if has_url or has_b64:
        print("PASS: 生图请求成功，已确认图片模型权限可用。")
        return 0

    print("FAIL: 生图请求返回成功状态，但未找到 url/b64_json")
    print(f"响应: {json.dumps(image_result, ensure_ascii=False)}")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 OpenAI 兼容网关是否具备图片模型权限。")
    parser.add_argument("--model", default="gpt-image-1", help="要测试的图片模型 ID，默认 gpt-image-1")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP 超时秒数，默认 30")
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help=f".env 路径，默认 {DEFAULT_ENV_FILE}",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="覆盖 BASE_URL（优先级高于环境变量），例如 https://agenthub.vipthink.cn",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="覆盖 API Key（优先级高于环境变量）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file).expanduser()
    env_values = load_env_file(env_path)
    api_key, base_url = get_config(env_values)
    if args.api_key.strip():
        api_key = args.api_key.strip()
    if args.base_url.strip():
        base_url = normalize_base_url(args.base_url.strip())

    print("=== OpenAI 图片权限自检 ===")
    print(f"Base URL: {base_url}")
    print(f"Model: {args.model}")
    print(f"Env file: {env_path}")
    print("")
    return run_check(api_key=api_key, base_url=base_url, model=args.model, timeout=max(5, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
