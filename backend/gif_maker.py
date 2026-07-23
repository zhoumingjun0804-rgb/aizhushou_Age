"""合成按钮动效 GIF：底图（静图或动图）+ 一个或多个透明图层（呼吸 / 浮动 / 摇摆 / 旋转）。"""
import hashlib
import math
from io import BytesIO
from pathlib import Path

from PIL import Image

DEFAULT_FPS = 12
DEFAULT_DURATION_SEC = 1.6
SCALE_PRESETS = {"weak": 0.04, "medium": 0.07, "strong": 0.11}
FLOAT_PRESETS = {"weak": 4, "medium": 8, "strong": 14}
SWAY_PRESETS = {"weak": 4, "medium": 8, "strong": 14}
ROTATE_PRESETS = {"weak": 2.0, "medium": 4.0, "strong": 7.0}
MIN_BUTTON_SCALE = 0.1
MAX_BUTTON_SCALE = 3.0
TRANSPARENCY_INDEX = 255
ALPHA_CUTOFF = 128
GIF_EFFECT_TYPES = ("breathing", "float", "sway", "rotate")


def _clamp_button_rect(
    x: int,
    y: int,
    width: int,
    height: int,
    bg_w: int,
    bg_h: int,
) -> tuple[int, int, int, int]:
    # 允许图层移出底图；超出部分在合成时由 paste 自然裁掉
    _ = (bg_w, bg_h)
    width = max(1, int(width))
    height = max(1, int(height))
    return int(x), int(y), width, height


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


