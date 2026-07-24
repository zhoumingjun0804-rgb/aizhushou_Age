"""合成按钮动效 GIF：底图（静图或动图）+ 一个或多个透明图层（呼吸 / 浮动 / 摇摆 / 旋转）。"""
import hashlib
import math
from io import BytesIO
from pathlib import Path

from PIL import Image

DEFAULT_FPS = 12
DEFAULT_DURATION_SEC = 1.6
# App 上传常用上限；0 / None 表示不限制
DEFAULT_MAX_BYTES = 1024 * 1024
# 合成时上限：慢速底图插帧再多也不超过，避免动辄上百帧撑爆体积
MAX_COMPOSE_FRAMES = 48
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


def _rgba_to_palette(frame: Image.Image, colors: int = 255) -> Image.Image:
    """RGBA → 调色板，保留透明像素（避免 convert('RGB') 把透明变黑）。"""
    n = max(2, min(255, int(colors)))
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    p = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=n)
    return _apply_alpha_mask(p, alpha)


def _quantize_to_shared_palette(frame: Image.Image, palette_ref: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    p = rgb.quantize(palette=palette_ref, dither=Image.Dither.NONE)
    return _apply_alpha_mask(p, alpha)


def _normalize_durations(frame_ms: int | list[int], frame_count: int) -> list[int]:
    if isinstance(frame_ms, list):
        durations = [max(20, int(d or 100)) for d in frame_ms]
        if len(durations) < frame_count:
            durations.extend([durations[-1] if durations else 100] * (frame_count - len(durations)))
        return durations[:frame_count]
    return [max(20, int(frame_ms or 100))] * frame_count


def _thin_timeline(
    frames: list[Image.Image],
    durations: list[int],
    step: int,
) -> tuple[list[Image.Image], list[int]]:
    step = max(1, int(step))
    if step <= 1 or len(frames) <= 2:
        return frames, durations
    out_frames: list[Image.Image] = []
    out_durations: list[int] = []
    i = 0
    while i < len(frames):
        chunk_d = durations[i : i + step]
        out_frames.append(frames[i])
        out_durations.append(max(20, sum(chunk_d) if chunk_d else 100))
        i += step
    if len(out_frames) < 2 and len(frames) >= 2:
        return [frames[0], frames[-1]], [
            max(20, sum(durations[:-1]) or 100),
            max(20, durations[-1]),
        ]
    return out_frames, out_durations


def _scale_frames(frames: list[Image.Image], scale: float) -> list[Image.Image]:
    scale = float(scale)
    if scale >= 0.999 or not frames:
        return frames
    scale = max(0.35, min(1.0, scale))
    w, h = frames[0].size
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if (nw, nh) == (w, h):
        return frames
    return [f.resize((nw, nh), Image.Resampling.LANCZOS) for f in frames]


def _save_transparent_gif(
    frames: list[Image.Image],
    output_path: Path,
    frame_ms: int | list[int],
    *,
    colors: int = 255,
) -> None:
    if not frames:
        raise ValueError("no frames")
    durations = _normalize_durations(frame_ms, len(frames))
    first_p = _rgba_to_palette(frames[0], colors=colors)
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


def _save_gif_under_max_bytes(
    frames: list[Image.Image],
    frame_ms: int | list[int],
    output_path: Path,
    max_bytes: int | None,
) -> dict:
    """写入 GIF；若超过 max_bytes，按抽帧 → 减色 → 等比缩小依次压缩。"""
    durations = _normalize_durations(frame_ms, len(frames))
    enforce = bool(max_bytes and max_bytes > 0)
    limit = int(max_bytes) if enforce else 0

    # 先保尺寸，再缩小；同档内先抽帧再减色
    scales = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5)
    thin_steps = (1, 2, 3, 4, 5, 6, 8)
    color_levels = (255, 128, 96, 64)

    best: dict | None = None
    uncompressed_size: int | None = None

    for scale in scales:
        for thin in thin_steps:
            work_f, work_d = _thin_timeline(frames, durations, thin)
            work_f = _scale_frames(work_f, scale)
            for colors in color_levels:
                _save_transparent_gif(work_f, output_path, work_d, colors=colors)
                size = output_path.stat().st_size
                if uncompressed_size is None and scale >= 0.999 and thin == 1 and colors >= 255:
                    uncompressed_size = size
                info = {
                    "fileSize": size,
                    "underLimit": (not enforce) or size <= limit,
                    "framesReduced": len(work_f) < len(frames),
                    "frameThinStep": thin if len(work_f) < len(frames) else 1,
                    "scaled": scale < 0.999,
                    "scale": round(scale, 3),
                    "colors": colors,
                    "frameCount": len(work_f),
                    "width": work_f[0].size[0],
                    "height": work_f[0].size[1],
                    "durations": work_d,
                    "uncompressedSize": uncompressed_size,
                    "maxBytes": limit if enforce else None,
                }
                if best is None or size < best["fileSize"]:
                    best = info
                    if enforce and size <= limit:
                        # 已达标：把当前最优结果留在 output_path（刚写入）
                        return info
                if not enforce:
                    return info
            if scale < 0.999 and thin >= 4:
                break

    # 仍超限：保留体积最小的一档
    assert best is not None
    work_f, work_d = _thin_timeline(frames, durations, int(best["frameThinStep"]))
    work_f = _scale_frames(work_f, float(best["scale"]))
    _save_transparent_gif(work_f, output_path, work_d, colors=int(best["colors"]))
    best["fileSize"] = output_path.stat().st_size
    best["underLimit"] = (not enforce) or best["fileSize"] <= limit
    best["width"], best["height"] = work_f[0].size
    best["frameCount"] = len(work_f)
    best["durations"] = work_d
    return best


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


