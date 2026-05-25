"""规范延展：从设计图框选 Logo/IP，按模板尺寸与放置区合成输出。"""
from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageFilter

BACKEND_DIR = Path(__file__).resolve().parent
LAYOUT_PRESETS_DIR = BACKEND_DIR / "layout_presets"
JPEG_QUALITY = 88


def _clamp_roi(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    x = max(0, min(int(x), img_w - 1))
    y = max(0, min(int(y), img_h - 1))
    w = max(1, min(int(w), img_w - x))
    h = max(1, min(int(h), img_h - y))
    return x, y, w, h


def safe_stem(name: str, default: str = "延展") -> str:
    stem = Path(name).stem if name else ""
    stem = re.sub(r'[/\\?%*:|"<>#\s]+', "_", stem.strip())
    stem = re.sub(r"_+", "_", stem).strip("_")
    return (stem[:80] if stem else default) or default


def list_layout_presets() -> list[dict]:
    packs: list[dict] = []
    if not LAYOUT_PRESETS_DIR.is_dir():
        return packs
    for manifest_path in sorted(LAYOUT_PRESETS_DIR.glob("*/manifest.json")):
        pack_dir = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pack_id = str(manifest.get("id") or pack_dir.name)
        templates: list[dict] = []
        for tid in manifest.get("templates") or []:
            tpl_path = pack_dir / f"{tid}.json"
            if not tpl_path.is_file():
                continue
            try:
                tpl = json.loads(tpl_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            templates.append({
                "id": tpl.get("id", tid),
                "name": tpl.get("name", tid),
                "width": int(tpl["width"]),
                "height": int(tpl["height"]),
            })
        if templates:
            packs.append({
                "id": pack_id,
                "name": manifest.get("name", pack_id),
                "description": manifest.get("description", ""),
                "templates": templates,
            })
    return packs


def load_template(pack_id: str, template_id: str) -> dict:
    tpl_path = LAYOUT_PRESETS_DIR / pack_id / f"{template_id}.json"
    if not tpl_path.is_file():
        raise FileNotFoundError(f"找不到模板: {pack_id}/{template_id}")
    tpl = json.loads(tpl_path.read_text(encoding="utf-8"))
    tpl["pack_id"] = pack_id
    return tpl


def _region_box(template: dict, key: str) -> tuple[int, int, int, int]:
    tw = int(template["width"])
    th = int(template["height"])
    reg = template["regions"][key]
    x = int(reg.get("x", 0))
    y = int(reg.get("y", 0))
    w = int(reg.get("w", tw))
    h = int(reg.get("h", th))
    x = max(0, min(x, tw - 1))
    y = max(0, min(y, th - 1))
    w = max(1, min(w, tw - x))
    h = max(1, min(h, th - y))
    return x, y, w, h


def _anchor_offset(anchor: str, region_w: int, region_h: int, elem_w: int, elem_h: int) -> tuple[int, int]:
    anchor = (anchor or "center").lower()
    if anchor in ("center", "middle"):
        return (region_w - elem_w) // 2, (region_h - elem_h) // 2
    if anchor in ("top-left", "left-top", "nw"):
        return 0, 0
    if anchor in ("top-right", "right-top", "ne"):
        return region_w - elem_w, 0
    if anchor in ("bottom-left", "left-bottom", "sw"):
        return 0, region_h - elem_h
    if anchor in ("bottom-right", "right-bottom", "se"):
        return region_w - elem_w, region_h - elem_h
    if anchor in ("top", "north"):
        return (region_w - elem_w) // 2, 0
    if anchor in ("bottom", "south"):
        return (region_w - elem_w) // 2, region_h - elem_h
    return (region_w - elem_w) // 2, (region_h - elem_h) // 2


def _fit_element(src: Image.Image, max_w: int, max_h: int) -> Image.Image:
    img = src.convert("RGBA")
    sw, sh = img.size
    scale = min(max_w / sw, max_h / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def _paste_in_region(
    canvas: Image.Image,
    element: Image.Image,
    region: tuple[int, int, int, int],
    anchor: str = "center",
) -> None:
    rx, ry, rw, rh = region
    fitted = _fit_element(element, rw, rh)
    ox, oy = _anchor_offset(anchor, rw, rh, fitted.width, fitted.height)
    canvas.paste(fitted, (rx + ox, ry + oy), fitted)


def build_background_cover(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比放大裁切铺满画布（MVP 本地背景）。"""
    img = src.convert("RGBA")
    sw, sh = img.size
    scale = max(target_w / sw, target_h / sh)
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    big = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = max(0, (nw - target_w) // 2)
    y = max(0, (nh - target_h) // 2)
    return big.crop((x, y, x + target_w, y + target_h))


def build_background_blur_extend(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """模糊延展背景：边缘更自然，仍不依赖 AI。"""
    base = build_background_cover(src, target_w, target_h)
    blurred = build_background_cover(src, target_w, target_h)
    blurred = blurred.filter(ImageFilter.GaussianBlur(radius=max(8, min(target_w, target_h) // 40)))
    sharp = build_background_cover(src, int(target_w * 0.92), int(target_h * 0.92))
    canvas = blurred.copy()
    sx = (target_w - sharp.width) // 2
    sy = (target_h - sharp.height) // 2
    canvas.paste(sharp, (sx, sy), sharp)
    return canvas


def crop_roi(src: Image.Image, roi: tuple[int, int, int, int]) -> Image.Image:
    x, y, w, h = roi
    return src.crop((x, y, x + w, y + h))


def composite_layout(
    source: Image.Image,
    template: dict,
    logo_roi: tuple[int, int, int, int],
    ip_roi: tuple[int, int, int, int],
    *,
    background_builder: Callable[[Image.Image, int, int], Image.Image] | None = None,
) -> Image.Image:
    tw = int(template["width"])
    th = int(template["height"])
    builder = background_builder or build_background_blur_extend
    canvas = builder(source, tw, th)

    logo = crop_roi(source, logo_roi)
    ip = crop_roi(source, ip_roi)

    ip_region = _region_box(template, "ip")
    logo_region = _region_box(template, "logo")
    ip_anchor = template["regions"]["ip"].get("anchor", "center")
    logo_anchor = template["regions"]["logo"].get("anchor", "center")

    _paste_in_region(canvas, ip, ip_region, ip_anchor)
    _paste_in_region(canvas, logo, logo_region, logo_anchor)
    return canvas


def save_layout_jpeg(canvas: Image.Image, out_path: Path) -> None:
    rgba = canvas.convert("RGBA")
    bg = Image.new("RGB", rgba.size, (255, 255, 255))
    bg.paste(rgba, mask=rgba.split()[3])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)


def export_layout_extend(
    input_path: Path,
    output_dir: Path,
    job_id: str,
    pack_id: str,
    logo_roi: tuple[int, int, int, int],
    ip_roi: tuple[int, int, int, int],
    *,
    template_ids: list[str] | None = None,
    use_ai_background: bool = False,
    ai_background_fn: Callable[[Image.Image, dict], Image.Image] | None = None,
    source_basename: str = "延展",
) -> dict:
    manifest_path = LAYOUT_PRESETS_DIR / pack_id / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"找不到规范包: {pack_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = template_ids or list(manifest.get("templates") or [])
    if not ids:
        raise ValueError(f"规范包 {pack_id} 未配置任何模板")

    with Image.open(input_path) as im:
        source = im.convert("RGBA")
        img_w, img_h = source.size

    logo_roi = _clamp_roi(*logo_roi, img_w, img_h)
    ip_roi = _clamp_roi(*ip_roi, img_w, img_h)

    base = safe_stem(source_basename)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict] = []
    zip_buffer = BytesIO()

    bg_builder = build_background_blur_extend
    if use_ai_background and ai_background_fn:
        def _ai_builder(src: Image.Image, tw: int, th: int) -> Image.Image:
            return ai_background_fn(src, {"width": tw, "height": th})

        bg_builder = _ai_builder

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for tid in ids:
            template = load_template(pack_id, tid)
            canvas = composite_layout(
                source,
                template,
                logo_roi,
                ip_roi,
                background_builder=bg_builder,
            )
            fname = f"{base}_{template['id']}_{template['width']}x{template['height']}.jpg"
            out_path = output_dir / f"layout_{job_id}_{fname}"
            save_layout_jpeg(canvas, out_path)
            rel = out_path.name
            zf.write(out_path, arcname=fname)
            outputs.append({
                "templateId": template["id"],
                "name": template.get("name", tid),
                "width": template["width"],
                "height": template["height"],
                "filename": rel,
                "download_url": f"/outputs/{rel}",
            })

    zip_name = f"layout_{job_id}_{base}_全部尺寸.zip"
    zip_path = output_dir / zip_name
    zip_path.write_bytes(zip_buffer.getvalue())

    return {
        "packId": pack_id,
        "outputs": outputs,
        "zip_url": f"/outputs/{zip_name}",
        "zip_download_name": f"{base}_规范延展.zip",
        "sourceBaseName": base,
        "backgroundMode": "ai" if use_ai_background and ai_background_fn else "local",
    }


def parse_psd_layout(psd_path: Path) -> dict:
    """从规范 PSD 解析画布与 Logo/IP 放置区（红色矩形图层）。"""
    try:
        from psd_tools import PSDImage
    except ImportError as e:
        raise RuntimeError("需要安装 psd-tools: pip install psd-tools") from e

    psd = PSDImage.open(psd_path)
    w, h = psd.width, psd.height

    ip_layer_names = ("矩形 1", "ip_region", "IP主体区域", "ip")
    logo_layer_names = ("矩形 1 拷贝", "logo_region", "logo区域", "logo")

    def find_layer(names: tuple[str, ...]) -> tuple[int, int, int, int] | None:
        for layer in psd.descendants():
            if layer.name not in names:
                continue
            x1, y1, x2, y2 = layer.bbox
            bw, bh = x2 - x1, y2 - y1
            if bw < 8 or bh < 8:
                continue
            if layer.name in ("IP主体区域", "logo区域") and (bw < w * 0.15 or bh < h * 0.08):
                continue
            return x1, y1, bw, bh
        return None

    ip_box = find_layer(ip_layer_names)
    logo_box = find_layer(logo_layer_names)
    if not ip_box or not logo_box:
        raise ValueError("PSD 中未找到 IP/Logo 放置区图层，请保留红色矩形或命名为 ip_region / logo_region")

    def clamp_box(x: int, y: int, bw: int, bh: int) -> dict:
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        bw = max(1, min(bw, w - x))
        bh = max(1, min(bh, h - y))
        return {"x": x, "y": y, "w": bw, "h": bh, "anchor": "center"}

    stem = psd_path.stem
    tpl_id = re.sub(r"[^a-zA-Z0-9_\-]+", "_", stem).strip("_").lower() or "template"
    return {
        "id": tpl_id,
        "name": stem,
        "width": w,
        "height": h,
        "regions": {
            "ip": clamp_box(*ip_box),
            "logo": clamp_box(*logo_box),
        },
        "source_psd": psd_path.name,
    }
