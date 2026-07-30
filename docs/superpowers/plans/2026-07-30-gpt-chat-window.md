# GPT 聊天式生图窗口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增主导航「GPT」聊天页：单输入框多轮文生图/改图，固定 GPT Image，服务端持久化线程，历史可回看续聊；复用现有 GPT 队列。

**Architecture:** 新建 `backend/gpt_chat.py` 管线程/消息落盘与 history 摘要；`POST /api/gpt-chat/...` 鉴权后组装 `image_backend=gpt` 的 generation payload 提交 `gpt_queue`；`execute_generation_job` 在带 `gpt_chat_thread_id` 时回写 assistant 消息并 upsert `mode=gpt_chat` 摘要（跳过普通生图 history）。前端新增 `gptTab` 消息流 + 输入框。

**Tech Stack:** Python 3.10+、`unittest`、现有 `LovartQueue`/`gpt_queue`、`backend/templates/index.html`

**Spec:** `docs/superpowers/specs/2026-07-30-gpt-chat-window-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/gpt_chat.py` | Create | 线程/消息 CRUD、上一张成功图、pending 检查、history 摘要 upsert |
| `backend/tests/test_gpt_chat_store.py` | Create | 存储层与续聊/pending 规则单测 |
| `backend/tests/test_gpt_chat_api.py` | Create | HTTP 鉴权、发消息模式、跨项目 403 |
| `backend/tests/test_gpt_chat_ui.py` | Create | 模板静态断言：GPT Tab、输入框、无必选表单 |
| `backend/app.py` | Modify | 路由、handler、job 完成钩子、`_history_mode_label` 支持 `gpt_chat` |
| `backend/templates/index.html` | Modify | GPT Tab UI/CSS/JS、历史摘要点击续聊 |
| `gpt_chat_threads.json` | Runtime | 运行时落盘（gitignore 若尚未忽略则加入；勿提交真实数据） |

---

### Task 1: 线程存储层（TDD）

**Files:**
- Create: `backend/tests/test_gpt_chat_store.py`
- Create: `backend/gpt_chat.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_gpt_chat_store.py
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gpt_chat


class GptChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "gpt_chat_threads.json"
        self.patcher = mock.patch.object(gpt_chat, "THREADS_FILE", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_create_thread_and_append_messages(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="暑期海报")
        self.assertEqual(thread["project"], "小灯塔")
        user = gpt_chat.append_user_message(thread["id"], text="画一只猫", image_urls=[])
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="job1")
        self.assertEqual(user["role"], "user")
        self.assertEqual(asst["status"], "pending")
        loaded = gpt_chat.get_thread(thread["id"])
        self.assertEqual(len(loaded["messages"]), 2)

    def test_reject_when_pending(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        self.assertTrue(gpt_chat.thread_has_pending(thread["id"]))

    def test_last_success_image(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        gpt_chat.complete_assistant_message(
            thread["id"], asst["id"], status="done", image_urls=["variant_a.png"], error=""
        )
        self.assertEqual(gpt_chat.last_success_image(thread["id"]), "variant_a.png")

    def test_complete_error_clears_pending(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        gpt_chat.complete_assistant_message(
            thread["id"], asst["id"], status="error", image_urls=[], error="失败"
        )
        self.assertFalse(gpt_chat.thread_has_pending(thread["id"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_store -v
```

Expected: FAIL（`gpt_chat` 模块不存在或符号缺失）

- [ ] **Step 3: 最小实现**

```python
# backend/gpt_chat.py
"""GPT 聊天式生图：线程/消息落盘。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

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


def append_assistant_pending(thread_id: str, *, job_id: str) -> dict:
    msg = {
        "id": uuid.uuid4().hex[:10],
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
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_store -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/gpt_chat.py backend/tests/test_gpt_chat_store.py
git commit -m "$(cat <<'EOF'
feat: GPT 聊天线程落盘存储

EOF
)"
```

---

### Task 2: History 摘要与 mode 标签

