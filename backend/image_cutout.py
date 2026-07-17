"""抠图：魔棒蒙版删除；智能提取（框选主体透明化）。"""
from __future__ import annotations

import io
from collections import Counter, deque
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

try:
    from rembg import new_session, remove
except BaseException:  # pragma: no cover - rembg 缺失或 onnxruntime 不可用时走运行时提示
    new_session = None
    remove = None


_REMBG_SESSION = None


def apply_cutout_mask(input_path: Path, mask_path: Path, output_path: Path) -> dict:
    with Image.open(input_path) as img:
        img = img.convert("RGBA")
        with Image.open(mask_path) as mask:
            mask = mask.convert("L")
            if mask.size != img.size:
                mask = mask.resize(img.size, Image.Resampling.NEAREST)
            alpha = img.split()[3]
            keep = ImageChops.invert(mask)
            alpha = ImageChops.multiply(alpha, keep)
            img.putalpha(alpha)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, format="PNG", optimize=True)
        w, h = img.size
    return {"width": w, "height": h}


def _clamp_roi(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    x = max(0, min(int(x), img_w - 1))
    y = max(0, min(int(y), img_h - 1))
    w = max(1, min(int(w), img_w - x))
    h = max(1, min(int(h), img_h - y))
    return x, y, w, h


def compute_extract_crop_bbox(
    img_w: int,
    img_h: int,
    roi_x: int,
    roi_y: int,
    roi_w: int,
    roi_h: int,
    pad_ratio: float = 0.1,
) -> tuple[int, int, int, int]:
    x, y, w, h = _clamp_roi(roi_x, roi_y, roi_w, roi_h, img_w, img_h)
    pad = max(6, int(min(w, h) * pad_ratio))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img_w, x + w + pad)
    y1 = min(img_h, y + h + pad)
    return x0, y0, x1 - x0, y1 - y0


def save_extract_crop(input_path: Path, crop_path: Path, x: int, y: int, w: int, h: int) -> None:
    with Image.open(input_path) as im:
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        crop = im.crop((x, y, x + w, y + h))
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(crop_path, format="PNG", optimize=True)


def build_ai_extract_prompt(user_prompt: str) -> str:
    """直接使用用户填写的提取说明，不再注入透明背景等固定规则。"""
    return (user_prompt or "").strip() or "根据参考图生成素材"


def has_rembg() -> bool:
    return remove is not None and new_session is not None


def _get_rembg_session():
    global _REMBG_SESSION
    if not has_rembg():
        raise RuntimeError("未安装 rembg 依赖，请先安装 requirements.txt 后重启服务")
    if _REMBG_SESSION is None:
        _REMBG_SESSION = new_session("u2net")
    return _REMBG_SESSION


def preferred_cutout_backend() -> str:
    return "rembg" if has_rembg() else "local"


def _refine_alpha_edges(alpha: Image.Image) -> Image.Image:
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.45))
    return alpha.point(lambda v: 0 if v <= 4 else 255 if v >= 251 else int(v))


