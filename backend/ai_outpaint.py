"""Lovart img2img 阔图：将参考图延展到目标像素尺寸。"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from PIL import Image

Img2ImgFn = Callable[[Path, str, str], tuple[str | None, str | None]]
DownloadFn = Callable[[str, Path], None]


def splash_extend_prompt(target_w: int, target_h: int) -> str:
    return (
        f"将参考图自然延展为 {target_w}x{target_h} 像素开屏图，"
        "保持原图风格、色调与画面主体不变，向四周自然补边延展背景与场景，"
        "不要新增文字、不要添加 Logo、不要裁切或变形主体。"
    )


def layout_background_extend_prompt(target_w: int, target_h: int) -> str:
    return (
        f"将参考图延展为 {target_w}x{target_h} 像素横竖比的设计背景，"
        "保持原图背景风格与色调一致，自然补边，不要新增文字或 Logo。"
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