**Files:**
- Modify: `backend/app.py`（`_history_mode_label`、新增 `upsert_gpt_chat_history`）
- Modify: `backend/tests/test_gpt_chat_store.py`（或新建 `test_gpt_chat_history.py`）
- Modify: `backend/gpt_chat.py`（可选：导出 `RATIO_PRESETS`）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_gpt_chat_history.py`：

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import gpt_chat


class GptChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.threads = Path(self.tmp.name) / "threads.json"
        self.history = Path(self.tmp.name) / "history.json"
        self.history.write_text("[]", encoding="utf-8")
        self.p1 = mock.patch.object(gpt_chat, "THREADS_FILE", self.threads)
        self.p2 = mock.patch.object(app, "HISTORY_FILE", self.history)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.tmp.cleanup()

    def test_mode_label_gpt_chat(self):
        self.assertEqual(app._history_mode_label("gpt_chat"), "💬GPT对话")

    def test_upsert_summary_creates_then_updates(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="画猫")
        hid = app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="画猫",
            output_images=["a.png"],
        )
        items = app.load_history()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["mode"], "gpt_chat")
        self.assertEqual(items[0]["thread_id"], thread["id"])
        self.assertEqual(items[0]["id"], hid)
        app.upsert_gpt_chat_history(
            thread_id=thread["id"],
            project="小灯塔",
            prompt="改成蓝色",
            output_images=["b.png"],
        )
        items = app.load_history()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["output_images"], ["b.png"])
        self.assertIn("改成蓝色", items[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_history -v
```

Expected: FAIL（缺 `upsert_gpt_chat_history` 或 label）

- [ ] **Step 3: 实现**

在 `app.py` 的 `_history_mode_label` 增加：

```python
if mode == "gpt_chat":
    return "💬GPT对话"
```

新增：

```python
def upsert_gpt_chat_history(*, thread_id: str, project: str, prompt: str, output_images: list) -> str:
    """每条 GPT 对话线程只保留一条 history 摘要；按 thread_id 更新。"""
    history = load_history()
    existing = None
    for item in history:
        if item.get("mode") == "gpt_chat" and item.get("thread_id") == thread_id:
            existing = item
            break
    thread = None
    try:
        import gpt_chat as _gc
        thread = _gc.get_thread(thread_id)
    except Exception:
        thread = None
    history_id = (existing or {}).get("id") or (thread or {}).get("history_id") or uuid.uuid4().hex[:8]
    entry = build_history_entry(
        id=history_id,
        mode="gpt_chat",
        prompt=prompt or "",
        description="",
        source="gpt_chat",
        project=project or "",
        thread_id=thread_id,
        output_images=list(output_images or []),
        variants_count=len(output_images or []),
    )
    if existing:
        history = [entry if (i.get("id") == history_id) else i for i in history]
        # 移到最前
        history = [entry] + [i for i in history if i.get("id") != history_id]
    else:
        history.insert(0, entry)
    save_history(history)
    try:
        import gpt_chat as _gc
        _gc.set_history_id(thread_id, history_id)
    except Exception:
        pass
    return history_id
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_history -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/tests/test_gpt_chat_history.py
git commit -m "$(cat <<'EOF'
feat: GPT 对话 history 摘要 upsert

EOF
)"
```

---

### Task 3: Job 完成钩子（聊天回写）

