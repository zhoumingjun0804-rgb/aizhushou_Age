"""按项目组配置设计类型与参考图目录。"""
import json
import os
import re
from pathlib import Path

from multi_size_export import normalize_product_type

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", str(BASE_DIR / "projects")))
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_FOLDER_RE = re.compile(r"^(\d+)-(.+)$")
_TYPES_DIRNAME = "types"
_REFS_DIRNAME = "refs"

# 静态设计类型项目组（小灯塔等）：types 目录不存在时使用
STATIC_DESIGN_TYPES: list[dict] = [
    {"value": "海报", "label": "海报", "defaultRatio": "9:16"},
    {"value": "传单", "label": "传单", "defaultRatio": "9:16"},
    {"value": "Banner", "label": "Banner", "defaultRatio": "16:9"},
    {"value": "朋友圈图", "label": "朋友圈图", "defaultRatio": "1:1"},
    {"value": "公众号封面", "label": "公众号封面", "defaultRatio": "16:9"},
    {"value": "PPT封面", "label": "PPT封面", "defaultRatio": "16:9"},
    {"value": "其他", "label": "其他", "defaultRatio": None},
]

FOLDER_TYPE_RATIO_BY_LABEL: dict[str, str] = {
    "banner": "16:9",
    "弹窗": "1:1",
    "品宣海报": "9:16",
    "销售海报": "9:16",
    "倒计时海报": "9:16",
    "开屏": "9:16",
    "直播间": "16:9",
    "课程表": "16:9",
    "画具清单": "16:9",
    "礼品海报": "9:16",
    "投放素材": "1:1",
    "好友添加页": "9:16",
    "公众号海报": "16:9",
    "拼团海报": "9:16",
    "转介绍海报": "9:16",
    "小程序封面图": "1:1",
}

# 项目组名 → 开屏导出 product_type（output_sizes*.json）
PROJECT_PRODUCT_TYPE_BY_NAME: dict[str, str] = {
    "画啦啦": "hll",
    "小灯塔": "xdt",
}


def _is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _IMAGE_EXTS


def project_dir(project_name: str) -> Path:
    return PROJECTS_DIR / project_name


def project_types_dir(project_name: str) -> Path:
    return project_dir(project_name) / _TYPES_DIRNAME


def project_refs_dir(project_name: str) -> Path:
    """扁平参考图目录：优先 refs/，否则项目组根目录（兼容旧数据）。"""
    refs = project_dir(project_name) / _REFS_DIRNAME
    if refs.is_dir():
        return refs
    return project_dir(project_name)


def read_project_meta(project_name: str) -> dict:
    meta_file = project_dir(project_name) / "project.json"
    if meta_file.is_file():
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"name": project_name}


def folder_type_label(folder_name: str) -> str:
    m = _FOLDER_RE.match(folder_name.strip())
    return m.group(2) if m else folder_name.strip()


def detect_project_catalog(project_name: str) -> str:
    """folder_types：types/ 下按子文件夹；static_types：固定设计类型列表。"""
    meta = read_project_meta(project_name)
    catalog = (meta.get("catalog") or "").strip()
    if catalog in ("folder_types", "static_types"):
        return catalog
    types_dir = project_types_dir(project_name)
    if types_dir.is_dir():
        for child in types_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                return "folder_types"
    return "static_types"


def project_product_type(project_name: str) -> str:
    meta = read_project_meta(project_name)
    raw = (meta.get("product_type") or "").strip()
    if raw:
        return normalize_product_type(raw)
    return normalize_product_type(PROJECT_PRODUCT_TYPE_BY_NAME.get(project_name, "xdt"))


def resolve_type_folder(project_name: str, design_value: str) -> str | None:
    raw = (design_value or "").strip()
    if not raw:
        return None
    types_dir = project_types_dir(project_name)
    if not types_dir.is_dir():
        return None
    direct = types_dir / raw
    if direct.is_dir():
        return raw
    for item in types_dir.iterdir():
        if item.is_dir() and folder_type_label(item.name) == raw:
            return item.name
    return None