def _synthetic_transparent_bg(
    layers: list[tuple[Path, list[str], dict | None]],
) -> Image.Image:
    """无底图时：按动效图层尺寸生成透明画布（预留动效边距）。"""
    max_w = 1
    max_h = 1
    for path, _, layout in layers:
        if layout and layout.get("w") and layout.get("h"):
            max_w = max(max_w, int(layout["w"]))
            max_h = max(max_h, int(layout["h"]))
            continue
        with Image.open(path) as raw:
            iw, ih = raw.size
        max_w = max(max_w, iw)
        max_h = max(max_h, ih)
    pad = max(24, int(max(max_w, max_h) * 0.15))
    return Image.new("RGBA", (max_w + 2 * pad, max_h + 2 * pad), (0, 0, 0, 0))


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
    background_path: Path | None,
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
    max_bytes: int | None = DEFAULT_MAX_BYTES,
) -> dict:
    if not layers:
        raise ValueError("请至少上传一个动效图层")

    merged_layers = merge_gif_layers_by_image(layers)
    if background_path is not None and Path(background_path).is_file():
        bg_frames, bg_durations, bg_animated = _load_background_timeline(Path(background_path))
        background = bg_frames[0]
    else:
        background = _synthetic_transparent_bg(merged_layers)
        bg_frames, bg_durations, bg_animated = [background], [100], False
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
        frame_count = min(frame_count, MAX_COMPOSE_FRAMES)
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
    # 动效至少按约 12fps 采样，但总帧数封顶，避免慢速底图插出上百帧
    effect_sample_ms = max(40.0, effect_cycle_ms / max(8.0, effect_duration_sec * 12.0))
    if bg_animated:
        total_ms = sum(durations_ms) or 1.0
        est_frames = 0
        for d in durations_ms:
            est_frames += max(1, int(math.ceil(float(d) / effect_sample_ms)))
        if est_frames > MAX_COMPOSE_FRAMES:
            effect_sample_ms = max(effect_sample_ms, total_ms / float(MAX_COMPOSE_FRAMES))

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

    save_info = _save_gif_under_max_bytes(frames, frame_ms, output_path, max_bytes)
    frame_count = int(save_info["frameCount"])
    bg_w = int(save_info["width"])
    bg_h = int(save_info["height"])
    saved_durs = save_info.get("durations") or _normalize_durations(frame_ms, frame_count)
    total_out_ms = sum(saved_durs) or 1
    fps = max(1, int(round(1000.0 * frame_count / total_out_ms)))
    out_duration_sec = total_out_ms / 1000.0

    enabled_effects: list[str] = []
    for _, effects, _ in merged_layers:
        for effect in effects:
            if effect not in enabled_effects:
                enabled_effects.append(effect)

    first_rect = layer_images[0][2] if layer_images else (0, 0, 0, 0)
    bx, by, bw, bh = first_rect
    scale = float(save_info.get("scale") or 1.0)
    if scale < 0.999:
        bx = int(round(bx * scale))
        by = int(round(by * scale))
        bw = max(1, int(round(bw * scale)))
        bh = max(1, int(round(bh * scale)))

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
        "fileSize": save_info["fileSize"],
        "underLimit": save_info.get("underLimit", True),
        "maxBytes": save_info.get("maxBytes"),
        "framesReduced": save_info.get("framesReduced", False),
        "frameThinStep": save_info.get("frameThinStep", 1),
        "scaled": save_info.get("scaled", False),
        "scale": save_info.get("scale", 1.0),
        "colors": save_info.get("colors", 255),
        "uncompressedSize": save_info.get("uncompressedSize"),
    }


def make_breathing_gif(
    background_path: Path | None,
    button_path: Path,
    output_path: Path,
    **kwargs,
) -> dict:
    """兼容旧接口：单图层呼吸动效。background_path 可为 None（透明画布）。"""
    return make_animated_gif(
        background_path,
        [(button_path, ["breathing"], None)],
        output_path,
        **kwargs,
    )