**Files:**
- Modify: `backend/app.py` — `execute_generation_job`
- Create: `backend/tests/test_gpt_chat_job_hook.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_gpt_chat_job_hook.py
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import gpt_chat


class GptChatJobHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.threads = Path(self.tmp.name) / "threads.json"
        self.history = Path(self.tmp.name) / "history.json"
        self.history.write_text("[]", encoding="utf-8")
        self.p1 = mock.patch.object(gpt_chat, "THREADS_FILE", self.threads)
        self.p2 = mock.patch.object(app, "HISTORY_FILE", self.history)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.tmp.cleanup()

    def test_notify_done_updates_message_and_history(self):
        thread = gpt_chat.create_thread(project="小灯塔", title="猫")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="jobx")
        payload = {
            "gpt_chat_thread_id": thread["id"],
            "gpt_chat_assistant_id": asst["id"],
            "project": "小灯塔",
            "prompt": "画猫",
        }
        app._notify_gpt_chat_job(payload, status="done", output_images=["out.png"], error="")
        t = gpt_chat.get_thread(thread["id"])
        msg = next(m for m in t["messages"] if m["id"] == asst["id"])
        self.assertEqual(msg["status"], "done")
        self.assertEqual(msg["image_urls"], ["out.png"])
        hist = app.load_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["mode"], "gpt_chat")

    def test_notify_error_sets_error_no_regular_requirement(self):
        thread = gpt_chat.create_thread(project="小灯塔")
        asst = gpt_chat.append_assistant_pending(thread["id"], job_id="joby")
        payload = {
            "gpt_chat_thread_id": thread["id"],
            "gpt_chat_assistant_id": asst["id"],
            "project": "小灯塔",
            "prompt": "画猫",
        }
        app._notify_gpt_chat_job(payload, status="error", output_images=[], error="超时")
        t = gpt_chat.get_thread(thread["id"])
        msg = next(m for m in t["messages"] if m["id"] == asst["id"])
        self.assertEqual(msg["status"], "error")
        self.assertEqual(msg["error"], "超时")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_job_hook -v
```

Expected: FAIL（缺 `_notify_gpt_chat_job`）

- [ ] **Step 3: 实现钩子并接入 `execute_generation_job`**

```python
def _notify_gpt_chat_job(payload: dict, *, status: str, output_images=None, error: str = "") -> None:
    tid = str((payload or {}).get("gpt_chat_thread_id") or "").strip()
    mid = str((payload or {}).get("gpt_chat_assistant_id") or "").strip()
    if not tid or not mid:
        return
    import gpt_chat
    gpt_chat.complete_assistant_message(
        tid, mid, status=status, image_urls=list(output_images or []), error=error or ""
    )
    if status == "done" and output_images:
        upsert_gpt_chat_history(
            thread_id=tid,
            project=str(payload.get("project") or ""),
            prompt=str(payload.get("prompt") or ""),
            output_images=list(output_images),
        )
```

在 `execute_generation_job` 写 history 处改为：

```python
    is_gpt_chat = bool(str(payload.get("gpt_chat_thread_id") or "").strip())
    output_images = [v["filename"] for v in variants if v.get("filename")]
    if is_gpt_chat:
        if output_images:
            _notify_gpt_chat_job(payload, status="done", output_images=output_images)
        else:
            err = next((v.get("error") for v in variants if v.get("error")), None) or "生成失败"
            _notify_gpt_chat_job(payload, status="error", output_images=[], error=err)
        return

    if variants and variants[0].get("filename"):
        # 现有 add_history(...)
```

在所有 `q.fail_job(job_id, ...)` 之后（本函数内、且 payload 含 chat id）调用 `_notify_gpt_chat_job(..., status="error", error=...)`。最小做法：封装本地 helper：

```python
    def fail(msg: str):
        q.fail_job(job_id, msg)
        _notify_gpt_chat_job(payload, status="error", error=msg)
```

把本函数内的 `q.fail_job` 换成 `fail`。

- [ ] **Step 4: 运行确认通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_job_hook tests.test_gpt_chat_history -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/tests/test_gpt_chat_job_hook.py
git commit -m "$(cat <<'EOF'
feat: 生图 job 完成回写 GPT 对话消息

