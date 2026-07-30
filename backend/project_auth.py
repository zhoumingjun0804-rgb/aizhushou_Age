"""项目组门禁：密码校验 + 内存 token。"""
from __future__ import annotations

import os
import secrets
import time
import uuid
from typing import Optional

TOKEN_TTL_SECONDS = 12 * 3600

ALLOWED_PROJECTS = ("画啦啦", "小灯塔")
_PROJECT_SLUG = {"画啦啦": "HLL", "小灯塔": "XDT"}
_PASSWORD_ENV = {"画啦啦": "PROJECT_PASSWORD_HLL", "小灯塔": "PROJECT_PASSWORD_XDT"}

_tokens: dict[str, dict] = {}


def is_gate_enabled() -> bool:
    """门禁已移除；保留函数仅兼容现有调用。"""
    return False


def fixed_project() -> Optional[str]:
    value = os.environ.get("FIXED_PROJECT", "").strip()
    return value if value in ALLOWED_PROJECTS else None


def project_slug(project: str) -> str:
    slug = _PROJECT_SLUG.get(project)
    if not slug:
        raise ValueError(f"unknown project: {project}")
    return slug


def password_for(project: str) -> Optional[str]:
    env_name = _PASSWORD_ENV.get(project)
    if not env_name:
        return None
    value = os.environ.get(env_name, "").strip()
    return value or None


def unlock(project: str, password: str) -> Optional[str]:
    if project not in ALLOWED_PROJECTS:
        return None
    expected = password_for(project)
    if not expected:
        return None
    if not secrets.compare_digest(password, expected):
        return None
    token = uuid.uuid4().hex
    _tokens[token] = {"project": project, "created_at": time.time()}
    return token


def resolve_token(token: str) -> Optional[dict]:
    if not token:
        return None
    entry = _tokens.get(token)
    if not entry:
        return None
    if time.time() - entry["created_at"] > TOKEN_TTL_SECONDS:
        _tokens.pop(token, None)
        return None
    return {"project": entry["project"]}
