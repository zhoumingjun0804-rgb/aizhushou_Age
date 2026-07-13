"""单图按预设尺寸批量导出（未勾选 AI 时等比裁切满图，勾选时用 Lovart AI 阔图）。"""
import json
import re
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_PRODUCT_TYPE = "xdt"
PRODUCT_TYPES = frozenset({"xdt", "hll"})
SIZES_FILES = {
    "xdt": BACKEND_DIR / "output_sizes.json",
    "hll": BACKEND_DIR / "output_sizes_hll.json",
}
JPEG_QUALITY = 85

SPLASH_SUBFRAME_HERO_WIDTH = 1440
SPLASH_SUBFRAME_HERO_HEIGHT = 2560


def is_splash_subframe_hero_source(width: int, height: int) -> bool:
    return width == SPLASH_SUBFRAME_HERO_WIDTH and height == SPLASH_SUBFRAME_HERO_HEIGHT

# 开屏「延展尺寸」固定尺寸手动上传槽位（小灯塔）
SPLASH_MANUAL_UPLOAD_SLOTS = (
    {"id": "manual_1440x2560", "name": "1440×2560", "width": 1440, "height": 2560},
    {"id": "manual_1125x2436", "name": "1125×2436", "width": 1125, "height": 2436},
    {"id": "manual_1536x2048", "name": "1536×2048", "width": 1536, "height": 2048},
    {"id": "manual_1668x2388", "name": "1668×2388", "width": 1668, "height": 2388},
)


def manual_upload_field_key(width: int, height: int) -> str:
    return f"manual_{width}x{height}"


def normalize_product_type(value: str | None) -> str:
    """URL/表单 type：xdt=小灯塔，hll=画啦啦；无效时默认小灯塔。"""
    raw = (value or "").strip().lower()
    aliases = {
        "xiaodengta": "xdt",
        "小灯塔": "xdt",
        "hualala": "hll",
        "画啦啦": "hll",
    }
    t = aliases.get(raw, raw)
    return t if t in PRODUCT_TYPES else DEFAULT_PRODUCT_TYPE


def sizes_config_path(product_type: str | None = None) -> Path:
    return SIZES_FILES[normalize_product_type(product_type)]


def safe_download_stem(name: str, default: str = "开屏") -> str:
    """上传图文件名（无扩展名）→ 安全下载前缀。"""
    stem = Path(name).stem if name else ""
    stem = re.sub(r'[/\\?%*:|"<>#\s]+', "_", stem.strip())
    stem = re.sub(r"_+", "_", stem).strip("_")
    return (stem[:80] if stem else default) or default


def load_output_sizes(config_path: Path | None = None, *, product_type: str | None = None) -> list[dict]:
    path = config_path or sizes_config_path(product_type)
    if not path.is_file():
        raise FileNotFoundError(f"缺少尺寸配置: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < 1:
        raise ValueError("output_sizes.json 须为非空数组")
    sizes = []
    for i, item in enumerate(data):
        w = int(item["width"])
        h = int(item["height"])
        if w < 1 or h < 1:
            raise ValueError(f"尺寸配置第 {i + 1} 项宽高无效")
        sizes.append({
            "id": str(item.get("id") or f"size_{i + 1}"),
            "name": str(item.get("name") or f"{w}×{h}"),
            "width": w,
            "height": h,
        })
    return sizes


def normalize_export_sizes(items: list) -> list[dict]:
    """校验前端提交的导出尺寸列表。"""
    if not isinstance(items, list) or len(items) < 1:
        raise ValueError("请至少选择一个导出尺寸")
    max_dim = 8192
    sizes: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"尺寸配置第 {i + 1} 项无效")
        w = int(item.get("width", 0))
        h = int(item.get("height", 0))
        if w < 1 or h < 1:
            raise ValueError(f"尺寸配置第 {i + 1} 项宽高无效")
        if w > max_dim or h > max_dim:
            raise ValueError(f"尺寸不能超过 {max_dim}px")
        key = (w, h)
        if key in seen:
            continue
        seen.add(key)
        sizes.append({
            "id": str(item.get("id") or f"custom_{w}x{h}"),
            "name": str(item.get("name") or f"{w}×{h}"),
            "width": w,
            "height": h,
        })
    if not sizes:
        raise ValueError("请至少选择一个导出尺寸")
    return sizes


