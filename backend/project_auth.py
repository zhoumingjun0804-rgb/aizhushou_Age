"""项目组常量与实例固定项目（FIXED_PROJECT）。"""
from __future__ import annotations

import os
from typing import Optional

ALLOWED_PROJECTS = ("画啦啦", "小灯塔")
_PROJECT_SLUG = {"画啦啦": "HLL", "小灯塔": "XDT"}


def project_slug(project: str) -> str:
    slug = _PROJECT_SLUG.get(project)
    if not slug:
        raise ValueError(f"unknown project: {project}")
    return slug


def fixed_project() -> Optional[str]:
    """实例锁定的项目组。未配置或非法值时返回 None。"""
    value = os.environ.get("FIXED_PROJECT", "").strip()
    if value in ALLOWED_PROJECTS:
        return value
    return None