EOF
)"
```

---

### Task 4: HTTP API（建线程 / 拉线程 / 发消息）

**Files:**
- Modify: `backend/app.py` — `do_GET` / `do_POST` / `do_OPTIONS`、handlers
- Create: `backend/tests/test_gpt_chat_api.py`

**约定**

- `POST /api/gpt-chat/threads` JSON `{project}` → `{thread}`
- `GET /api/gpt-chat/threads/{id}?project=` → `{thread}`
- `POST /api/gpt-chat/threads/{id}/messages` multipart：`project`、`client_id`、`text`、`ratio`（默认 `1:1`）、`gpt_output_quality`（默认 `medium`）、`ref_image_0..n`
- 比例映射：`1:1→1024×1024`，`16:9→1920×1080`，`9:16→1080×1920`（用已有 `ONLINE_RATIO_TO_SIZE`）
- 无附图且线程有上一张成功图 → `image_paths=[OUTPUT_DIR/filename]`，`mode=img2img`
- 有附图 → 只用不超 3 张附图
- `image_backend` 固定 `gpt`；`count=1`；`kind=with_prompt`
- pending 中 → 409
- 项目不一致 → 403
- GPT 不可用 → 400

- [ ] **Step 1: 写失败测试（核心路径用 mock 队列）**

```python
# backend/tests/test_gpt_chat_api.py
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import app
import gpt_chat


