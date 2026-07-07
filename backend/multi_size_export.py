"""单图按预设尺寸批量导出（未勾选 AI 时等比裁切满图，勾选时用 Lovart AI 阔图）。"""
import json
import re
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFilter

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_PRODUCT_TYPE = "xdt"
PRODUCT_TYPES = frozenset({"xdt", "hll"})
SIZES_FILES = {
    "xdt": BACKEND_DIR / "output_sizes.json",
    "hll": BACKEND_DIR / "output_sizes_hll.json",
}
JPEG_QUALITY = 85


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


def render_splash_canvas(
    src: Image.Image,
    target_w: int,
    target_h: int,
    *,
    fit_mode: str = "extend",
    ai_canvas_fn: Callable[[Image.Image, int, int], Image.Image] | None = None,
) -> Image.Image:
    """extend=本地模糊补边；crop=裁切满图；勾选 AI 时走 img2img，失败回退 extend/crop。"""
    mode = (fit_mode or "extend").strip().lower()
    if mode not in ("extend", "crop"):
        mode = "extend"
    fallback_fn = fit_image_cover_crop if mode == "crop" else fit_image_contain_blur_extend
    if ai_canvas_fn:
        try:
            return ai_canvas_fn(src, target_w, target_h)
        except Exception as e:
            label = "裁切满图" if mode == "crop" else "模糊补边延展"
            print(f"[MULTI-SIZE] AI 阔图 {target_w}x{target_h} 失败，回退{label}: {e}")
    return fallback_fn(src, target_w, target_h)


def save_canvas_compressed(canvas: Image.Image, out_path: Path, *, quality: int = JPEG_QUALITY) -> None:
    """保存为 JPEG，下载体积更小。"""
    rgba = canvas.convert("RGBA")
    bg = Image.new("RGB", rgba.size)
    bg.paste(rgba, mask=rgba.split()[3])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, format="JPEG", quality=quality, optimize=True, progressive=True)


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
