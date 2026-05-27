"""单图按预设尺寸批量导出（本地模糊延展或 Lovart AI 阔图）。"""
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


def _background_cover_blur(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比放大裁切铺满画布后高斯模糊，用作延展背景。"""
    img = src.convert("RGBA")
    sw, sh = img.size
    scale = max(target_w / sw, target_h / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    big = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = max(0, (nw - target_w) // 2)
    y = max(0, (nh - target_h) // 2)
    bg = big.crop((x, y, x + target_w, y + target_h))
    radius = max(12, min(target_w, target_h) // 25)
    return bg.filter(ImageFilter.GaussianBlur(radius=radius))


def render_splash_canvas(
    src: Image.Image,
    target_w: int,
    target_h: int,
    *,
    ai_canvas_fn: Callable[[Image.Image, int, int], Image.Image] | None = None,
) -> Image.Image:
    """优先 AI 阔图；失败时回退本地模糊延展。"""
    if ai_canvas_fn:
        try:
            return ai_canvas_fn(src, target_w, target_h)
        except Exception as e:
            print(f"[MULTI-SIZE] AI 阔图 {target_w}x{target_h} 失败，回退本地延展: {e}")
    return fit_image_to_canvas(src, target_w, target_h)


def fit_image_to_canvas(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比缩放至目标画布内完整显示，空白区用同源图模糊延展（非白底）。"""
    img = src.convert("RGBA")
    src_w, src_h = img.size
    canvas = _background_cover_blur(img, target_w, target_h)
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas


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
    make_zip: bool = True,
    jpeg_quality: int = JPEG_QUALITY,
    source_basename: str = "开屏",
    use_ai: bool = False,
    ai_canvas_fn: Callable[[Image.Image, int, int], Image.Image] | None = None,
) -> dict:
    sizes = load_output_sizes(config_path)
    base = safe_download_stem(source_basename)
    output_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as src:
        src_img = src.convert("RGBA")
        orig_w, orig_h = src_img.size

    outputs: list[dict] = []
    zip_buffer = BytesIO()
    ai_fn = ai_canvas_fn if use_ai and ai_canvas_fn else None

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for spec in sizes:
            tw, th = spec["width"], spec["height"]
            canvas = render_splash_canvas(src_img, tw, th, ai_canvas_fn=ai_fn)
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
        "backgroundMode": "ai" if ai_fn else "local",
    }