def _multipart(fields: dict, files: dict | None = None) -> tuple[bytes, str]:
    boundary = "----TestBoundary7"
    chunks = []
    for k, v in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    for k, (filename, data) in (files or {}).items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        )
        chunks.append(data)
        chunks.append("\r\n")
    chunks.append(f"--{boundary}--\r\n")
    body = b""
    for c in chunks:
        body += c if isinstance(c, bytes) else c.encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class GptChatApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.threads = Path(self.tmp.name) / "threads.json"
        self.history = Path(self.tmp.name) / "history.json"
        self.history.write_text("[]", encoding="utf-8")
        self.p1 = mock.patch.object(gpt_chat, "THREADS_FILE", self.threads)
        self.p2 = mock.patch.object(app, "HISTORY_FILE", self.history)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.tmp.cleanup()

    def _handler(self):
        h = app.Handler.__new__(app.Handler)
        h.headers = {}
        h.path = "/"
        h.rfile = io.BytesIO()
        h.wfile = io.BytesIO()
        h._json_out = None
        h._status = None

        def send_json(obj, status=200):
            h._json_out = obj
            h._status = status

        h._send_json = send_json
        return h

    @mock.patch.object(app.Handler, "_auth_project", return_value="小灯塔")
    def test_create_thread(self, _auth):
        h = self._handler()
        h.path = "/api/gpt-chat/threads"
        h.headers = {"Content-Type": "application/json", "Content-Length": "0"}
        body = json.dumps({"project": "小灯塔"}).encode()
        h.headers["Content-Length"] = str(len(body))
        h.rfile = io.BytesIO(body)
        h._handle_gpt_chat_create_thread()
        self.assertEqual(h._status, 201)
        self.assertIn("thread", h._json_out)

    @mock.patch.object(app.Handler, "_auth_project", return_value="小灯塔")
    @mock.patch("app.image_backend_allowed", return_value=True)
    @mock.patch("app.raise_if_duplicate_high")
    def test_message_text2img_submits_gpt_job(self, _dup, _allowed, _auth):
        thread = gpt_chat.create_thread(project="小灯塔")
        h = self._handler()
        body, ctype = _multipart({
            "project": "小灯塔",
            "client_id": "c1",
            "text": "画一只猫",
            "ratio": "1:1",
            "gpt_output_quality": "medium",
        })
        h.headers = {"Content-Type": ctype, "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        submitted = {}

        class Q:
            def submit_generation(self, payload, fn):
                submitted["payload"] = payload
                return "job123"

            def get_job(self, jid):
                return {"status": "queued", "position": 0}

        with mock.patch.object(app, "gpt_queue", Q()):
            h._handle_gpt_chat_post_message(thread["id"])
        self.assertEqual(h._status, 201)
        self.assertEqual(submitted["payload"]["mode"], "text2img")
        self.assertEqual(submitted["payload"]["image_backend"], "gpt")
        self.assertEqual(submitted["payload"]["count"], 1)
        self.assertTrue(submitted["payload"]["gpt_chat_thread_id"])

    @mock.patch.object(app.Handler, "_auth_project", return_value="小灯塔")
    def test_message_rejects_pending(self, _auth):
        thread = gpt_chat.create_thread(project="小灯塔")
        gpt_chat.append_assistant_pending(thread["id"], job_id="j1")
        h = self._handler()
        body, ctype = _multipart({
            "project": "小灯塔", "client_id": "c1", "text": "再改一下",
        })
        h.headers = {"Content-Type": ctype, "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h._handle_gpt_chat_post_message(thread["id"])
        self.assertEqual(h._status, 409)

    @mock.patch.object(app.Handler, "_auth_project", return_value="画啦啦")
    def test_get_thread_wrong_project_403(self, _auth):
        thread = gpt_chat.create_thread(project="小灯塔")
        h = self._handler()
        h.path = f"/api/gpt-chat/threads/{thread['id']}?project={quote('画啦啦')}"
        h._handle_gpt_chat_get_thread(thread["id"])
        self.assertEqual(h._status, 403)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_api -v
```

Expected: FAIL（缺 handlers）

- [ ] **Step 3: 实现 handlers**

在 `app.py` 增加（逻辑要点）：

```python
GPT_CHAT_RATIOS = {"1:1", "16:9", "9:16"}

def build_gpt_chat_generation_payload(
    *,
    project: str,
    client_id: str,
    prompt: str,
    ratio: str,
    quality: str,
    image_paths: list,
    thread_id: str,
    assistant_id: str,
) -> dict:
    ratio = ratio if ratio in GPT_CHAT_RATIOS else "1:1"
    w, h = ONLINE_RATIO_TO_SIZE.get(ratio, (1024, 1024))
    fields = {"gpt_output_quality": quality or "medium", "gpt_tier": "balanced"}
    return {
        "kind": "with_prompt",
        "client_id": client_id,
        "project": project,
        "count": 1,
        "prompt": prompt,
        "ratio": ratio,
        "output_width": w,
        "output_height": h,
        "size_mode": "online",
        "dpi": None,
        "size_label": f"{w}×{h}",
        "width_mm": None,
        "height_mm": None,
        "image_backend": "gpt",
        "image_backend_raw": "gpt",
        "gpt_model": resolve_gpt_model("gpt", fields),
        "gpt_output_quality": resolve_gpt_output_quality(fields),
        "image_paths": [str(p) for p in image_paths],
        "logo_path": None,
        "logo_position": "top_left",
        "input_filename": None,
        "mode": "img2img" if image_paths else "text2img",
        "gpt_chat_thread_id": thread_id,
        "gpt_chat_assistant_id": assistant_id,
    }
```

Handlers 伪代码（实现时写完整）：

- `_handle_gpt_chat_create_thread`：读 JSON `project` → `_auth_project` → `gpt_chat.create_thread` → 201
- `_handle_gpt_chat_get_thread(tid)`：query `project` → auth → get_thread → project 必须相等 → 200
- `_handle_gpt_chat_post_message(tid)`：
  1. multipart parse
  2. auth project
  3. get thread；缺失 404；project 不符 403
  4. `thread_has_pending` → 409
  5. text 空 → 400
  6. `image_backend_allowed(project, "gpt")` 否则 400
  7. `_save_ref_images_from_fields` 取最多 3 张；若空则 `last_success_image` → `OUTPUT_DIR / name`（文件存在才加入）
  8. `append_user_message`（用户附图存为上传后的相对展示路径：可用 `/outputs/` 不合适，用户附图用上传文件名列表或空，前端已有本地预览；服务端 `image_urls` 对 user 可存上传 basename）
  9. 先占位：需要 `assistant_id` 才能进 payload → 先 `append_assistant_pending(job_id="pending")` 再 submit 后把 `job_id` 写回消息，或 submit 前用临时 id。推荐顺序：
     - 生成 `assistant_id = uuid...`
     - build payload with that id
     - submit → job_id
     - append_user_message
     - append_assistant_pending with real job_id，但 complete 钩子用 payload 里的 assistant_id —— **因此 pending 消息必须用 payload 里同一个 id**。

调整 `append_assistant_pending` 支持可选 `message_id=`：

```python
def append_assistant_pending(thread_id: str, *, job_id: str, message_id: str | None = None) -> dict:
    msg = {
        "id": message_id or uuid.uuid4().hex[:10],
        ...
    }
```

发消息顺序：

```python
assistant_id = uuid.uuid4().hex[:10]
payload = build_gpt_chat_generation_payload(..., assistant_id=assistant_id)
with _generation_admit_lock:
    raise_if_duplicate_high(...)
    job_id = gpt_queue.submit_generation(payload, execute_generation_job)
gpt_chat.append_user_message(...)
gpt_chat.append_assistant_pending(tid, job_id=job_id, message_id=assistant_id)
gpt_chat.set_thread_prefs(tid, size=ratio, quality=quality)
return 201 {thread, job_id, status_url}
```

路由：

- GET：`path.startswith('/api/gpt-chat/threads/')` 且无 `/messages`
- POST：`/api/gpt-chat/threads`；`/api/gpt-chat/threads/{id}/messages`
- OPTIONS 白名单加上上述路径
- GET/POST 均需 `_auth_any` 或 `_auth_project`（与 generation jobs 一致）

- [ ] **Step 4: 运行确认通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_api tests.test_gpt_chat_store tests.test_gpt_chat_job_hook -v
```

Expected: PASS

另加一条单测（可同文件）：续聊无图时 `mode==img2img` 且 `image_paths` 指向上一张。实现后跑通。

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/gpt_chat.py backend/tests/test_gpt_chat_api.py
git commit -m "$(cat <<'EOF'
feat: GPT 聊天建线程与发消息 API

EOF
)"
```

---

### Task 5: 前端 GPT Tab 骨架（静态）

**Files:**
- Create: `backend/tests/test_gpt_chat_ui.py`
- Modify: `backend/templates/index.html`

- [ ] **Step 1: 写失败静态测试**

```python
# backend/tests/test_gpt_chat_ui.py
import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"


class GptChatUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_gpt_nav_tab_exists(self):
        self.assertRegex(self.html, r'data-tab="gpt"[^>]*>.*GPT')

    def test_gpt_tab_panel_and_composer(self):
        self.assertIn('id="gptTab"', self.html)
        self.assertIn('id="gptChatMessages"', self.html)
        self.assertIn('id="gptChatInput"', self.html)
        self.assertIn('id="gptChatSendBtn"', self.html)
        self.assertIn('id="gptChatFileInput"', self.html)

    def test_gpt_optional_ratio_quality(self):
        self.assertIn('id="gptChatRatioSelect"', self.html)
        self.assertIn('id="gptChatQualitySelect"', self.html)

    def test_gpt_tab_has_no_logo_or_structured_fields(self):
        # gptTab 区块内不应出现结构化生图表单控件 id
        m = re.search(r'id="gptTab"[\s\S]*?(?=<div class="card tab-content"|$)', self.html)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertNotIn('id="mainTitle"', block)
        self.assertNotIn('id="logoPositionSelect"', block)
        self.assertNotIn('id="requirementName"', block)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_ui -v
```

Expected: FAIL

- [ ] **Step 3: 在 `index.html` 加入导航与面板**

主导航（「修图」按钮后）：

```html
<button type="button" class="tab-btn" data-tab="gpt" onclick="switchTab('gpt', this)">💬 GPT</button>
```

在 `generateTab` 卡片后增加 `gptTab`（结构）：

```html
<div class="card tab-content" id="gptTab">
  <div id="gptChatMessages" class="gpt-chat-messages" aria-live="polite"></div>
  <div class="gpt-chat-composer">
    <div id="gptChatAttachPreview" class="gpt-chat-attach-preview"></div>
    <div class="gpt-chat-composer-row">
      <input type="file" id="gptChatFileInput" accept="image/*" multiple hidden>
      <button type="button" id="gptChatAttachBtn" title="上传参考图">📎</button>
      <select id="gptChatRatioSelect" title="比例">
        <option value="1:1" selected>1:1</option>
        <option value="16:9">16:9</option>
        <option value="9:16">9:16</option>
      </select>
      <select id="gptChatQualitySelect" title="画质">
        <option value="medium" selected>中等</option>
        <option value="high">高</option>
        <option value="low">低</option>
      </select>
      <textarea id="gptChatInput" rows="2" placeholder="描述你想生成或修改的画面…"></textarea>
      <button type="button" id="gptChatSendBtn" onclick="sendGptChatMessage()">发送</button>
    </div>
    <p class="gpt-chat-hint">可不选比例/画质（默认 1024×1024 · 中等）。续聊不附图时自动基于上一张结果修改。</p>
  </div>
</div>
```

加少量 CSS（沿用现有深色变量，消息气泡左右对齐，composer 吸底）。**不要**引入新的品牌色体系；匹配现有 `.card` / 按钮风格。

- [ ] **Step 4: 运行确认通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_ui -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/templates/index.html backend/tests/test_gpt_chat_ui.py
git commit -m "$(cat <<'EOF'
feat: 增加 GPT 聊天 Tab 骨架

EOF
)"
```

---

### Task 6: 前端发消息、轮询、渲染

**Files:**
- Modify: `backend/templates/index.html`（JS）
- Modify: `backend/tests/test_gpt_chat_ui.py`（断言关键函数名存在）

- [ ] **Step 1: 扩展静态测试**

```python
def test_gpt_chat_js_helpers_exist(self):
    for name in (
        "sendGptChatMessage",
        "renderGptChatThread",
        "openGptChatThread",
        "pollGptChatJob",
    ):
        self.assertIn("function " + name, self.html)
```

跑测确认失败。

- [ ] **Step 2: 实现 JS（贴入 `index.html` 合适位置）**

状态：

```javascript
var gptChatThreadId = null;
var gptChatAttachFiles = []; // File[]，最多 3
```

核心流程：

1. `sendGptChatMessage`：读 textarea；空则 return；`appendProjectContext`/`getSelectedProjectName` 取项目；若无 `gptChatThreadId` 则 `POST /api/gpt-chat/threads`；再 `FormData` 提交 messages（`text`、`client_id=getClientId()`、`ratio`、`gpt_output_quality`、`ref_image_i`）；成功后清空输入与附件，`renderGptChatThread(resp.thread)`，若有 `job_id` 则 `pollGptChatJob`。
2. `pollGptChatJob(jobId)`：复用现有 `authFetch('/api/generation/jobs/'+id)` 轮询逻辑（可抄 `waitForGenerationJob` 间隔）；结束后 `GET /api/gpt-chat/threads/{id}` 刷新消息（服务端钩子已写完状态）。
3. `renderGptChatThread(thread)`：渲染 user/assistant 气泡；assistant pending 显示「生成中…」；done 显示 `/outputs/{filename}`；error 显示 `error`。
4. 附件：attach 按钮触发 file input；Ctrl+V 在 gptTab 聚焦时粘贴图片进 `gptChatAttachFiles`。
5. 发送中禁用发送按钮；409/未配置 GPT 用 `alert` 或气泡提示。

- [ ] **Step 3: 静态测试通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_ui -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/templates/index.html backend/tests/test_gpt_chat_ui.py
git commit -m "$(cat <<'EOF'
feat: GPT 聊天发送与轮询渲染

EOF
)"
```

---

### Task 7: 历史摘要点击续聊

**Files:**
- Modify: `backend/templates/index.html` — `_renderHistoryItemHtml` / 点击处理
- Modify: `backend/tests/test_gpt_chat_ui.py`

- [ ] **Step 1: 测试断言**

```python
def test_history_opens_gpt_chat_thread(self):
    self.assertIn("openGptChatThread", self.html)
    self.assertIn("gpt_chat", self.html)