def list_design_types_for_project(project_name: str) -> list[dict]:
    if not project_name:
        return []
    catalog = detect_project_catalog(project_name)
    if catalog == "folder_types":
        types_dir = project_types_dir(project_name)
        if not types_dir.is_dir():
            return []
        result: list[dict] = []
        for path in sorted(types_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            folder = path.name
            label = folder_type_label(folder)
            result.append({
                "value": folder,
                "label": label,
                "folder": folder,
                "defaultRatio": FOLDER_TYPE_RATIO_BY_LABEL.get(label),
                "referenceCount": len(list_typed_reference_images(project_name, folder)),
            })
        return result
    return [dict(item) for item in STATIC_DESIGN_TYPES]


def list_flat_reference_images(project_name: str) -> list[str]:
    ref_dir = project_refs_dir(project_name)
    if not ref_dir.is_dir():
        return []
    names: list[str] = []
    for f in sorted(ref_dir.iterdir()):
        if _is_image(f) and f.name != "project.json":
            names.append(f.name)
    return names


def list_typed_reference_images(project_name: str, design_type: str) -> list[str]:
    folder = resolve_type_folder(project_name, design_type)
    if not folder:
        return []
    ref_dir = project_types_dir(project_name) / folder
    if not ref_dir.is_dir():
        return []
    return sorted(f.name for f in ref_dir.iterdir() if _is_image(f))


def typed_reference_dir(project_name: str, design_type: str) -> Path | None:
    folder = resolve_type_folder(project_name, design_type)
    if not folder:
        return None
    ref_dir = project_types_dir(project_name) / folder
    return ref_dir if ref_dir.is_dir() else None


def count_project_assets(project_name: str) -> dict:
    catalog = detect_project_catalog(project_name)
    if catalog == "folder_types":
        types_dir = project_types_dir(project_name)
        type_count = 0
        image_count = 0
        if types_dir.is_dir():
            for child in types_dir.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    type_count += 1
                    image_count += sum(1 for f in child.iterdir() if _is_image(f))
        flat_count = len(list_flat_reference_images(project_name))
        return {
            "catalog": catalog,
            "imageCount": image_count + flat_count,
            "typeCount": type_count,
            "flatCount": flat_count,
        }
    flat = list_flat_reference_images(project_name)
    return {
        "catalog": catalog,
        "imageCount": len(flat),
        "typeCount": 0,
        "flatCount": len(flat),
    }


def resolve_reference_image_path(img_ref: str, project: str = "") -> Path | None:
    ref = (img_ref or "").strip()
    if not ref:
        return None

    # 新格式：项目组/types/文件夹/文件
    marker = "/types/"
    if marker in ref:
        proj_part, rest = ref.split(marker, 1)
        proj_name = proj_part.strip() or (project or "").strip()
        if not proj_name or "/" not in rest:
            return None
        folder, filename = rest.split("/", 1)
        ref_dir = typed_reference_dir(proj_name, folder)
        if not ref_dir:
            return None
        path = ref_dir / filename
        return path if path.is_file() else None

    # 兼容旧格式 hll/06-开屏/file → 画啦啦/types/...
    if ref.startswith("hll/"):
        parts = ref.split("/", 2)
        if len(parts) >= 3:
            return resolve_reference_image_path(
                f"画啦啦/types/{parts[1]}/{parts[2]}", "画啦啦"
            )
        return None

    proj_name = project.strip()
    if "/" in ref:
        proj_name, img_name = ref.split("/", 1)
        path = project_refs_dir(proj_name) / img_name
    else:
        path = project_refs_dir(proj_name) / ref
    return path if path.is_file() else None


def collect_reference_image_paths(
    selected_list: list,
    project: str = "",
    *,
    max_count: int = 10,
) -> list[Path]:
    paths: list[Path] = []
    for img_ref in selected_list[:max_count]:
        path = resolve_reference_image_path(str(img_ref), project)
        if path:
            paths.append(path)
    return paths

# 兼容旧 API 命名
list_design_types = list_design_types_for_project
