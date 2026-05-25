"""抠图：魔棒蒙版删除；智能提取（框选主体透明化）。"""
from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

try:
    from rembg import new_session, remove
except Exception:  # pragma: no cover - 依赖缺失时走运行时提示
    new_session = None
    remove = None


_REMBG_SESSION = None


def apply_cutout_mask(input_path: Path, mask_path: Path, output_path: Path) -> dict:
    with Image.open(input_path) as img:
        img = img.convert("RGBA")
        with Image.open(mask_path) as mask:
            mask = mask.convert("L")
            if mask.size != img.size:
                mask = mask.resize(img.size, Image.Resampling.NEAREST)
            alpha = img.split()[3]
            keep = ImageChops.invert(mask)
            alpha = ImageChops.multiply(alpha, keep)
            img.putalpha(alpha)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, format="PNG", optimize=True)
        w, h = img.size
    return {"width": w, "height": h}


def _clamp_roi(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    x = max(0, min(int(x), img_w - 1))
    y = max(0, min(int(y), img_h - 1))
    w = max(1, min(int(w), img_w - x))
    h = max(1, min(int(h), img_h - y))
    return x, y, w, h


def compute_extract_crop_bbox(
    img_w: int,
    img_h: int,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
    pad_ratio: float = 0.1,
) -> tuple[int, int, int, int]:
    x, y, w, h = _clamp_roi(roi_x, roi_y, roi_w, roi_h, img_w, img_h)
    pad = max(6, int(min(w, h) * pad_ratio))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img_w, x + w + pad)
    y1 = min(img_h, y + h + pad)
    return x0, y0, x1 - x0, y1 - y0


def save_extract_crop(input_path: Path, crop_path: Path, x: int, y: int, w: int, h: int) -> None:
    with Image.open(input_path) as im:
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        crop = im.crop((x, y, x + w, y + h))
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(crop_path, format="PNG", optimize=True)


def build_ai_extract_prompt(user_prompt: str) -> str:
    subject = (user_prompt or "").strip() or "框选区域中的主体"
    return (
        f"【抠图素材】从参考图中仅提取：{subject}。"
        "输出该元素单独呈现在完全透明背景上的 PNG 素材。"
        "不要任何海报背景、场景、装饰或其他文字元素。"
        "必须保持参考图中该主体的造型、配色、字体与细节一致，禁止改造型或重绘。"
        "画面里只保留这一个主体，边缘干净，可用于设计合成。"
    )


def has_rembg() -> bool:
    return remove is not None and new_session is not None


def _get_rembg_session():
    global _REMBG_SESSION
    if not has_rembg():
        raise RuntimeError("未安装 rembg 依赖，请先安装 requirements.txt 后重启服务")
    if _REMBG_SESSION is None:
        _REMBG_SESSION = new_session("u2net")
    return _REMBG_SESSION


def preferred_cutout_backend() -> str:
    return "rembg" if has_rembg() else "local"


def _refine_alpha_edges(alpha: Image.Image) -> Image.Image:
    alpha = ImageOps.autocontrast(alpha)
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.4))
    alpha = alpha.point(lambda v: 0 if v <= 4 else 255 if v >= 251 else int(v))
    return alpha