```

- [ ] **Step 2: 实现**

在 `_renderHistoryItemHtml`：若 `item.mode === 'gpt_chat' && item.thread_id`，把 `continueBtn` 换成：

```javascript
'<button type="button" class="history-continue-btn" onclick="event.stopPropagation(); openGptChatThread(\'' +
  _escapeHtml(item.thread_id) + '\')">💬 打开对话</button>'
```

`openGptChatThread(threadId)`：

```javascript
async function openGptChatThread(threadId) {
    switchTab('gpt', document.querySelector('.tab-btn[data-tab="gpt"]'));
    var project = getSelectedProjectName();
    var res = await authFetch('/api/gpt-chat/threads/' + encodeURIComponent(threadId) +
        '?project=' + encodeURIComponent(project));
    if (!res.ok) { alert('无法打开对话'); return; }
    var data = await res.json();
    gptChatThreadId = data.thread.id;
    renderGptChatThread(data.thread);
    // 恢复可选控件
    if (data.thread.size) document.getElementById('gptChatRatioSelect').value = data.thread.size;
    if (data.thread.quality) document.getElementById('gptChatQualitySelect').value = data.thread.quality;
}
```

前端 history mode 回退文案分支增加 `gpt_chat` → `💬GPT对话`。

- [ ] **Step 3: 跑测 + Commit**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest tests.test_gpt_chat_ui -v
git add backend/templates/index.html backend/tests/test_gpt_chat_ui.py
git commit -m "$(cat <<'EOF'
feat: 历史记录打开 GPT 对话续聊

EOF
)"
```

