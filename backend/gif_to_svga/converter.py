"""GIF → SVGA 2.0 转换（Protobuf + zlib，兼容 svga.dev）。

- 画布宽高始终与原 GIF 一致（不缩放像素）
- 默认将文件体积控制在 1MB 内（可通过 SVGA_MAX_BYTES 调整，0=不限制）
"""
import hashlib
import os
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image

from gif_to_svga.proto import svga_pb2

VALID_FPS = [1, 2, 3, 5, 6, 10, 12, 15, 20, 30, 60]
SVGA_VERSION = "2.0.0"
DEFAULT_MAX_BYTES = int(os.environ.get("SVGA_MAX_BYTES", str(1024 * 1024)))

FULL_SIZE_COLOR_LEVELS = [256, 200, 160, 128, 96, 72, 64, 48, 36, 32, 24, 16, 12, 8]
THIN_FRAME_STEPS = (2, 3, 4, 5, 6, 8)
THIN_FRAME_COLOR_LEVELS = [128, 96, 64, 48, 32, 24, 16, 12, 8]


def nearest_valid_fps(raw_fps: float) -> int:
    best = VALID_FPS[0]
    best_dist = abs(best - raw_fps)
    for v in VALID_FPS:
        d = abs(v - raw_fps)
        if d < best_dist:
            best_dist = d
            best = v
    return best


def estimate_fps_from_durations_ms(durations_ms: list[int]) -> int | None:
    if not durations_ms:
        return None
    valid = [d if d > 0 else 100 for d in durations_ms]
    avg_ms = sum(valid) / len(valid)
    return nearest_valid_fps(1000.0 / avg_ms)


def _total_duration_ms(durations_ms: list[int]) -> float:
    if not durations_ms:
        return 0.0
    return float(sum(d if d > 0 else 100 for d in durations_ms))


def fps_preserving_duration(frame_count: int, total_ms: float) -> int:
    """按「帧数 / 原 GIF 总时长」选合法 FPS，避免抽帧后仍用原帧率导致播放变快。"""
    if frame_count <= 0 or total_ms <= 0:
        return 20
    target_sec = total_ms / 1000.0
    best = VALID_FPS[0]
    best_err = abs(frame_count / float(best) - target_sec)
    for v in VALID_FPS:
        err = abs(frame_count / float(v) - target_sec)
        if err < best_err:
            best_err = err
            best = v
    return best


def _read_gif_frames(input_path: Path) -> tuple[list[Image.Image], int, int, list[int]]:
    with Image.open(input_path) as img:
        width, height = img.size
        frames: list[Image.Image] = []
        durations_ms: list[int] = []
        try:
            frame_index = 0
            while True:
                frames.append(img.copy().convert("RGBA"))
                durations_ms.append(int(img.info.get("duration") or 100))
                frame_index += 1
                img.seek(frame_index)
        except EOFError:
            pass
    if not frames:
        raise ValueError("GIF 没有可用帧")
    return frames, width, height, durations_ms


def _thin_frames(frames: list[Image.Image], step: int) -> list[Image.Image]:
    if step <= 1 or len(frames) <= 2:
        return frames
    thinned = frames[::step]
    return thinned if len(thinned) >= 2 else frames


def _encode_png(frame: Image.Image, colors: int) -> bytes:
    img = frame.convert("RGBA")
    if colors < 256:
        n_colors = max(2, min(colors, 255))
        try:
            img = img.quantize(colors=n_colors, method=Image.Quantize.LIBIMAGEQUANT)
        except (ValueError, OSError, AttributeError):
            img = img.quantize(colors=n_colors, method=Image.Quantize.FASTOCTREE)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def _prepare_frame_pngs(frames: list[Image.Image], colors: int) -> tuple[list[bytes], int, int]:
    if not frames:
        raise ValueError("没有可用帧")
    out_w, out_h = frames[0].size
    png_list: list[bytes] = []
    for frame in frames:
        if frame.size != (out_w, out_h):
            raise ValueError("GIF 各帧尺寸不一致，无法保持统一画布输出")
        png_list.append(_encode_png(frame, colors))
    return png_list, out_w, out_h