def _decontaminate_white_fringe(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= 0:
                px[x, y] = (0, 0, 0, 0)
                continue
            if a >= 250:
                continue
            af = a / 255.0
            nr = max(0, min(255, int(round((r - 255 * (1 - af)) / af))))
            ng = max(0, min(255, int(round((g - 255 * (1 - af)) / af))))
            nb = max(0, min(255, int(round((b - 255 * (1 - af)) / af))))
            px[x, y] = (nr, ng, nb, a)
    return rgba


def _trim_to_subject(im: Image.Image, padding: int = 0) -> Image.Image:
    bbox = im.getbbox()
    if not bbox:
        return im
    if padding <= 0:
        return im.crop(bbox)
    left, top, right, bottom = bbox
    return im.crop((
        max(0, left - padding),
        max(0, top - padding),
        min(im.width, right + padding),
        min(im.height, bottom + padding),
    ))


def _collect_border_samples(im: Image.Image, border: int) -> list[tuple[int, int, int]]:
    rgba = im.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    samples: list[tuple[int, int, int]] = []
    for y in range(h):
        for x in range(w):
            if x >= border and x < (w - border) and y >= border and y < (h - border):
                continue
            r, g, b, a = px[x, y]
            if a >= 12:
                samples.append((r, g, b))
    return samples


def _build_border_palette(samples: list[tuple[int, int, int]], max_colors: int = 6) -> list[tuple[int, int, int]]:
    if not samples:
        return [(255, 255, 255)]
    bins = Counter((r // 16, g // 16, b // 16) for r, g, b in samples)
    palette = []
    for (r, g, b), _ in bins.most_common(max_colors):
        palette.append((r * 16 + 8, g * 16 + 8, b * 16 + 8))
    return palette or [(255, 255, 255)]


def _nearest_palette_distance(r: int, g: int, b: int, palette: list[tuple[int, int, int]]) -> float:
    return min(
        ((r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2) ** 0.5
        for pr, pg, pb in palette
    )


def _mark_edge_connected_background(
    im: Image.Image,
    palette: list[tuple[int, int, int]],
    bg_threshold: float,
) -> tuple[bytearray, int, int]:
    """从画布四边泛洪，仅标记与边缘连通的背景像素（不穿透主体内部）。"""
    rgba = im.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    bg = bytearray(w * h)
    visited = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def is_bg_color(r: int, g: int, b: int, a: int) -> bool:
        if a <= 0:
            return True
        return _nearest_palette_distance(r, g, b, palette) <= bg_threshold

    def try_seed(x: int, y: int) -> None:
        idx = y * w + x
        if visited[idx]:
            return
        visited[idx] = 1
        r, g, b, a = px[x, y]
        if is_bg_color(r, g, b, a):
            bg[idx] = 1
            q.append((x, y))

    for x in range(w):
        try_seed(x, 0)
        try_seed(x, h - 1)
    for y in range(1, h - 1):
        try_seed(0, y)
        try_seed(w - 1, y)

    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nx < 0 or nx >= w or ny < 0 or ny >= h:
                continue
            idx = ny * w + nx
            if visited[idx]:
                continue
            visited[idx] = 1
            r, g, b, a = px[nx, ny]
            if is_bg_color(r, g, b, a):
                bg[idx] = 1
                q.append((nx, ny))
    return bg, w, h


def _neighbor_touches_background(bg: bytearray, w: int, h: int, x: int, y: int) -> bool:
    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if nx < 0 or nx >= w or ny < 0 or ny >= h:
            continue
        if bg[ny * w + nx]:
            return True
    return False


def _find_foreground_seed(
    edge_bg: bytearray,
    w: int,
    h: int,
    cx: int,
    cy: int,
    roi_x: int = 0,
    roi_y: int = 0,
    roi_w: int | None = None,
    roi_h: int | None = None,
) -> tuple[int, int] | None:
    cx = max(0, min(cx, w - 1))
    cy = max(0, min(cy, h - 1))
    if not edge_bg[cy * w + cx]:
        return cx, cy
    rx1 = max(0, roi_x)
    ry1 = max(0, roi_y)
    rx2 = min(w, roi_x + (roi_w if roi_w is not None else w))
    ry2 = min(h, roi_y + (roi_h if roi_h is not None else h))
    best: tuple[int, int] | None = None
    best_dist = 10**9
    for y in range(ry1, ry2):
        for x in range(rx1, rx2):
            if edge_bg[y * w + x]:
                continue
            dist = (x - cx) ** 2 + (y - cy) ** 2
            if dist < best_dist:
                best_dist = dist
                best = (x, y)
    return best


def _flood_foreground_from_seed(
    edge_bg: bytearray,
    w: int,
    h: int,
    seed_x: int,
    seed_y: int,
) -> bytearray:
    fg = bytearray(w * h)
    idx = seed_y * w + seed_x
    if edge_bg[idx]:
        return fg
    fg[idx] = 1
    q: deque[tuple[int, int]] = deque([(seed_x, seed_y)])
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nx < 0 or nx >= w or ny < 0 or ny >= h:
                continue
            nidx = ny * w + nx
            if fg[nidx] or edge_bg[nidx]:
                continue
            fg[nidx] = 1
            q.append((nx, ny))
    return fg


def _fill_foreground_holes(fg: bytearray, w: int, h: int) -> None:
    """将前景轮廓内的孔洞（如眼睛、镜片）补回前景。"""
    outside = bytearray(w * h)
    visited = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def try_seed(x: int, y: int) -> None:
        idx = y * w + x
        if visited[idx] or fg[idx]:
            return
        visited[idx] = 1
        outside[idx] = 1
        q.append((x, y))

    for x in range(w):
        try_seed(x, 0)
        try_seed(x, h - 1)
    for y in range(1, h - 1):
        try_seed(0, y)
        try_seed(w - 1, y)

    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nx < 0 or nx >= w or ny < 0 or ny >= h:
                continue
            idx = ny * w + nx
            if visited[idx] or fg[idx]:
                continue
            visited[idx] = 1
            outside[idx] = 1
            q.append((nx, ny))

    for idx in range(w * h):
        if not fg[idx] and not outside[idx]:
            fg[idx] = 1


def _build_local_subject_alpha(
    im: Image.Image,
    palette: list[tuple[int, int, int]],
    roi_cx: int,
    roi_cy: int,
    roi_x: int = 0,
    roi_y: int = 0,
    roi_w: int | None = None,
    roi_h: int | None = None,
    bg_threshold: float = 22,
    fg_threshold: float = 78,
) -> Image.Image:
    """从框选中心泛洪主体，仅去除与边缘连通的背景，并填补内部孔洞。"""
    rgba = im.convert("RGBA")
    px = rgba.load()
    edge_bg, w, h = _mark_edge_connected_background(rgba, palette, bg_threshold)
    seed = _find_foreground_seed(edge_bg, w, h, roi_cx, roi_cy, roi_x, roi_y, roi_w, roi_h)
    if seed is None:
        raise ValueError("框选区域内未找到主体，请重新框选后重试")
    fg = _flood_foreground_from_seed(edge_bg, w, h, seed[0], seed[1])
    _fill_foreground_holes(fg, w, h)

    alpha = Image.new("L", (w, h), 0)
    apx = alpha.load()
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            r, g, b, a = px[x, y]
            if edge_bg[idx]:
                apx[x, y] = 0
                continue
            if fg[idx]:
                apx[x, y] = a
                continue
            if not _neighbor_touches_background(edge_bg, w, h, x, y):
                apx[x, y] = 0
                continue
            dist = _nearest_palette_distance(r, g, b, palette)
            if dist >= fg_threshold:
                apx[x, y] = a
            elif dist <= bg_threshold:
                apx[x, y] = 0
            else:
                apx[x, y] = int(round(a * (dist - bg_threshold) / (fg_threshold - bg_threshold)))
    return _refine_alpha_edges(alpha)


def _mask_image_to_foreground(mask: Image.Image, threshold: int = 128) -> tuple[bytearray, int, int]:
    gray = mask.convert("L")
    w, h = gray.size
    data = gray.load()
    fg = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            if data[x, y] >= threshold:
                fg[y * w + x] = 1
    return fg, w, h


def _foreground_mask_to_image(fg: bytearray, w: int, h: int) -> Image.Image:
    alpha = Image.new("L", (w, h), 0)
    apx = alpha.load()
    for y in range(h):
        for x in range(w):
            if fg[y * w + x]:
                apx[x, y] = 255
    return alpha


def _apply_alpha_mask_to_image(src_path: Path, mask: Image.Image) -> Image.Image:
    """将分割蒙版应用到原图，保留主体内部原始颜色。"""
    with Image.open(src_path) as im:
        im = im.convert("RGBA")
    alpha = mask.convert("L")
    if alpha.size != im.size:
        alpha = alpha.resize(im.size, Image.Resampling.LANCZOS)
    fg, w, h = _mask_image_to_foreground(alpha)
    _fill_foreground_holes(fg, w, h)
    alpha = _foreground_mask_to_image(fg, w, h)
    alpha = _refine_alpha_edges(alpha)
    im.putalpha(alpha)
    return _decontaminate_white_fringe(im)


def _extract_subject_cutout_local(
    src_path: Path,
    dst_path: Path,
    trim: bool = True,
    roi_cx: int | None = None,
    roi_cy: int | None = None,
    roi_x: int = 0,
    roi_y: int = 0,
    roi_w: int | None = None,
    roi_h: int | None = None,
) -> tuple[int, int]:
    im = Image.open(src_path).convert("RGBA")
    w, h = im.size
    if roi_cx is None:
        roi_cx = w // 2
    if roi_cy is None:
        roi_cy = h // 2
    border = max(2, min(im.size) // 36)
    palette = _build_border_palette(_collect_border_samples(im, border))
    alpha = _build_local_subject_alpha(
        im,
        palette,
        roi_cx,
        roi_cy,
        roi_x=roi_x,
        roi_y=roi_y,
        roi_w=roi_w,
        roi_h=roi_h,
    )
    im.putalpha(alpha)
    if trim:
        im = _trim_to_subject(im, padding=0)
    bbox = im.getbbox()
    if not bbox:
        raise ValueError("未识别到明显主体，请适当扩大框选范围后重试")
    nonzero = sum(1 for v in im.getchannel("A").getdata() if v > 8)
    total = max(1, im.width * im.height)
    if nonzero / total > 0.985:
        raise ValueError("背景分离不明显，请适当扩大框选范围后重试")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst_path, format="PNG", optimize=True)
    return im.size


def extract_subject_cutout(
    src_path: Path,
    dst_path: Path,
    trim: bool = True,
    roi_x: int | None = None,
    roi_y: int | None = None,
    roi_w: int | None = None,
    roi_h: int | None = None,
) -> tuple[int, int, str]:
    with Image.open(src_path) as probe:
        crop_w, crop_h = probe.size
    if roi_x is None or roi_y is None or roi_w is None or roi_h is None:
        roi_x, roi_y = 0, 0
        roi_w, roi_h = crop_w, crop_h
    roi_cx = max(0, min(int(roi_x + roi_w // 2), crop_w - 1))
    roi_cy = max(0, min(int(roi_y + roi_h // 2), crop_h - 1))
    if not has_rembg():
        out_w, out_h = _extract_subject_cutout_local(
            src_path,
            dst_path,
            trim=trim,
            roi_cx=roi_cx,
            roi_cy=roi_cy,
            roi_x=roi_x,
            roi_y=roi_y,
            roi_w=roi_w,
            roi_h=roi_h,
        )
        return out_w, out_h, "local"
    session = _get_rembg_session()
    data = src_path.read_bytes()
    kwargs = {"session": session, "post_process_mask": True}
    try:
        mask_bytes = remove(data, only_mask=True, **kwargs)
        mask_im = Image.open(io.BytesIO(mask_bytes))
        im = _apply_alpha_mask_to_image(src_path, mask_im)
    except Exception:
        out_bytes = remove(data, **kwargs)
        result = Image.open(io.BytesIO(out_bytes)).convert("RGBA")
        im = _apply_alpha_mask_to_image(src_path, result.getchannel("A"))
    if trim:
        im = _trim_to_subject(im, padding=0)
    if not im.getbbox():
        raise ValueError("未识别到明显主体，请适当扩大框选范围后重试")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst_path, format="PNG", optimize=True)
    out_w, out_h = im.size
    return out_w, out_h, "rembg"


def _flood_clear_light_background(im: Image.Image, threshold: int = 235) -> None:
    rgba = im.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    stack = []
    seen = set()

    def is_light(x: int, y: int) -> bool:
        r, g, b, a = px[x, y]
        return a > 0 and min(r, g, b) >= threshold

    for sx, sy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if is_light(sx, sy):
            stack.append((sx, sy))
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or x < 0 or y < 0 or x >= w or y >= h:
            continue
        if not is_light(x, y):
            continue
        seen.add((x, y))
        r, g, b, _ = px[x, y]
        px[x, y] = (r, g, b, 0)
        stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])


def postprocess_ai_cutout_png(
    src_path: Path,
    dst_path: Path,
    trim: bool = True,
    *,
    force_transparent: bool = True,
) -> tuple[int, int]:
    """后处理 AI 成图。默认把近白底转透明；用户要求白底时保留不透明背景。"""
    im = Image.open(src_path).convert("RGBA")
    if force_transparent:
        px = im.load()
        w, h = im.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a > 0 and min(r, g, b) > 238:
                    px[x, y] = (r, g, b, 0)
        _flood_clear_light_background(im, threshold=235)
    if trim:
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst_path, format="PNG", optimize=True)
    return im.size
