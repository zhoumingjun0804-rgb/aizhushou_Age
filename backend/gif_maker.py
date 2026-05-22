"""合成按钮呼吸动效 GIF：静态底图 + 透明按钮图层。"""
import math
from io import BytesIO
from pathlib import Path

from PIL import Image

DEFAULT_FPS = 12
DEFAULT_DURATION_SEC = 1.6
SCALE_PRESETS = {"weak": 0.04, "medium": 0.07, "strong": 0.11}
TRANSPARENCY_INDEX = 255
ALPHA_CUTOFF = 128


def _paste_button(
    background: Image.Image,
    button: Image.Image,
    scale: float,
    offset_x: int = 0,
    offset_y: int = 0,
) -> Image.Image:
    bg = background.convert("RGBA")
    btn = button.convert("RGBA")
    bw = max(1, int(round(btn.width * scale)))
    bh = max(1, int(round(btn.height * scale)))
    if (bw, bh) != btn.size:
        btn = btn.resize((bw, bh), Image.Resampling.LANCZOS)
    x = (bg.width - bw) // 2 + int(offset_x)
    y = (bg.height - bh) // 2 + int(offset_y)
    frame = bg.copy()
    frame.paste(btn, (x, y), btn)
    return frame


def _apply_alpha_mask(palette_img: Image.Image, alpha: Image.Image) -> Image.Image:
    mask = Image.eval(alpha, lambda a: TRANSPARENCY_INDEX if a < ALPHA_CUTOFF else 0)
    palette_img.paste(TRANSPARENCY_INDEX, mask)
    palette_img.info["transparency"] = TRANSPARENCY_INDEX
    return palette_img


def _rgba_to_palette(frame: Image.Image) -> Image.Image:
    """RGBA → 256 色调色板，保留透明像素（避免 convert('RGB') 把透明变黑）。"""
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    p = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
    return _apply_alpha_mask(p, alpha)


def _quantize_to_shared_palette(frame: Image.Image, palette_ref: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    p = rgb.quantize(palette=palette_ref, dither=Image.Dither.NONE)
    return _apply_alpha_mask(p, alpha)


def _save_transparent_gif(frames: list[Image.Image], output_path: Path, frame_ms: int) -> None:
    if not frames:
        raise ValueError("no frames")
    first_p = _rgba_to_palette(frames[0])
    palette_frames = [first_p]
    for frame in frames[1:]:
        palette_frames.append(_quantize_to_shared_palette(frame, first_p))
    first_p.save(
        output_path,
        save_all=True,
        append_images=palette_frames[1:],
        duration=frame_ms,
        loop=0,
        optimize=True,
        disposal=2,
    )


def make_breathing_gif(
    background_path: Path,
    button_path: Path,
    output_path: Path,
    *,
    intensity: str = "medium",
    duration_sec: float = DEFAULT_DURATION_SEC,
    fps: int = DEFAULT_FPS,
    offset_x: int = 0,
    offset_y: int = 0,
) -> dict:
    amplitude = SCALE_PRESETS.get(intensity, SCALE_PRESETS["medium"])
    fps = max(6, min(24, int(fps)))
    duration_sec = max(0.8, min(5.0, float(duration_sec)))
    frame_count = max(8, int(round(duration_sec * fps)))
    frame_ms = int(1000 / fps)

    with Image.open(background_path) as bg_raw, Image.open(button_path) as btn_raw:
        background = bg_raw.convert("RGBA")
        button = btn_raw.convert("RGBA")

    frames: list[Image.Image] = []
    for i in range(frame_count):
        t = i / frame_count
        scale = 1.0 + amplitude * math.sin(2 * math.pi * t)
        frames.append(_paste_button(background, button, scale, offset_x, offset_y))

    _save_transparent_gif(frames, output_path, frame_ms)

    return {
        "width": background.width,
        "height": background.height,
        "frameCount": frame_count,
        "fps": fps,
        "durationSec": duration_sec,
        "intensity": intensity,
        "fileSize": output_path.stat().st_size,
    }