def fit_image_cover_crop(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比放大后居中裁切，铺满目标画布（满图、不留白）。"""
    img = src.convert("RGBA")
    sw, sh = img.size
    scale = max(target_w / sw, target_h / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    big = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = max(0, (nw - target_w) // 2)
    y = max(0, (nh - target_h) // 2)
    return big.crop((x, y, x + target_w, y + target_h))


def fit_image_contain_blur_extend(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比缩放完整放入画布，不足区域用模糊背景补边（不裁切、不重绘主体）。"""
    img = src.convert("RGBA")
    sw, sh = img.size
    bg_scale = max(target_w / sw, target_h / sh)
    bg_w = max(1, int(round(sw * bg_scale)))
    bg_h = max(1, int(round(sh * bg_scale)))
    bg_big = img.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
    bx = max(0, (bg_w - target_w) // 2)
    by = max(0, (bg_h - target_h) // 2)
    bg = bg_big.crop((bx, by, bx + target_w, by + target_h))
    blur_radius = max(8, min(target_w, target_h) // 40)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    scale = min(target_w / sw, target_h / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    fitted = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = bg.copy()
    canvas.paste(fitted, ((target_w - nw) // 2, (target_h - nh) // 2), fitted)
    return canvas


def fit_image_contain_mirror_extend(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比放入画布后，用边缘镜像填充留白（供 GPT 扩边输入，非最终成图）。"""
    fitted, ox, oy = fit_contain_box(src, target_w, target_h)
    nw, nh = fitted.size
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    canvas.paste(fitted, (ox, oy), fitted)

    if ox > 0:
        sw = min(nw, ox)
        mirror = fitted.crop((0, 0, sw, nh)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if mirror.width != ox:
            mirror = mirror.resize((ox, nh), Image.Resampling.LANCZOS)
        canvas.paste(mirror, (0, oy))
    if ox + nw < target_w:
        rw = target_w - ox - nw
        sw = min(nw, rw)
        mirror = fitted.crop((nw - sw, 0, nw, nh)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if mirror.width != rw:
            mirror = mirror.resize((rw, nh), Image.Resampling.LANCZOS)
        canvas.paste(mirror, (ox + nw, oy))
    if oy > 0:
        sh = min(nh, oy)
        mirror = fitted.crop((0, 0, nw, sh)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if mirror.height != oy:
            mirror = mirror.resize((nw, oy), Image.Resampling.LANCZOS)
        canvas.paste(mirror, (ox, 0))
    if oy + nh < target_h:
        bh = target_h - oy - nh
        sh = min(nh, bh)
        mirror = fitted.crop((0, nh - sh, nw, nh)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if mirror.height != bh:
            mirror = mirror.resize((nw, bh), Image.Resampling.LANCZOS)
        canvas.paste(mirror, (ox, oy + nh))

    canvas.paste(fitted, (ox, oy), fitted)
    return canvas


def fit_image_contain_seamless_extend(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比放入画布后，从四边像素向外拉伸延伸，主体不变、扩边与画面边缘一致。"""
    fitted, ox, oy = fit_contain_box(src, target_w, target_h)
    nw, nh = fitted.size
    canvas = Image.new("RGBA", (target_w, target_h), fitted.getpixel((0, 0)))

    if ox > 0:
        strip = fitted.crop((0, 0, 1, nh))
        canvas.paste(strip.resize((ox, nh), Image.Resampling.BILINEAR), (0, oy))
    if ox + nw < target_w:
        rw = target_w - ox - nw
        strip = fitted.crop((nw - 1, 0, nw, nh))
        canvas.paste(strip.resize((rw, nh), Image.Resampling.BILINEAR), (ox + nw, oy))
    if oy > 0:
        strip = fitted.crop((0, 0, nw, 1))
        canvas.paste(strip.resize((nw, oy), Image.Resampling.BILINEAR), (ox, 0))
    if oy + nh < target_h:
        bh = target_h - oy - nh
        strip = fitted.crop((0, nh - 1, nw, nh))
        canvas.paste(strip.resize((nw, bh), Image.Resampling.BILINEAR), (ox, oy + nh))

    # 四角用相邻边条再拉一遍，避免纯色块
    if ox > 0 and oy > 0:
        corner = fitted.crop((0, 0, 1, 1)).resize((ox, oy), Image.Resampling.BILINEAR)
        canvas.paste(corner, (0, 0))
    if ox + nw < target_w and oy > 0:
        rw = target_w - ox - nw
        corner = fitted.crop((nw - 1, 0, nw, 1)).resize((rw, oy), Image.Resampling.BILINEAR)
        canvas.paste(corner, (ox + nw, 0))
    if ox > 0 and oy + nh < target_h:
        bh = target_h - oy - nh
        corner = fitted.crop((0, nh - 1, 1, nh)).resize((ox, bh), Image.Resampling.BILINEAR)
        canvas.paste(corner, (0, oy + nh))
    if ox + nw < target_w and oy + nh < target_h:
        rw = target_w - ox - nw
        bh = target_h - oy - nh
        corner = fitted.crop((nw - 1, nh - 1, nw, nh)).resize((rw, bh), Image.Resampling.BILINEAR)
        canvas.paste(corner, (ox + nw, oy + nh))

    canvas.paste(fitted, (ox, oy), fitted)
    return canvas


def fit_contain_box(src: Image.Image, target_w: int, target_h: int) -> tuple[Image.Image, int, int]:
    """等比缩放完整放入目标画布，返回贴合图与粘贴坐标。"""
    img = src.convert("RGBA")
    sw, sh = img.size
    scale = min(target_w / sw, target_h / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    fitted = img.resize((nw, nh), Image.Resampling.LANCZOS)
    ox = (target_w - nw) // 2
    oy = (target_h - nh) // 2
    return fitted, ox, oy


def fit_image_contain_on_canvas(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比缩放放入目标画布（不拉伸变形），居中放置。"""
    fitted, ox, oy = fit_contain_box(src, target_w, target_h)
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    canvas.paste(fitted, (ox, oy), fitted)
    return canvas


def compute_outpaint_feather(nw: int, nh: int) -> int:
    """扩边与原图衔接的羽化宽度（像素）。"""
    return max(28, min(80, min(nw, nh) // 10))


def build_edge_feather_mask(width: int, height: int, feather: int) -> Image.Image:
    """中心不透明、四边线性羽化的蒙版，用于衔接处柔和叠回。"""
    feather = max(1, min(feather, width // 2, height // 2))
    inner_w = max(1, width - feather * 2)
    inner_h = max(1, height - feather * 2)
    core = Image.new("L", (inner_w, inner_h), 255)
    mask = Image.new("L", (width, height), 0)
    mask.paste(core, (feather, feather))
    return mask.filter(ImageFilter.GaussianBlur(radius=max(2, feather / 2.5)))


def overlay_preserved_center(
    canvas: Image.Image,
    src: Image.Image,
    target_w: int,
    target_h: int,
) -> Image.Image:
    """将原图中心区域精确叠回，确保主体像素不被 AI 改写。"""
    return overlay_preserved_center_feathered(
        canvas, src, target_w, target_h, feather_px=0
    )


def overlay_preserved_center_feathered(
    canvas: Image.Image,
    src: Image.Image,
    target_w: int,
    target_h: int,
    *,
    feather_px: int | None = None,
) -> Image.Image:
    """羽化叠回原图：中心保持原像素，边缘与 AI 扩边柔和衔接。"""
    fitted, ox, oy = fit_contain_box(src, target_w, target_h)
    nw, nh = fitted.size
    out = canvas.convert("RGBA")
    layer = fitted.convert("RGBA")
    if feather_px is None:
        feather_px = compute_outpaint_feather(nw, nh)
    if feather_px > 0:
        r, g, b, a = layer.split()
        edge = build_edge_feather_mask(nw, nh, feather_px)
        a = ImageChops.multiply(a, edge)
        layer = Image.merge("RGBA", (r, g, b, a))
    out.paste(layer, (ox, oy), layer)
    return out


def render_splash_canvas(
    src: Image.Image,
    target_w: int,
    target_h: int,
    *,
    fit_mode: str = "extend",
    ai_canvas_fn: Callable[[Image.Image, int, int], Image.Image] | None = None,
) -> Image.Image:
    """extend=本地模糊补边；crop=裁切满图；AI 阔图失败时向上抛错。"""
    mode = (fit_mode or "extend").strip().lower()
    if mode not in ("extend", "crop"):
        mode = "extend"
    fallback_fn = fit_image_cover_crop if mode == "crop" else fit_image_contain_blur_extend
    if ai_canvas_fn:
        return ai_canvas_fn(src, target_w, target_h)
    return fallback_fn(src, target_w, target_h)


def save_canvas_compressed(canvas: Image.Image, out_path: Path, *, quality: int = JPEG_QUALITY) -> None:
    """保存为 JPEG，下载体积更小。"""
    rgba = canvas.convert("RGBA")
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.split()[3])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, format="JPEG", quality=quality, optimize=True, progressive=True)


def export_splash_subframe_sizes(
    input_path: Path,
    output_dir: Path,
    job_id: str,
    *,
    sizes: list[dict],
    generate_at_size: Callable[[Image.Image, int, int], Image.Image],
    source_basename: str = "开屏",
    jpeg_quality: int = JPEG_QUALITY,
    make_zip: bool = True,
) -> dict:
    """开屏拓展子画面：按尺寸直接 AI 生图（与生图同路径，无蒙版扩边）。"""
    if not sizes:
        raise ValueError("请至少选择一个导出尺寸")
    base = safe_download_stem(source_basename)
    output_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as src:
        src_img = src.convert("RGBA")
        orig_w, orig_h = src_img.size

    outputs: list[dict] = []
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for spec in sizes:
            tw, th = spec["width"], spec["height"]
            if is_splash_subframe_hero_source(tw, th) and is_splash_subframe_hero_source(orig_w, orig_h):
                canvas = fit_image_cover_crop(src_img, tw, th)
                item_source = "passthrough"
            else:
                canvas = generate_at_size(src_img, tw, th)
                item_source = "ai"
            filename = f"multi_{job_id}_{spec['id']}.jpg"
            out_path = output_dir / filename
            save_canvas_compressed(canvas, out_path, quality=jpeg_quality)
            download_name = f"{base}_{tw}x{th}.jpg"
            zf.write(out_path, arcname=download_name)
            outputs.append({
                "id": spec["id"],
                "name": spec["name"],
                "width": tw,
                "height": th,
                "filename": filename,
                "downloadName": download_name,
                "url": f"/outputs/{filename}",
                "fileSize": out_path.stat().st_size,
                "source": item_source,
            })

    zip_filename = None
    zip_url = None
    zip_download_name = None
    if make_zip:
        zip_filename = f"multi_{job_id}_all.zip"
        zip_download_name = f"{base}_全部尺寸.zip"
        zip_path = output_dir / zip_filename
        zip_path.write_bytes(zip_buffer.getvalue())
        zip_url = f"/outputs/{zip_filename}"

    return {
        "count": len(outputs),
        "images": outputs,
        "sourceBaseName": base,
        "originalWidth": orig_w,
        "originalHeight": orig_h,
        "zip_filename": zip_filename,
        "zip_download_name": zip_download_name if make_zip else None,
        "zip_url": zip_url,
        "backgroundMode": "ai",
    }


def export_multi_sizes(
    input_path: Path,
    output_dir: Path,
    job_id: str,
    *,
    config_path: Path | None = None,
    sizes: list[dict] | None = None,
    make_zip: bool = True,
    jpeg_quality: int = JPEG_QUALITY,
    source_basename: str = "开屏",
    use_ai: bool = False,
    fit_mode: str = "extend",
    ai_canvas_fn: Callable[[Image.Image, int, int], Image.Image] | None = None,
) -> dict:
    if sizes is None:
        sizes = load_output_sizes(config_path)
    if not sizes:
        raise ValueError("请至少选择一个导出尺寸")
    base = safe_download_stem(source_basename)
    output_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as src:
        src_img = src.convert("RGBA")
        orig_w, orig_h = src_img.size

    outputs: list[dict] = []
    zip_buffer = BytesIO()
    ai_fn = ai_canvas_fn if use_ai and ai_canvas_fn else None
    resolved_fit = (fit_mode or "extend").strip().lower()
    if resolved_fit not in ("extend", "crop"):
        resolved_fit = "extend"
    background_mode = "ai" if ai_fn else resolved_fit

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for spec in sizes:
            tw, th = spec["width"], spec["height"]
            canvas = render_splash_canvas(
                src_img, tw, th, fit_mode=resolved_fit, ai_canvas_fn=ai_fn
            )
            filename = f"multi_{job_id}_{spec['id']}.jpg"
            out_path = output_dir / filename
            save_canvas_compressed(canvas, out_path, quality=jpeg_quality)
            download_name = f"{base}_{tw}x{th}.jpg"
            zf.write(out_path, arcname=download_name)
            outputs.append({
                "id": spec["id"],
                "name": spec["name"],
                "width": tw,
                "height": th,
                "filename": filename,
                "downloadName": download_name,
                "url": f"/outputs/{filename}",
                "fileSize": out_path.stat().st_size,
            })

    zip_filename = None
    zip_url = None
    zip_download_name = None
    if make_zip:
        zip_filename = f"multi_{job_id}_all.zip"
        zip_download_name = f"{base}_全部尺寸.zip"
        zip_path = output_dir / zip_filename
        zip_path.write_bytes(zip_buffer.getvalue())
        zip_url = f"/outputs/{zip_filename}"

    return {
        "count": len(outputs),
        "images": outputs,
        "sourceBaseName": base,
        "originalWidth": orig_w,
        "originalHeight": orig_h,
        "zip_filename": zip_filename,
        "zip_download_name": zip_download_name if make_zip else None,
        "zip_url": zip_url,
        "backgroundMode": background_mode,
    }


def pick_closest_manual_source(
    sources: list[tuple[dict, Image.Image]],
    target_w: int,
    target_h: int,
) -> tuple[dict, Image.Image]:
    """按宽高比，从已上传源图中选最接近目标尺寸的一张。"""
    target_ratio = target_w / max(target_h, 1)
    return min(
        sources,
        key=lambda item: abs(item[0]["width"] / max(item[0]["height"], 1) - target_ratio),
    )


def load_manual_splash_sources(
    fields: dict,
    upload_dir: Path,
    job_id: str,
) -> list[tuple[dict, Image.Image, Path]]:
    """读取四个固定尺寸上传槽位；返回 (slot, rgba图, 临时文件路径)。"""
    loaded: list[tuple[dict, Image.Image, Path]] = []
    for spec in SPLASH_MANUAL_UPLOAD_SLOTS:
        key = manual_upload_field_key(spec["width"], spec["height"])
        field = fields.get(key)
        if not field or not isinstance(field, dict) or not field.get("data"):
            continue
        tmp = upload_dir / f"manual_{job_id}_{spec['width']}x{spec['height']}.img"
        tmp.write_bytes(field["data"])
        try:
            with Image.open(tmp) as src:
                img = src.convert("RGBA")
            if img.size != (spec["width"], spec["height"]):
                raise ValueError(
                    f"上传尺寸错误：{spec['name']} 窗口收到 {img.size[0]}×{img.size[1]}，"
                    f"仅接受 {spec['width']}×{spec['height']}"
                )
            loaded.append((spec, img, tmp))
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    return loaded


def export_manual_splash_uploads(
    fields: dict,
    upload_dir: Path,
    output_dir: Path,
    job_id: str,
    *,
    source_basename: str,
    jpeg_quality: int = JPEG_QUALITY,
    product_type: str = "xdt",
) -> tuple[list[dict], list[dict]]:
    """用已上传的固定尺寸图，按等比裁切导出 output_sizes 中的全部目标尺寸。

    返回 (延展尺寸列表, 源图列表)；源图用于 ZIP 打包，不混入延展结果网格。
    """
    sources = load_manual_splash_sources(fields, upload_dir, job_id)
    tmp_paths = [tmp for _, _, tmp in sources]
    try:
        if not sources:
            return [], []
        target_sizes = load_output_sizes(product_type=product_type)
        base = safe_download_stem(source_basename)
        output_dir.mkdir(parents=True, exist_ok=True)
        source_pairs = [(spec, img) for spec, img, _ in sources]
        source_outputs: list[dict] = []
        for spec, img, _ in sources:
            sw, sh = spec["width"], spec["height"]
            src_filename = f"multi_{job_id}_{spec['id']}_src.jpg"
            src_path = output_dir / src_filename
            save_canvas_compressed(img, src_path, quality=jpeg_quality)
            source_outputs.append({
                "id": spec["id"],
                "name": spec["name"],
                "width": sw,
                "height": sh,
                "filename": src_filename,
                "downloadName": f"{base}_{sw}x{sh}.jpg",
                "url": f"/outputs/{src_filename}",
                "fileSize": src_path.stat().st_size,
                "source": "manual_src",
            })
        outputs: list[dict] = []
        for spec in target_sizes:
            tw, th = spec["width"], spec["height"]
            _slot, src_img = pick_closest_manual_source(source_pairs, tw, th)
            canvas = fit_image_cover_crop(src_img, tw, th)
            filename = f"multi_{job_id}_{spec['id']}.jpg"
            out_path = output_dir / filename
            save_canvas_compressed(canvas, out_path, quality=jpeg_quality)
            outputs.append({
                "id": spec["id"],
                "name": spec["name"],
                "width": tw,
                "height": th,
                "filename": filename,
                "downloadName": f"{base}_{tw}x{th}.jpg",
                "url": f"/outputs/{filename}",
                "fileSize": out_path.stat().st_size,
                "source": "manual",
            })
        return outputs, source_outputs
    finally:
        for path in tmp_paths:
            try:
                path.unlink()
            except OSError:
                pass


def merge_multi_size_export_results(
    result: dict,
    extra_outputs: list[dict],
    output_dir: Path,
) -> dict:
    """将手动上传尺寸并入批量导出结果与 ZIP。"""
    if not extra_outputs:
        return result
    images = list(result.get("images") or []) + extra_outputs
    zip_filename = result.get("zip_filename")
    if zip_filename:
        zip_path = output_dir / zip_filename
        if zip_path.is_file():
            with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
                for item in extra_outputs:
                    out_path = output_dir / item["filename"]
                    if out_path.is_file():
                        zf.write(out_path, arcname=item["downloadName"])
    merged = dict(result)
    merged["images"] = images
    merged["count"] = len(images)
    return merged


def build_manual_only_export_result(
    outputs: list[dict],
    output_dir: Path,
    job_id: str,
    *,
    source_basename: str,
    source_outputs: list[dict] | None = None,
) -> dict:
    """仅含手动上传尺寸的导出结果（无 AI 扩边）；ZIP 含延展尺寸与源图。"""
    if not outputs:
        raise ValueError("请至少上传一个固定尺寸图片")
    base = safe_download_stem(source_basename)
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in outputs:
            out_path = output_dir / item["filename"]
            if out_path.is_file():
                zf.write(out_path, arcname=item["downloadName"])
        for item in source_outputs or []:
            out_path = output_dir / item["filename"]
            if out_path.is_file():
                zf.write(out_path, arcname=item["downloadName"])
    zip_filename = f"multi_{job_id}_all.zip"
    zip_path = output_dir / zip_filename
    zip_path.write_bytes(zip_buffer.getvalue())
    return {
        "count": len(outputs),
        "images": outputs,
        "sourceBaseName": base,
        "originalWidth": outputs[0]["width"],
        "originalHeight": outputs[0]["height"],
        "zip_filename": zip_filename,
        "zip_download_name": f"{base}_全部尺寸.zip",
        "zip_url": f"/outputs/{zip_filename}",
        "backgroundMode": "manual",
    }