---

### Task 8: gitignore、回归与手工验收

**Files:**
- Modify: `.gitignore`（若需要忽略 `gpt_chat_threads.json`）
- Verify: 全量相关单测

- [ ] **Step 1: 忽略运行时文件**

若根 `.gitignore` 未包含，追加：

```
gpt_chat_threads.json
```

- [ ] **Step 2: 跑全部 GPT 聊天相关测试 + 既有队列冒烟**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python3 -m unittest \
  tests.test_gpt_chat_store \
  tests.test_gpt_chat_history \
  tests.test_gpt_chat_job_hook \
  tests.test_gpt_chat_api \
  tests.test_gpt_chat_ui \
  tests.test_gpt_slot \
  -v
```

Expected: 全部 PASS

- [ ] **Step 3: 手工验收清单**

1. 解锁小灯塔 → 点「GPT」→ 只输入文案发送 → 出图。
2. 不选比例/画质 → 默认 1:1 medium。
3. 上传参考图改图 → 成功。
4. 纯文字续聊 → 基于上一张。
5. 生成中再发送 → 被拒或按钮禁用。
6. 打开「生图/修图记录」→ 见 💬GPT对话 → 打开对话可续聊。
7. 「生图」「修图」原流程无回归。

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "$(cat <<'EOF'
chore: 忽略 GPT 聊天线程落盘文件

EOF
)"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 第三主导航 GPT | 5 |
| 单输入框 + 可选附图 | 5–6 |
| 可选比例/画质，默认 1024² + medium | 4–6 |
| 真多轮，无附图用上一张 | 1, 4, 6 |
| 固定 GPT Image | 4 |
| 服务端持久化 | 1 |
| History 摘要 + 续聊 | 2, 3, 7 |
| 项目门禁 / Key | 4 |
| 复用 gpt_queue | 3–4 |
| 线程内单 pending | 1, 4 |
| 非目标（无 Lovart/Logo/蒙版/推断尺寸） | 5 静态断言 + API 不实现 |

---

## 执行备注

- `index.html` 很大：改动保持局部，搜索锚点插入，避免无关格式化。
- HTTP 测试使用 `app.Handler`（`BaseHTTPRequestHandler` 子类）。
- 用户附图 basename 写入 user `image_urls` 仅供回放；真正推理路径以 `UPLOAD_DIR` 绝对路径进 payload。
