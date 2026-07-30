"""GPT 聊天式生图：线程/消息落盘。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
THREADS_FILE = BASE_DIR / "gpt_chat_threads.json"
_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_all() -> dict:
    if not THREADS_FILE.is_file():
        return {"threads": {}}
    try:
        data = json.loads(THREADS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"threads": {}}
    if not isinstance(data, dict) or not isinstance(data.get("threads"), dict):
        return {"threads": {}}
    return data


def _write_all(data: dict) -> None:
    THREADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    THREADS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_thread(*, project: str, title: str = "", size: str = "1:1", quality: str = "medium") -> dict:
    tid = uuid.uuid4().hex[:12]
    thread = {
        "id": tid,
        "project": project,
        "title": (title or "新对话").strip()[:28] or "新对话",
        "created_at": _now(),
        "updated_at": _now(),
        "size": size or "1:1",
        "quality": quality or "medium",
        "messages": [],
        "history_id": None,
    }
    with _lock:
        data = _read_all()
        data["threads"][tid] = thread
        _write_all(data)
    return dict(thread)


def get_thread(thread_id: str) -> Optional[dict]:
    with _lock:
        data = _read_all()
        t = data["threads"].get(str(thread_id or "").strip())
        return dict(t) if isinstance(t, dict) else None


def _mutate(thread_id: str, fn) -> Optional[dict]:
    with _lock:
        data = _read_all()
        t = data["threads"].get(thread_id)
        if not isinstance(t, dict):
            return None
        fn(t)
        t["updated_at"] = _now()
        data["threads"][thread_id] = t
        _write_all(data)
        return dict(t)


def append_user_message(thread_id: str, *, text: str, image_urls: list | None = None) -> dict:
    msg = {
        "id": uuid.uuid4().hex[:10],
        "role": "user",
        "text": (text or "").strip(),
        "image_urls": list(image_urls or []),
        "created_at": _now(),
    }

    def add(t):
        t.setdefault("messages", []).append(msg)
        if not t.get("title") or t["title"] == "新对话":
            t["title"] = (msg["text"][:28] or t.get("title") or "新对话")

    if _mutate(thread_id, add) is None:
        raise KeyError(thread_id)
    return msg


def append_assistant_pending(thread_id: str, *, job_id: str, message_id: str | None = None) -> dict:
    msg = {
        "id": message_id or uuid.uuid4().hex[:10],
        "role": "assistant",
        "text": "",
        "image_urls": [],
        "job_id": job_id,
        "status": "pending",
        "error": "",
        "created_at": _now(),
    }

    def add(t):
        t.setdefault("messages", []).append(msg)

    if _mutate(thread_id, add) is None:
        raise KeyError(thread_id)
    return msg


def try_append_turn(
    thread_id: str,
    *,
    text: str,
    image_urls: list | None = None,
    assistant_id: str | None = None,
    job_id: str = "",
) -> Optional[dict]:
    user_msg = {
        "id": uuid.uuid4().hex[:10],
        "role": "user",
        "text": (text or "").strip(),
        "image_urls": list(image_urls or []),
        "created_at": _now(),
    }
    assistant_msg = {
        "id": assistant_id or uuid.uuid4().hex[:10],
        "role": "assistant",
        "text": "",
        "image_urls": [],
        "job_id": job_id,
        "status": "pending",
        "error": "",
        "created_at": _now(),
    }

    with _lock:
        data = _read_all()
        t = data["threads"].get(thread_id)
        if not isinstance(t, dict):
            raise KeyError(thread_id)
        for m in t.get("messages") or []:
            if m.get("role") == "assistant" and m.get("status") == "pending":
                return None
        messages = t.setdefault("messages", [])
        messages.append(user_msg)
        messages.append(assistant_msg)
        if not t.get("title") or t["title"] == "新对话":
            t["title"] = (user_msg["text"][:28] or t.get("title") or "新对话")
        t["updated_at"] = _now()
        data["threads"][thread_id] = t
        _write_all(data)
        return {
            "thread": dict(t),
            "user": dict(user_msg),
            "assistant": dict(assistant_msg),
        }


def thread_has_pending(thread_id: str) -> bool:
    t = get_thread(thread_id)
    if not t:
        return False
    for m in t.get("messages") or []:
        if m.get("role") == "assistant" and m.get("status") == "pending":
            return True
    return False


def complete_assistant_message(
    thread_id: str,
    message_id: str,
    *,
    status: str,
    image_urls: list | None = None,
    error: str = "",
) -> Optional[dict]:
    updated = {"ok": False}

    def upd(t):
        for m in t.get("messages") or []:
            if m.get("id") == message_id and m.get("role") == "assistant":
                m["status"] = status
                m["image_urls"] = list(image_urls or [])
                m["error"] = error or ""
                updated["ok"] = True
                updated["msg"] = m
                break

    if _mutate(thread_id, upd) is None:
        return None
    return updated.get("msg")


def fail_stale_pending(
    thread_id: str,
    *,
    reason: str,
    job_ids: list | None = None,
) -> bool:
    stale_ids = {str(j) for j in (job_ids or [])}
    changed = {"ok": False}

    def upd(t):
        for m in t.get("messages") or []:
            if m.get("role") != "assistant" or m.get("status") != "pending":
                continue
            if stale_ids and str(m.get("job_id") or "") not in stale_ids:
                continue
            m["status"] = "error"
            m["image_urls"] = []
            m["error"] = reason or "任务不存在或已过期，请重新发送消息继续对话"
            changed["ok"] = True

    if _mutate(thread_id, upd) is None:
        return False
    return bool(changed["ok"])


def set_assistant_job_id(thread_id: str, message_id: str, job_id: str) -> Optional[dict]:
    updated = {"ok": False}

    def upd(t):
        for m in t.get("messages") or []:
            if m.get("id") == message_id and m.get("role") == "assistant":
                m["job_id"] = job_id
                updated["ok"] = True
                updated["msg"] = m
                break

    if _mutate(thread_id, upd) is None:
        return None
    return updated.get("msg")


def last_success_image(thread_id: str) -> Optional[str]:
    t = get_thread(thread_id)
    if not t:
        return None
    for m in reversed(t.get("messages") or []):
        if m.get("role") != "assistant" or m.get("status") != "done":
            continue
        urls = m.get("image_urls") or []
        if urls:
            return str(urls[0])
    return None


def set_thread_prefs(thread_id: str, *, size: str | None = None, quality: str | None = None) -> None:
    def upd(t):
        if size:
            t["size"] = size
        if quality:
            t["quality"] = quality

    _mutate(thread_id, upd)


def set_history_id(thread_id: str, history_id: str) -> None:
    def upd(t):
        t["history_id"] = history_id

    _mutate(thread_id, upd)
