"""单图按预设尺寸批量导出（等比缩放 + 居中铺底，不裁切画面内容）。"""
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_SIZES_FILE = BACKEND_DIR / "output_sizes.json"
JPEG_QUALITY = 85


def safe_download_stem(name: str, default: str = "开屏") -> str:
    """上传图文件名（无扩展名）→ 安全下载前缀。"""
    stem = Path(name).stem if name else ""
    stem = re.sub(r'[/\\?%*:|"<>#\s]+', "_", stem.strip())
    stem = re.sub(r"_+", "_", stem).strip("_")
    return (stem[:80] if stem else default) or default


def load_output_sizes(config_path: Path | None = None) -> list[dict]:
    path = config_path or DEFAULT_SIZES_FILE
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


def fit_image_to_canvas(
    src: Image.Image,
    target_w: int,
    target_h: int,
    *,
    bg_rgba: tuple[int, int, int, int] = (255, 255, 255, 0),
) -> Image.Image:
    """等比缩放至目标画布内完整显示，居中放置，不拉伸变形。"""
    img = src.convert("RGBA")
    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (target_w, target_h), bg_rgba)
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas


def save_canvas_compressed(canvas: Image.Image, out_path: Path, *, quality: int = JPEG_QUALITY) -> None:
    """保存为 JPEG（白底），下载体积更小。"""
    rgba = canvas.convert("RGBA")
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
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
) -> dict:
    sizes = load_output_sizes(config_path)
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
            canvas = fit_image_to_canvas(src_img, tw, th)
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
    }