def _build_movie_entity(
    png_frames: list[bytes],
    width: int,
    height: int,
    fps: int,
) -> svga_pb2.MovieEntity:
    total_frames = len(png_frames)
    movie = svga_pb2.MovieEntity()
    movie.version = SVGA_VERSION
    movie.params.viewBoxWidth = float(width)
    movie.params.viewBoxHeight = float(height)
    movie.params.fps = int(fps)
    movie.params.frames = int(total_frames)

    # 相同 PNG 内容复用 imageKey，减小体积
    key_by_digest: dict[str, str] = {}

    for i, png_data in enumerate(png_frames):
        digest = hashlib.md5(png_data).hexdigest()
        if digest not in key_by_digest:
            key = f"img_{len(key_by_digest)}.png"
            key_by_digest[digest] = key
            movie.images[key] = png_data
        image_key = key_by_digest[digest]

        sprite = svga_pb2.SpriteEntity()
        sprite.imageKey = image_key
        for f in range(total_frames):
            fe = sprite.frames.add()
            if f == i:
                fe.alpha = 1.0
                fe.layout.x = 0.0
                fe.layout.y = 0.0
                fe.layout.width = float(width)
                fe.layout.height = float(height)
                fe.transform.a = 1.0
                fe.transform.d = 1.0
            else:
                fe.alpha = 0.0
        movie.sprites.append(sprite)

    return movie


def _movie_to_svga_bytes(movie: svga_pb2.MovieEntity) -> bytes:
    return zlib.compress(movie.SerializeToString(), level=9)


def _iter_compress_attempts(frames: list[Image.Image]):
    """原尺寸：先全帧减色，再在不改宽高前提下隔帧抽取。"""
    for colors in FULL_SIZE_COLOR_LEVELS:
        yield frames, colors, 1
    for thin_step in THIN_FRAME_STEPS:
        if len(frames) < thin_step * 2:
            continue
        thinned = _thin_frames(frames, thin_step)
        if len(thinned) >= len(frames):
            continue
        for colors in THIN_FRAME_COLOR_LEVELS:
            yield thinned, colors, thin_step


