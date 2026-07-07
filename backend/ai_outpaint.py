"""Lovart img2img 阔图：将参考图延展到目标像素尺寸。"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from PIL import Image

Img2ImgFn = Callable[[Path, str, str], tuple[str | None, str | None]]
DownloadFn = Callable[[str, Path], None]

SPLASH_OUTPAINT_RULES = (
    "【Outpainting 纯扩边·禁止改图·最高优先级】"
    "附件是已完成的成品设计稿，不是灵感参考。"
    "任务 ONLY：向外扩展(outpaint/inpaint edge)背景与场景，使画布达到目标尺寸与比例。"
    "中心主体区域必须与附件完全一致："
    "禁止 img2img 重绘、禁止换风格、禁止改配色、"
    "禁止新增/删除/修改任何文字、人物、IP、Logo、按钮、UI 元素、装饰与构图；"
    "禁止替换主体、禁止重新设计、禁止生成新的海报或 Banner。"
    "只允许在四周新增与原有背景连贯的延伸区域；"
    "若需补边，延伸现有天空/地面/渐变/纹理，不得引入新主体。"
)


def splash_extend_prompt(target_w: int, target_h: int) -> str:
    return (
        f"{SPLASH_OUTPAINT_RULES}"
        f"目标输出 {target_w}x{target_h} 像素。"
        "输出整张图必须看起来像是原图自然加宽/加高，而非新画的图。"
    )


def layout_background_extend_prompt(target_w: int, target_h: int) -> str:
    return (
        f"{SPLASH_OUTPAINT_RULES}"
        f"目标输出 {target_w}x{target_h} 像素背景层。"
        "仅延伸背景，主体 Logo/IP 将由后续合成覆盖，背景勿含新文字。"
    )


def run_ai_extend_to_size(
    src_img: Image.Image,
    target_w: int,
    target_h: int,
    *,
    prompt: str,
    ratio: str,
    upload_dir: Path,
    img2img: Img2ImgFn,
    download_image: DownloadFn,
) -> Image.Image:
    tmp = upload_dir / f"ai_extend_src_{uuid.uuid4().hex[:10]}.png"
    raw: Path | None = None
    try:
        src_img.convert("RGBA").save(tmp, format="PNG")
        url, err = img2img(tmp, prompt, ratio)
        if not url:
            raise ValueError(err or "AI 阔图失败")
        raw = upload_dir / f"ai_extend_out_{uuid.uuid4().hex[:10]}.png"
        download_image(url, raw)
        with Image.open(raw) as out:
            return out.convert("RGBA").resize((target_w, target_h), Image.Resampling.LANCZOS)
    finally:
        for path in (tmp, raw):
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                pass