def _decontaminate_white_fringe(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= 0:
                px[x, y] = (0, 0, 0, 0)
                continue
            if a >= 250:
                continue
            af = a / 255.0
            nr = max(0, min(255, int(round((r - 255 * (1 - af)) / af))))
            ng = max(0, min(255, int(round((g - 255 * (1 - af)) / af))))
            nb = max(0, min(255, int(round((b - 255 * (1 - af)) / af))))
            px[x, y] = (nr, ng, nb, a)
    return rgba


def _trim_to_subject(im: Image.Image, padding: int = 0) -> Image.Image:
    bbox = im.getbbox()
    if not bbox:
        return im
    if padding <= 0:
        return im.crop(bbox)
    left, top, right, bottom = bbox
    return im.crop((
        max(0, left - padding),
        max(0, top - padding),
        min(im.width, right + padding),
        min(im.height, bottom + padding),
    ))


def _collect_border_samples(im: Image.Image, border: int) -> list[tuple[int, int, int]]:
    rgba = im.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    samples: list[tuple[int, int, int]] = []
    for y in range(h):
        for x in range(w):
            if x >= border and x < (w - border) and y >= border and y < (h - border):
                continue
            r, g, b, a = px[x, y]
            if a >= 12:
                samples.append((r, g, b))
    return samples


def _build_border_palette(samples: list[tuple[int, int, int]], max_colors: int = 6) -> list[tuple[int, int, int]]:
    if not samples:
        return [(255, 255, 255)]
    bins = Counter((r // 16, g // 16, b // 16) for r, g, b in samples)
    palette = []
    for (r, g, b), _ in bins.most_common(max_colors):
        palette.append((r * 16 + 8, g * 16 + 8, b * 16 + 8))
    return palette or [(255, 255, 255)]


def _nearest_palette_distance(r: int, g: int, b: int, palette: list[tuple[int, int, int]]) -> float:
    return min(
        ((r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2) ** 0.5
        for pr, pg, pb in palette
    )


def _extract_subject_cutout_local(src_path: Path, dst_path: Path, trim: bool = True) -> tuple[int, int]:
    im = Image.open(src_path).convert("RGBA")
    border = max(2, min(im.size) // 36)
    palette = _build_border_palette(_collect_border_samples(im, border))
    px = im.load()
    alpha = Image.new("L", im.size, 0)
    apx = alpha.load()
    bg_threshold = 26
    fg_threshold = 78
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a <= 0:
                apx[x, y] = 0
                continue
            dist = _nearest_palette_distance(r, g, b, palette)
            if dist <= bg_threshold:
                av = 0
            elif dist >= fg_threshold:
                av = a
            else:
                av = int(round(a * (dist - bg_threshold) / (fg_threshold - bg_threshold)))
            apx[x, y] = av
    alpha = _refine_alpha_edges(alpha)
    im.putalpha(alpha)
    im = _decontaminate_white_fringe(im)
    if trim:
        im = _trim_to_subject(im, padding=0)
    bbox = im.getbbox()
    if not bbox:
        raise ValueError("未识别到明显主体，请适当扩大框选范围后重试")
    nonzero = sum(1 for v in im.getchannel("A").getdata() if v > 8)
    total = max(1, im.width * im.height)
    if nonzero / total > 0.985:
        raise ValueError("背景分离不明显，请适当扩大框选范围后重试")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst_path, format="PNG", optimize=True)
    return im.size


def extract_subject_cutout(src_path: Path, dst_path: Path, trim: bool = True) -> tuple[int, int, str]:
    if not has_rembg():
        out_w, out_h = _extract_subject_cutout_local(src_path, dst_path, trim=trim)
        return out_w, out_h, "local"
    session = _get_rembg_session()
    data = src_path.read_bytes()
    kwargs = {
        "session": session,
        "post_process_mask": True,
    }
    try:
        out_bytes = remove(
            data,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=8,
            alpha_matting_erode_size=6,
            **kwargs,
        )
    except Exception:
        out_bytes = remove(data, **kwargs)

    im = Image.open(io.BytesIO(out_bytes)).convert("RGBA")
    alpha = _refine_alpha_edges(im.getchannel("A"))
    im.putalpha(alpha)
    im = _decontaminate_white_fringe(im)
    if trim:
        im = _trim_to_subject(im, padding=0)
    if not im.getbbox():
        raise ValueError("未识别到明显主体，请适当扩大框选范围后重试")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst_path, format="PNG", optimize=True)
    out_w, out_h = im.size
    return out_w, out_h, "rembg"


def _flood_clear_light_background(im: Image.Image, threshold: int = 235) -> None:
    rgba = im.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    stack = []
    seen = set()

    def is_light(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        return a > 0 and min(r, g, b) >= threshold

    for sx, sy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if is_light(sx, sy):
            stack.append((sx, sy))
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or x < 0 or y < 0 or x >= w or y >= h:
            continue
        if not is_light(x, y):
            continue
        seen.add((x, y))
        r, g, b, _ = px[x, y]
        px[x, y] = (r, g, b, 0)
        stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])


def postprocess_ai_cutout_png(src_path: Path, dst_path: Path, trim: bool = True) -> tuple[int, int]:
    """AI 成图常为白底，转为透明并裁切到主体。"""
    im = Image.open(src_path).convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 0 and min(r, g, b) > 238:
                px[x, y] = (r, g, b, 0)
    _flood_clear_light_background(im, threshold=235)
    if trim:
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst_path, format="PNG", optimize=True)
    return im.size
