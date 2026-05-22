"""按指定区域裁切并输出为目标宽高。"""
from pathlib import Path

from PIL import Image


def crop_image_to_size(
    input_path: Path,
    output_path: Path,
    x: int,
    y: int,
    crop_w: int,
    crop_h: int,
    output_w: int,
    output_h: int,
) -> dict:
    x = max(0, int(x))
    y = max(0, int(y))
    crop_w = max(1, int(crop_w))
    crop_h = max(1, int(crop_h))
    output_w = max(1, int(output_w))
    output_h = max(1, int(output_h))

    with Image.open(input_path) as img:
        img = img.convert("RGBA")
        src_w, src_h = img.size
        if x + crop_w > src_w:
            crop_w = src_w - x
        if y + crop_h > src_h:
            crop_h = src_h - y
        if crop_w < 1 or crop_h < 1:
            raise ValueError("裁切区域超出图片范围")
        cropped = img.crop((x, y, x + crop_w, y + crop_h))
        if (crop_w, crop_h) != (output_w, output_h):
            cropped = cropped.resize((output_w, output_h), Image.Resampling.LANCZOS)
        if output_path.suffix.lower() in (".jpg", ".jpeg"):
            cropped.convert("RGB").save(output_path, format="JPEG", quality=92, optimize=True)
        else:
            cropped.save(output_path, format="PNG", optimize=True)

    return {
        "width": output_w,
        "height": output_h,
        "cropX": x,
        "cropY": y,
        "cropWidth": crop_w,
        "cropHeight": crop_h,
    }