def _save_transparent_gif(
    frames: list[Image.Image],
    output_path: Path,
    frame_ms: int | list[int],
) -> None:
    if not frames:
        raise ValueError("no frames")
    if isinstance(frame_ms, list):
        durations = [max(20, int(d or 100)) for d in frame_ms]
        if len(durations) < len(frames):
            durations.extend([durations[-1] if durations else 100] * (len(frames) - len(durations)))
        durations = durations[: len(frames)]
    else:
        durations = max(20, int(frame_ms or 100))
    first_p = _rgba_to_palette(frames[0])
    palette_frames = [first_p]
    for frame in frames[1:]:
        palette_frames.append(_quantize_to_shared_palette(frame, first_p))
    first_p.save(
        output_path,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def _load_background_timeline(path: Path) -> tuple[list[Image.Image], list[int], bool]:
    """读取底图时间轴。动图 GIF 保留各帧与时长；静图返回单帧。"""
    with Image.open(path) as im:
        n_frames = getattr(im, "n_frames", 1) or 1
        animated = bool(getattr(im, "is_animated", False) and n_frames > 1)
        if not animated:
            return [im.convert("RGBA").copy()], [100], False

        frames: list[Image.Image] = []
        durations: list[int] = []
        try:
            idx = 0
            while True:
                frames.append(im.copy().convert("RGBA"))
                try:
                    duration = max(20, int(im.info.get("duration") or 100))
                except (TypeError, ValueError):
                    duration = 100
                durations.append(duration)
                idx += 1
                if idx >= 300:
                    break
                im.seek(idx)
        except EOFError:
            pass
        if not frames:
            raise ValueError("底图 GIF 没有可用帧")
        return frames, durations, True


def compute_combined_transform(t: float, effects: list[str], intensity: str) -> dict[str, float]:
    """根据启用的动效计算单帧变换参数。"""
    scale = 1.0
    dx = 0.0
    dy = 0.0
    angle = 0.0
    phase = 2 * math.pi * t
    if "breathing" in effects:
        amp = SCALE_PRESETS.get(intensity, SCALE_PRESETS["medium"])
        scale *= 1.0 + amp * math.sin(phase)
    if "float" in effects:
        dy += FLOAT_PRESETS.get(intensity, FLOAT_PRESETS["medium"]) * math.sin(phase)
    if "sway" in effects:
        dx += SWAY_PRESETS.get(intensity, SWAY_PRESETS["medium"]) * math.sin(phase)
    if "rotate" in effects:
        angle += ROTATE_PRESETS.get(intensity, ROTATE_PRESETS["medium"]) * math.sin(phase)
    return {"scale": scale, "dx": dx, "dy": dy, "angle": angle}


def _paste_button_transformed(
    background: Image.Image,
    button: Image.Image,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    scale: float = 1.0,
    dx: float = 0.0,
    dy: float = 0.0,
    angle_deg: float = 0.0,
) -> Image.Image:
    bg = background.convert("RGBA")
    btn = button.convert("RGBA")
    base_w = max(1, int(width))
    base_h = max(1, int(height))
    bw = max(1, int(round(base_w * scale)))
    bh = max(1, int(round(base_h * scale)))
    if (bw, bh) != btn.size:
        btn = btn.resize((bw, bh), Image.Resampling.LANCZOS)
    if angle_deg:
        btn = btn.rotate(-angle_deg, resample=Image.Resampling.BICUBIC, expand=True)
    cx = x + base_w / 2.0 + dx
    cy = y + base_h / 2.0 + dy
    paste_x = int(round(cx - btn.width / 2.0))
    paste_y = int(round(cy - btn.height / 2.0))
    frame = bg.copy()
    frame.paste(btn, (paste_x, paste_y), btn)
    return frame


def _resolve_layer_rect(
    layout: dict | None,
    img: Image.Image,
    bg_w: int,
    bg_h: int,
    *,
    offset_x: int = 0,
    offset_y: int = 0,
    button_scale: float = 1.0,
    fallback_rect: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    if layout:
        w = layout.get("w") or layout.get("width")
        h = layout.get("h") or layout.get("height")
        if w and h:
            x = int(layout.get("x", 0))
            y = int(layout.get("y", 0))
            return _clamp_button_rect(x, y, int(w), int(h), bg_w, bg_h)
    if fallback_rect:
        return _clamp_button_rect(*fallback_rect, bg_w, bg_h)
    base_scale = max(MIN_BUTTON_SCALE, min(MAX_BUTTON_SCALE, float(button_scale or 1.0)))
    bw = max(1, int(round(img.width * base_scale)))
    bh = max(1, int(round(img.height * base_scale)))
    bx = (bg_w - bw) // 2 + int(offset_x)
    by = (bg_h - bh) // 2 + int(offset_y)
    return _clamp_button_rect(bx, by, bw, bh, bg_w, bg_h)


def merge_gif_layers_by_image(
    layers: list[tuple[Path, list[str], dict | None]],
) -> list[tuple[Path, list[str], dict | None]]:
    """相同图片且相同摆位的多个动效合并为单层（变换叠加）。"""
    grouped: dict[str, dict] = {}
    for path, effects, layout in layers:
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        if layout:
            layout_key = f"{layout.get('x')}:{layout.get('y')}:{layout.get('w')}:{layout.get('h')}"
        else:
            layout_key = ""
        group_key = f"{digest}|{layout_key}"
        if group_key not in grouped:
            grouped[group_key] = {"path": path, "effects": [], "layout": layout}
        for effect in effects:
            if effect not in grouped[group_key]["effects"]:
                grouped[group_key]["effects"].append(effect)
    return [(item["path"], item["effects"], item["layout"]) for item in grouped.values()]


def make_animated_gif(
    background_path: Path,
    layers: list[tuple[Path, list[str], dict | None]],
    output_path: Path,
    *,
    intensity: str = "medium",
    duration_sec: float = DEFAULT_DURATION_SEC,
    fps: int = DEFAULT_FPS,
    offset_x: int = 0,
    offset_y: int = 0,
    button_scale: float = 1.0,
    button_x: int | None = None,
    button_y: int | None = None,
    button_width: int | None = None,
    button_height: int | None = None,
    foreground_path: Path | None = None,
    foreground_layout: dict | None = None,
) -> dict:
    if not layers:
        raise ValueError("请至少上传一个动效图层")

    merged_layers = merge_gif_layers_by_image(layers)
    bg_frames, bg_durations, bg_animated = _load_background_timeline(background_path)
    background = bg_frames[0]
    bg_w, bg_h = background.size

    # 上层动效周期与底图帧率解耦：「循环时长」只控制呼吸/浮动等快慢
    effect_duration_sec = max(0.4, min(8.0, float(duration_sec or DEFAULT_DURATION_SEC)))
    effect_cycle_ms = max(1.0, effect_duration_sec * 1000.0)

    if bg_animated:
        frame_count = len(bg_frames)
        durations_ms = bg_durations
        total_ms = sum(durations_ms) or (frame_count * 100)
        bg_duration_sec = total_ms / 1000.0
        fps = max(1, int(round(1000.0 * frame_count / total_ms))) if total_ms else DEFAULT_FPS
        frame_ms: int | list[int] = durations_ms
        out_duration_sec = bg_duration_sec
    else:
        fps = max(6, min(24, int(fps)))
        frame_count = max(8, int(round(effect_duration_sec * fps)))
        frame_ms = int(1000 / fps)
        bg_frames = [background] * frame_count
        durations_ms = [frame_ms] * frame_count
        bg_duration_sec = effect_duration_sec
        out_duration_sec = effect_duration_sec

    global_rect: tuple[int, int, int, int] | None = None
    if button_width is not None and button_height is not None:
        global_rect = (
            int(button_x or 0),
            int(button_y or 0),
            int(button_width),
            int(button_height),
        )

    has_per_layer_layout = any(layout for _, _, layout in merged_layers)
    single_layer = len(merged_layers) == 1

    layer_images: list[tuple[Image.Image, list[str], tuple[int, int, int, int]]] = []
    for path, effects, layout in merged_layers:
        with Image.open(path) as raw:
            btn_img = raw.convert("RGBA")
        fallback_rect = None
        # 多图层各自有摆位时，勿把「当前选中层」的 offset 套到其他层上
        use_offset_x = 0 if has_per_layer_layout else offset_x
        use_offset_y = 0 if has_per_layer_layout else offset_y
        if layout is None and not has_per_layer_layout and single_layer:
            fallback_rect = global_rect
        rect = _resolve_layer_rect(
            layout,
            btn_img,
            bg_w,
            bg_h,
            offset_x=use_offset_x,
            offset_y=use_offset_y,
            button_scale=button_scale,
            fallback_rect=fallback_rect,
        )
        layer_images.append((btn_img, list(effects), rect))

    foreground_img: Image.Image | None = None
    foreground_rect: tuple[int, int, int, int] | None = None
    if foreground_path and foreground_path.is_file():
        with Image.open(foreground_path) as fg_raw:
            foreground_img = fg_raw.convert("RGBA")
        foreground_rect = _resolve_layer_rect(
            foreground_layout,
            foreground_img,
            bg_w,
            bg_h,
            offset_x=0 if (foreground_layout or has_per_layer_layout) else offset_x,
            offset_y=0 if (foreground_layout or has_per_layer_layout) else offset_y,
            button_scale=button_scale,
            fallback_rect=None,
        )

    frames: list[Image.Image] = []
    out_durations: list[int] = []
    elapsed_ms = 0.0
    # 动效至少按约 12fps 采样，避免慢速底图导致上层动作发飘/发慢
    effect_sample_ms = max(40.0, effect_cycle_ms / max(8.0, effect_duration_sec * 12.0))

    def _compose_at(bg_frame: Image.Image, phase_t: float) -> Image.Image:
        composed = bg_frame.copy()
        for btn_img, effects, (bx, by, bw, bh) in layer_images:
            tr = compute_combined_transform(phase_t, effects, intensity)
            composed = _paste_button_transformed(
                composed,
                btn_img,
                bx,
                by,
                bw,
                bh,
                scale=tr["scale"],
                dx=tr["dx"],
                dy=tr["dy"],
                angle_deg=tr["angle"],
            )
        if foreground_img is not None and foreground_rect is not None:
            fx, fy, fw, fh = foreground_rect
            composed = _paste_button_transformed(
                composed,
                foreground_img,
                fx,
                fy,
                fw,
                fh,
            )
        return composed

    if bg_animated:
        for i, bg_frame in enumerate(bg_frames):
            remaining = float(durations_ms[i] if i < len(durations_ms) else 100)
            while remaining > 0.5:
                chunk = min(effect_sample_ms, remaining)
                phase_t = ((elapsed_ms + chunk * 0.5) % effect_cycle_ms) / effect_cycle_ms
                frames.append(_compose_at(bg_frame, phase_t))
                out_durations.append(max(20, int(round(chunk))))
                elapsed_ms += chunk
                remaining -= chunk
        frame_count = len(frames)
        frame_ms = out_durations
        total_out_ms = sum(out_durations) or 1
        fps = max(1, int(round(1000.0 * frame_count / total_out_ms)))
        out_duration_sec = total_out_ms / 1000.0
    else:
        for i in range(frame_count):
            phase_t = (elapsed_ms % effect_cycle_ms) / effect_cycle_ms
            frames.append(_compose_at(bg_frames[i % len(bg_frames)], phase_t))
            step_ms = durations_ms[i] if i < len(durations_ms) else 100
            elapsed_ms += float(step_ms)

    _save_transparent_gif(frames, output_path, frame_ms)

    enabled_effects: list[str] = []
    for _, effects, _ in merged_layers:
        for effect in effects:
            if effect not in enabled_effects:
                enabled_effects.append(effect)

    first_rect = layer_images[0][2] if layer_images else (0, 0, 0, 0)
    bx, by, bw, bh = first_rect

    return {
        "width": bg_w,
        "height": bg_h,
        "frameCount": frame_count,
        "fps": fps,
        "durationSec": round(out_duration_sec, 3),
        "effectDurationSec": round(effect_duration_sec, 3),
        "backgroundDurationSec": round(bg_duration_sec, 3),
        "intensity": intensity,
        "effects": enabled_effects,
        "buttonX": bx,
        "buttonY": by,
        "buttonWidth": bw,
        "buttonHeight": bh,
        "hasForeground": foreground_img is not None,
        "backgroundAnimated": bg_animated,
        "fileSize": output_path.stat().st_size,
    }


def make_breathing_gif(
    background_path: Path,
    button_path: Path,
    output_path: Path,
    **kwargs,
) -> dict:
    """兼容旧接口：单图层呼吸动效。"""
    return make_animated_gif(
        background_path,
        [(button_path, ["breathing"], None)],
        output_path,
        **kwargs,
    )