def validate_svga_file(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        data = zlib.decompress(raw)
    except zlib.error as e:
        raise ValueError(f"不是有效的 SVGA 2.0 压缩包（zlib 解压失败）: {e}") from e
    movie = svga_pb2.MovieEntity()
    movie.ParseFromString(data)
    if not movie.version.startswith("2."):
        raise ValueError(f"版本为 {movie.version}，期望 2.x")
    return {
        "version": movie.version,
        "width": int(movie.params.viewBoxWidth),
        "height": int(movie.params.viewBoxHeight),
        "fps": movie.params.fps,
        "totalFrames": movie.params.frames,
        "imageCount": len(movie.images),
        "spriteCount": len(movie.sprites),
        "fileSize": len(raw),
    }


def _assert_dimensions(orig_w: int, orig_h: int, out_w: int, out_h: int) -> None:
    if out_w != orig_w or out_h != orig_h:
        raise ValueError(
            f"输出尺寸 {out_w}×{out_h} 与原图 {orig_w}×{orig_h} 不一致（已禁止缩放）"
        )


def gif_to_svga(
    input_path: str | Path,
    output_path: str | Path,
    fps: int | None = None,
    max_bytes: int | None = None,
) -> dict:
    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    max_bytes = max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES
    enforce_limit = max_bytes > 0
    input_size = input_path.stat().st_size

    frames, orig_w, orig_h, durations_ms = _read_gif_frames(input_path)
    orig_frame_count = len(frames)
    total_ms = _total_duration_ms(durations_ms)
    # 用户指定的 fps 仅在未抽帧时采用；抽帧后必须按总时长重算，否则会明显变快
    fps_override = fps
    if fps_override is not None and fps_override not in VALID_FPS:
        fps_override = nearest_valid_fps(float(fps_override))

    compressed: bytes | None = None
    profile_used: dict | None = None
    output_frame_count = orig_frame_count
    thin_step_used = 1
    fps_used = fps_override or estimate_fps_from_durations_ms(durations_ms) or 20

    for work_frames, colors, thin_step in _iter_compress_attempts(frames):
        png_list, final_w, final_h = _prepare_frame_pngs(work_frames, colors)
        _assert_dimensions(orig_w, orig_h, final_w, final_h)
        n = len(work_frames)
        if thin_step > 1 or fps_override is None:
            attempt_fps = fps_preserving_duration(n, total_ms)
        else:
            attempt_fps = fps_override
        movie = _build_movie_entity(png_list, final_w, final_h, attempt_fps)
        candidate = _movie_to_svga_bytes(movie)
        profile_used = {
            "colors": colors,
            "thin_step": thin_step,
            "phase": "full_size" if thin_step <= 1 else "thin_frames",
            "fps": attempt_fps,
        }
        output_frame_count = n
        thin_step_used = thin_step
        fps_used = attempt_fps
        if not enforce_limit or len(candidate) <= max_bytes:
            compressed = candidate
            break

    if compressed is None and enforce_limit:
        limit_mb = max_bytes / (1024 * 1024)
        raise ValueError(
            f"在保持原尺寸 {orig_w}×{orig_h} 的前提下，无法将 SVGA 压缩到 {limit_mb:.0f}MB 以内。"
            f"原 GIF 约 {orig_frame_count} 帧、{input_size / 1024:.0f}KB。"
            "可尝试：减少 GIF 帧数、简化画面颜色/细节，或联系管理员调高 SVGA_MAX_BYTES。"
        )

    if compressed is None:
        work_frames, colors, thin_step = frames, FULL_SIZE_COLOR_LEVELS[0], 1
        png_list, final_w, final_h = _prepare_frame_pngs(work_frames, colors)
        fps_used = (
            fps_override
            if fps_override is not None
            else fps_preserving_duration(len(work_frames), total_ms)
        )
        movie = _build_movie_entity(png_list, final_w, final_h, fps_used)
        compressed = _movie_to_svga_bytes(movie)
        profile_used = {"colors": colors, "thin_step": 1, "phase": "unlimited", "fps": fps_used}
        output_frame_count = len(work_frames)
        thin_step_used = 1

    if output_path.suffix.lower() != ".svga":
        output_path = output_path.with_suffix(".svga")
    output_path.write_bytes(compressed)

    meta = validate_svga_file(output_path)
    final_w = meta["width"]
    final_h = meta["height"]
    _assert_dimensions(orig_w, orig_h, final_w, final_h)

    out_size = output_path.stat().st_size
    frames_reduced = output_frame_count < orig_frame_count
    duration_sec = round(total_ms / 1000.0, 3) if total_ms else None
    svga_duration_sec = (
        round(output_frame_count / float(fps_used), 3) if fps_used else None
    )
    return {
        "inputPath": str(input_path),
        "outputPath": str(output_path),
        "width": final_w,
        "height": final_h,
        "originalWidth": orig_w,
        "originalHeight": orig_h,
        "sizePreserved": True,
        "totalFrames": meta["totalFrames"],
        "originalFrameCount": orig_frame_count,
        "framesReduced": frames_reduced,
        "frameThinStep": thin_step_used if frames_reduced else 1,
        "fps": fps_used,
        "durationSec": duration_sec,
        "svgaDurationSec": svga_duration_sec,
        "version": meta["version"],
        "output_filename": output_path.name,
        "fileSize": out_size,
        "inputFileSize": input_size,
        "underLimit": (not enforce_limit) or out_size <= max_bytes,
        "maxBytes": max_bytes if enforce_limit else None,
        "compressProfile": profile_used,
    }
