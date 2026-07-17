"""AI 阔图：按目标尺寸向外扩展画面，输出一张完整成图（非贴图合成）。"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageChops

from gpt_image_client import resolve_gpt_work_size
from multi_size_export import (
    fit_contain_box,
    fit_image_contain_mirror_extend,
    overlay_preserved_center,
)

Img2ImgFn = Callable[..., tuple[str | None, str | None]]
DownloadFn = Callable[[str, Path], None]

SPLASH_OUTPAINT_RULES = (
    "【Outpainting 纯扩边·输出一张完整成图】"
    "附件是用户上传的成品设计图，是唯一基准。"
    "请先仔细观察附件四边边缘的像素内容，再向外扩展："
    "左侧扩边必须延续左边缘的天空/树木/建筑/渐变；"
    "右侧扩边必须延续右边缘的草地/建筑/天空/渐变；"
    "上下扩边同理。禁止生成与边缘无关的新场景或新主体。"
    "任务：在保持附件中心内容完全不变的前提下，向四周扩展画面至目标尺寸，"
    "使输出看起来像同一张图被自然加宽/加高拍摄，而不是两张图拼在一起。"
    "严禁改动中心区域的文字、人物、IP、Logo、按钮与 UI；"
    "严禁拉伸、压扁或改变附件的宽高比例。"
)


def compute_outpaint_gaps(
    canvas_w: int,
    canvas_h: int,
    ox: int,
    oy: int,
    nw: int,
    nh: int,
) -> dict[str, int]:
    """各方向待扩边像素（0 表示该侧无需扩展）。"""
    return {
        "left": max(0, ox),
        "right": max(0, canvas_w - ox - nw),
        "top": max(0, oy),
        "bottom": max(0, canvas_h - oy - nh),
    }


def format_directional_extend_instructions(gaps: dict[str, int]) -> str:
    labels = (
        ("left", "左侧"),
        ("right", "右侧"),
        ("top", "上侧"),
        ("bottom", "下侧"),
    )
    parts = [f"{label}需扩展约 {gaps[key]}px" for key, label in labels if gaps.get(key, 0) > 0]
    if not parts:
        return "四周均需适度扩展。"
    horiz = gaps.get("left", 0) + gaps.get("right", 0)
    vert = gaps.get("top", 0) + gaps.get("bottom", 0)
    emphasis = ""
    if horiz > vert * 1.2:
        emphasis = "本次以左右横向扩边为主，务必让左右新增区域与对应边缘无缝衔接。"
    elif vert > horiz * 1.2:
        emphasis = "本次以上下纵向扩边为主，务必让上下新增区域与对应边缘无缝衔接。"
    return "；".join(parts) + "。" + emphasis


def splash_extend_prompt(
    target_w: int,
    target_h: int,
    gaps: dict[str, int] | None = None,
) -> str:
    directional = format_directional_extend_instructions(gaps or {})
    return (
        f"{SPLASH_OUTPAINT_RULES}"
        f"【扩边方向】{directional}"
        f"目标输出尺寸 {target_w}×{target_h} 像素。"
        "输出必须是一张完整、可直接使用的成品图。"
    )


def layout_background_extend_prompt(target_w: int, target_h: int) -> str:
    return (
        f"{SPLASH_OUTPAINT_RULES}"
        f"目标输出 {target_w}x{target_h} 像素背景层。"
        "仅延伸背景，主体 Logo/IP 将由后续合成覆盖，背景勿含新文字。"
    )


SPLASH_SUBFRAME_PROMPT_CORE = (
    "根据生成的尺寸要求，以参考图为视觉基准扩展上下左右的画面，注意不要去掉底部色块，"
    "整张图自然延展为一张完整成品，不要出现拼接缝、贴图层或原图硬贴效果。"
)

# 落地页头图延展 400×400：固定上下构图，直接注入 AI 提示词
SPLASH_SUBFRAME_LAYOUT_400x400 = (
    "本尺寸固定为上下排版布局：顶部是标题文字，底部是角色/人物主体，"
    "标题在上、角色在下，垂直分层清晰，不要把标题与角色挤在画面中部。"
)

# 落地页头图延展 690×320 / 750×280 / 750×422：固定左右构图
SPLASH_SUBFRAME_LAYOUT_HORIZONTAL = (
    "本尺寸固定为左右排版布局：左边是标题文字，右边是角色/人物主体，"
    "标题在左、角色在右，水平分层清晰，不要把标题与角色挤在画面中部。"
)

SPLASH_SUBFRAME_HORIZONTAL_SIZES = frozenset({(690, 320), (750, 280), (750, 422)})


def splash_subframe_extend_prompt(
    target_w: int,
    target_h: int,
    remark: str | None = None,
) -> str:
    prompt = (
        f"{SPLASH_SUBFRAME_PROMPT_CORE}"
        f"目标输出尺寸为 {target_w}×{target_h} 像素，输出一张完整可直接使用的成品图。"
    )
    size = (int(target_w), int(target_h))
    if size == (400, 400):
        prompt += SPLASH_SUBFRAME_LAYOUT_400x400
    elif size in SPLASH_SUBFRAME_HORIZONTAL_SIZES:
        prompt += SPLASH_SUBFRAME_LAYOUT_HORIZONTAL
    extra = (remark or "").strip()
    if extra:
        prompt += extra
    return prompt


def build_outpaint_canvas_for_gpt(src_img: Image.Image, work_w: int, work_h: int) -> Image.Image:
    """GPT 输入：中心清晰原图 + 四周镜像延展（比模糊更能保留边缘结构语义）。"""
    return fit_image_contain_mirror_extend(src_img, work_w, work_h)


def build_outpaint_mask(
    work_w: int,
    work_h: int,
    ox: int,
    oy: int,
    nw: int,
    nh: int,
) -> Image.Image:
    """RGBA 蒙版：透明=待补全四周，不透明=完整保留原图区域。"""
    mask = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
    center = Image.new("RGBA", (nw, nh), (255, 255, 255, 255))
    mask.paste(center, (ox, oy))
    return mask


def build_edge_reference_paths(
    fitted: Image.Image,
    upload_dir: Path,
    *,
    gaps: dict[str, int] | None = None,
    strip_px: int = 64,
    max_refs: int = 3,
) -> list[Path]:
    """待扩边方向的边缘条带参考图，按扩边量从大到小优先附加。"""
    img = fitted.convert("RGBA")
    w, h = img.size
    strip = max(12, min(strip_px, w // 3, h // 3))
    edge_boxes: list[tuple[str, tuple[int, int, int, int]]] = [
        ("left", (0, 0, strip, h)),
        ("right", (w - strip, 0, w, h)),
        ("top", (0, 0, w, strip)),
        ("bottom", (0, h - strip, w, h)),
    ]
    if gaps:
        edge_boxes = [item for item in edge_boxes if gaps.get(item[0], 0) > 0]
        edge_boxes.sort(key=lambda item: gaps.get(item[0], 0), reverse=True)

    paths: list[Path] = []
    for side, box in edge_boxes:
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        crop = img.crop(box)
        path = upload_dir / f"outpaint_edge_{side}_{uuid.uuid4().hex[:8]}.png"
        crop.save(path, format="PNG")
        paths.append(path)
        if len(paths) >= max(1, max_refs):
            break
    return paths


def build_outpaint_gap_alpha_mask(
    canvas_w: int,
    canvas_h: int,
    ox: int,
    oy: int,
    nw: int,
    nh: int,
) -> Image.Image:
    """L 蒙版：255=扩边区域（可叠 AI 结果），0=中心保留区。"""
    mask = Image.new("L", (canvas_w, canvas_h), 255)
    center = Image.new("L", (nw, nh), 0)
    mask.paste(center, (ox, oy))
    return mask


def _scale_ai_canvas_to_target(
    ai_canvas: Image.Image,
    target_w: int,
    target_h: int,
    work_w: int | None,
    work_h: int | None,
) -> Image.Image:
    """将 GPT 成图对齐到目标像素（避免 contain 留白导致 JPEG 黑边）。"""
    ai = ai_canvas.convert("RGBA")
    if work_w and work_h:
        if ai.size != (work_w, work_h):
            ai = ai.resize((work_w, work_h), Image.Resampling.LANCZOS)
        if (work_w, work_h) != (target_w, target_h):
            return ai.resize((target_w, target_h), Image.Resampling.LANCZOS)
        return ai
    if ai.size != (target_w, target_h):
        return ai.resize((target_w, target_h), Image.Resampling.LANCZOS)
    return ai


def finalize_outpaint_result(
    ai_canvas: Image.Image,
    src_img: Image.Image,
    target_w: int,
    target_h: int,
    *,
    work_w: int | None = None,
    work_h: int | None = None,
) -> Image.Image:
    """AI 扩边结果铺满目标画布：镜像种子垫底 + AI 只写入扩边区 + 中心叠回原图。"""
    fitted, ox, oy = fit_contain_box(src_img, target_w, target_h)
    nw, nh = fitted.size
    seed = fit_image_contain_mirror_extend(src_img, target_w, target_h)
    ai_scaled = _scale_ai_canvas_to_target(ai_canvas, target_w, target_h, work_w, work_h)

    gap_alpha = build_outpaint_gap_alpha_mask(target_w, target_h, ox, oy, nw, nh)
    r, g, b, a = ai_scaled.split()
    a = ImageChops.multiply(a, gap_alpha)
    ai_border = Image.merge("RGBA", (r, g, b, a))

    merged = seed.copy()
    merged.paste(ai_border, (0, 0), ai_border)
    return overlay_preserved_center(merged, src_img, target_w, target_h)


def _download_and_finalize(
    url: str,
    dest: Path,
    download_image: DownloadFn,
    src_img: Image.Image,
    target_w: int,
    target_h: int,
    *,
    work_w: int | None = None,
    work_h: int | None = None,
) -> Image.Image:
    download_image(url, dest)
    with Image.open(dest) as out:
        return finalize_outpaint_result(
            out.convert("RGBA"),
            src_img,
            target_w,
            target_h,
            work_w=work_w,
            work_h=work_h,
        )


def run_lovart_extend_to_size(
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
    """Lovart Outpainting：以原图为基准扩边，输出前叠回等比原图。"""
    tmp = upload_dir / f"lovart_extend_src_{uuid.uuid4().hex[:10]}.png"
    raw: Path | None = None
    try:
        src_img.convert("RGBA").save(tmp, format="PNG")
        url, err = img2img(tmp, prompt, ratio, None, target_w, target_h)
        if not url:
            raise ValueError(err or "Lovart 智能扩边失败，请稍后重试")
        raw = upload_dir / f"lovart_extend_out_{uuid.uuid4().hex[:10]}.png"
        return _download_and_finalize(url, raw, download_image, src_img, target_w, target_h)
    finally:
        for path in (tmp, raw):
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                pass


def run_gpt_extend_to_size(
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
    """GPT 蒙版扩边：工作尺寸与 API size 对齐，附边缘参考图，成图后叠回等比原图。"""
    work_w, work_h = resolve_gpt_work_size(target_w, target_h)
    fitted, ox, oy = fit_contain_box(src_img, work_w, work_h)
    nw, nh = fitted.size
    gaps = compute_outpaint_gaps(work_w, work_h, ox, oy, nw, nh)
    prompt = splash_extend_prompt(target_w, target_h, gaps)
    tmp = upload_dir / f"gpt_extend_src_{uuid.uuid4().hex[:10]}.png"
    mask_path = upload_dir / f"gpt_extend_mask_{uuid.uuid4().hex[:10]}.png"
    edge_refs: list[Path] = []
    raw: Path | None = None
    try:
        build_outpaint_canvas_for_gpt(src_img, work_w, work_h).save(tmp, format="PNG")
        build_outpaint_mask(work_w, work_h, ox, oy, nw, nh).save(mask_path, format="PNG")
        edge_refs = build_edge_reference_paths(fitted, upload_dir, gaps=gaps)
        if edge_refs:
            prompt += "附加参考图为待扩边方向的边缘条带特写，请严格按各边边缘纹理与色彩延续，勿左右混淆。"
        url, err = img2img(tmp, prompt, ratio, mask_path, work_w, work_h, edge_refs)
        if not url:
            raise ValueError(err or "GPT 智能扩边失败，请稍后重试")
        raw = upload_dir / f"gpt_extend_out_{uuid.uuid4().hex[:10]}.png"
        return _download_and_finalize(
            url,
            raw,
            download_image,
            src_img,
            target_w,
            target_h,
            work_w=work_w,
            work_h=work_h,
        )
    finally:
        for path in (tmp, mask_path, raw, *edge_refs):
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                pass


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
    """兼容旧调用：默认走 GPT 扩边。"""
    return run_gpt_extend_to_size(
        src_img,
        target_w,
        target_h,
        prompt=prompt,
        ratio=ratio,
        upload_dir=upload_dir,
        img2img=img2img,
        download_image=download_image,
    )


def build_outpaint_ai_input(src_img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """兼容测试：GPT 扩边输入底图。"""
    work_w, work_h = resolve_gpt_work_size(target_w, target_h)
    return build_outpaint_canvas_for_gpt(src_img, work_w, work_h)
