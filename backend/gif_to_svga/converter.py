"""GIF → SVGA 2.0 转换（Protobuf + zlib，兼容 svga.dev；支持压缩至目标体积）。"""
import os
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image

from gif_to_svga.proto import svga_pb2

VALID_FPS = [1, 2, 3, 5, 6, 10, 12, 15, 20, 30, 60]
SVGA_VERSION = "2.0.0"
DEFAULT_MAX_BYTES = int(os.environ.get("SVGA_MAX_BYTES", "1048576"))  # 1MB

# 逐步加强压缩，直到体积低于 max_bytes
COMPRESS_PROFILES = [
    {"colors": 256, "scale": 1.0, "max_side": None},
    {"colors": 160, "scale": 1.0, "max_side": 960},
    {"colors": 128, "scale": 1.0, "max_side": 720},
    {"colors": 96, "scale": 0.92, "max_side": 640},
    {"colors": 64, "scale": 0.85, "max_side": 560},
    {"colors": 48, "scale": 0.75, "max_side": 480},
    {"colors": 32, "scale": 0.65, "max_side": 400},
    {"colors": 24, "scale": 0.55, "max_side": 360},
    {"colors": 16, "scale": 0.5, "max_side": 320},
]


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


def _read_gif_frames(input_path: Path) -> tuple[list[Image.Image], int, int, list[int]]:
    img = Image.open(input_path)
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


def _resize_frame(img: Image.Image, scale: float, max_side: int | None) -> Image.Image:
    if max_side:
        w, h = img.size
        longest = max(w, h)
        if longest > max_side:
            ratio = max_side / longest
            scale *= ratio
    if scale != 1.0:
        nw = max(1, int(img.width * scale))
        nh = max(1, int(img.height * scale))
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    return img


def _encode_png(frame: Image.Image, colors: int) -> bytes:
    """PNG-8 调色板压缩，显著减小体积。"""
    img = frame.convert("RGBA")
    if colors < 256:
        n_colors = max(2, min(colors, 255))
        # Pillow 12+：RGBA 仅支持 FASTOCTREE / LIBIMAGEQUANT，不能用 MEDIANCUT
        try:
            img = img.quantize(colors=n_colors, method=Image.Quantize.LIBIMAGEQUANT)
        except (ValueError, OSError, AttributeError):
            img = img.quantize(colors=n_colors, method=Image.Quantize.FASTOCTREE)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def _prepare_frame_images(
    frames: list[Image.Image],
    *,
    colors: int,
    scale: float,
    max_side: int | None,
) -> tuple[list[bytes], int, int]:
    png_list: list[bytes] = []
    out_w, out_h = 0, 0
    for frame in frames:
        img = _resize_frame(frame, scale, max_side)
        out_w, out_h = img.size
        png_list.append(_encode_png(img, colors))
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

    for i, png_data in enumerate(png_frames):
        image_key = f"frame_{i}.png"
        movie.images[image_key] = png_data

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


def _thin_frames(frames: list[Image.Image], step: int) -> list[Image.Image]:
    if step <= 1 or len(frames) <= 2:
        return frames
    thinned = frames[::step]
    return thinned if len(thinned) >= 2 else frames


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
    input_size = input_path.stat().st_size

    frames, orig_w, orig_h, durations_ms = _read_gif_frames(input_path)
    if fps is None:
        fps = estimate_fps_from_durations_ms(durations_ms)
    fps = fps or 20
    if fps not in VALID_FPS:
        fps = nearest_valid_fps(float(fps))

    work_frames = frames
    thin_step = 1
    compressed: bytes | None = None
    final_w = orig_w
    final_h = orig_h
    profile_used: dict | None = None

    for thin_step in (1, 2, 3):
        if thin_step > 1:
            work_frames = _thin_frames(frames, thin_step)
        for profile in COMPRESS_PROFILES:
            png_list, final_w, final_h = _prepare_frame_images(
                work_frames,
                colors=profile["colors"],
                scale=profile["scale"],
                max_side=profile["max_side"],
            )
            movie = _build_movie_entity(png_list, final_w, final_h, fps)
            candidate = _movie_to_svga_bytes(movie)
            if len(candidate) <= max_bytes:
                compressed = candidate
                profile_used = {**profile, "thin_step": thin_step}
                break
        if compressed is not None:
            break

    if compressed is None:
        # 最后一搏：极限参数
        png_list, final_w, final_h = _prepare_frame_images(
            _thin_frames(frames, 4), colors=12, scale=0.45, max_side=280
        )
        movie = _build_movie_entity(png_list, final_w, final_h, fps)
        compressed = _movie_to_svga_bytes(movie)
        profile_used = {"colors": 12, "scale": 0.45, "max_side": 280, "thin_step": 4}

    if output_path.suffix.lower() != ".svga":
        output_path = output_path.with_suffix(".svga")
    output_path.write_bytes(compressed)

    meta = validate_svga_file(output_path)
    out_size = output_path.stat().st_size
    under_limit = out_size <= max_bytes
    return {
        "inputPath": str(input_path),
        "outputPath": str(output_path),
        "width": final_w,
        "height": final_h,
        "originalWidth": orig_w,
        "originalHeight": orig_h,
        "totalFrames": meta["totalFrames"],
        "fps": fps,
        "version": meta["version"],
        "output_filename": output_path.name,
        "fileSize": out_size,
        "inputFileSize": input_size,
        "underLimit": under_limit,
        "maxBytes": max_bytes,
        "compressProfile": profile_used,
    }
