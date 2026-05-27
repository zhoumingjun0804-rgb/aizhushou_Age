# Lovart 生图统一排队 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在测试环境多人共用时，通过内存优先级队列限制 Lovart 并发、主生图异步可轮询，并展示排队位置。

**Architecture:** 新建 `backend/lovart_queue.py` 管理 `PriorityQueue` + N 个 worker（`LOVART_MAX_CONCURRENCY`）。生图类任务登记为 `GenerationJob` 供 HTTP 查询；其它 Lovart 调用通过 `run_sync(priority, fn)` 入队后阻塞等待。移除 `LOVART_GENERATION_LOCK`。非 Lovart 后端不入队。

**Tech Stack:** Python 3.10+、`threading`/`queue.PriorityQueue`、现有 `http.server` 处理器、`index.html` 原生 JS、可选 `pytest`（单元测试）。

**Spec:** `docs/superpowers/specs/2026-05-27-lovart-generation-queue-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/lovart_queue.py` | Create | 队列、worker、job 注册表、position/eta |
| `backend/tests/test_lovart_queue.py` | Create | 队列语义单元测试（mock worker fn） |
| `backend/app.py` | Modify | 配置加载、路由、`run_generation_job`、去掉 lock、`run_sync` 接入 |
| `backend/templates/index.html` | Modify | `client_id`、异步提交、轮询、任务列表 UI |
| `.env.example` | Modify | 新环境变量 |
| `README.md`, `AGENTS.md`, `ENVIRONMENT.md` | Modify | API 与多人使用说明 |

---

### Task 1: `lovart_queue` 核心（优先级 + worker）

**Files:**
- Create: `backend/lovart_queue.py`
- Create: `backend/tests/__init__.py`（空文件）
- Create: `backend/tests/test_lovart_queue.py`

- [ ] **Step 1: 写失败测试（优先级 FIFO）**

```python
# backend/tests/test_lovart_queue.py
import time
import unittest
from unittest.mock import patch

from lovart_queue import LovartQueue, PRIORITY_HIGH, PRIORITY_LOW


class LovartQueuePriorityTests(unittest.TestCase):
    def test_high_runs_before_low(self):
        order = []
        q = LovartQueue(max_workers=1, queue_max=10, job_ttl=60, eta_avg_seconds=1)

        def work(label):
            order.append(label)

        q.run_sync(PRIORITY_LOW, lambda: work("low"), label="low")
        # 先 low 入队但未执行完时插入 high — 用 submit 异步测顺序
        q = LovartQueue(max_workers=1, queue_max=10, job_ttl=60, eta_avg_seconds=1)
        order.clear()
        ev_low = __import__("threading").Event()
        ev_high = __import__("threading").Event()

        def slow_low():
            order.append("low_start")
            ev_low.wait(timeout=2)

        def fast_high():
            order.append("high")

        t1 = __import__("threading").Thread(
            target=lambda: q.run_sync(PRIORITY_LOW, slow_low, label="low")
        )
        t1.start()
        time.sleep(0.05)
        q.run_sync(PRIORITY_HIGH, fast_high, label="high")
        ev_low.set()
        t1.join(timeout=3)
        self.assertEqual(order[0], "high")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m pytest tests/test_lovart_queue.py::LovartQueuePriorityTests::test_high_runs_before_low -v
```

Expected: `ModuleNotFoundError: lovart_queue` 或 `ImportError`

- [ ] **Step 3: 实现 `lovart_queue.py` 骨架**

```python
# backend/lovart_queue.py
"""内存 Lovart 任务队列（优先级 + 限并发 worker）。"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional

PRIORITY_HIGH = "high"
PRIORITY_LOW = "low"
_PRIORITY_RANK = {PRIORITY_HIGH: 0, PRIORITY_LOW: 1}


class QueueFullError(Exception):
    pass


class DuplicateHighJobError(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"client already has active high-priority job: {job_id}")


@dataclass
class _HeapItem:
    sort_key: tuple
    seq: int
    job_id: str
    priority: str
    fn: Callable[[], None]
    label: str = ""


class LovartQueue:
    def __init__(
        self,
        max_workers: int = 1,
        queue_max: int = 20,
        job_ttl: int = 3600,
        job_max_seconds: int = 1800,
        eta_avg_seconds: int = 90,
    ):
        self.max_workers = max(1, max_workers)
        self.queue_max = max(1, queue_max)
        self.job_ttl = job_ttl
        self.job_max_seconds = job_max_seconds
        self.eta_avg_seconds = eta_avg_seconds
        self._seq = itertools.count()
        self._heap: list[_HeapItem] = []
        self._heap_lock = threading.Lock()
        self._cond = threading.Condition(self._heap_lock)
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._active_workers = 0
        self._running_high_by_client: dict[str, str] = {}
        for i in range(self.max_workers):
            threading.Thread(target=self._worker_loop, name=f"lovart-worker-{i}", daemon=True).start()

    def _enqueue(self, priority: str, fn: Callable[[], None], *, job_id: str, label: str = "") -> int:
        with self._heap_lock:
            if self._count_active() >= self.queue_max:
                raise QueueFullError(self.queue_max)
            seq = next(self._seq)
            heapq.heappush(
                self._heap,
                _HeapItem(
                    sort_key=(_PRIORITY_RANK[priority], seq),
                    seq=seq,
                    job_id=job_id,
                    priority=priority,
                    fn=fn,
                    label=label,
                ),
            )
            self._cond.notify()
            return self._position_locked(job_id)

    def _count_active(self) -> int:
        queued = len(self._heap)
        with self._jobs_lock:
            running = sum(1 for j in self._jobs.values() if j.get("status") == "running")
        return queued + running

    def _position_locked(self, job_id: str) -> int:
        # 0 = running self; 1+ = ahead in heap
        ordered = sorted(self._heap, key=lambda x: x.sort_key)
        ids = [x.job_id for x in ordered]
        if job_id in ids:
            return ids.index(job_id) + self._active_workers
        return 0

    def _worker_loop(self) -> None:
        while True:
            with self._heap_lock:
                while not self._heap:
                    self._cond.wait()
                item = heapq.heappop(self._heap)
            self._run_item(item)

    def _run_item(self, item: _HeapItem) -> None:
        self._active_workers += 1
        try:
            item.fn()
        finally:
            self._active_workers -= 1

    def run_sync(self, priority: str, fn: Callable[[], Any], *, label: str = "") -> Any:
        """低优 Lovart 片段：入队并阻塞直到 fn 完成。"""
        done = threading.Event()
        box: dict[str, Any] = {}

        def wrapper():
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                done.set()

        jid = uuid.uuid4().hex[:12]
        self._enqueue(priority, wrapper, job_id=jid, label=label)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")
```

（Step 3 先提交最小可 import 版本；Task 2 补全 `GenerationJob` 与 `submit_generation`。）

- [ ] **Step 4: 再跑测试** — 调整测试与实现对齐后 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/lovart_queue.py backend/tests/
git commit -m "feat: add Lovart priority queue module"
```

---

### Task 2: `GenerationJob` 注册、position、409/503

**Files:**
- Modify: `backend/lovart_queue.py`
- Modify: `backend/tests/test_lovart_queue.py`

- [ ] **Step 1: 写失败测试（409 重复 high）**

```python
class LovartQueueGenerationTests(unittest.TestCase):
    def test_duplicate_high_raises(self):
        q = LovartQueue(max_workers=1, queue_max=10, job_ttl=60, eta_avg_seconds=1)
        blocker = threading.Event()

        def slow():
            blocker.wait(timeout=2)

        payload = {"client_id": "c1", "kind": "variants", "count": 1}
        j1 = q.submit_generation(payload, runner=lambda job: slow())
        with self.assertRaises(DuplicateHighJobError) as ctx:
            q.submit_generation(payload, runner=lambda job: None)
        self.assertEqual(ctx.exception.job_id, j1)
        blocker.set()
```

- [ ] **Step 2: 运行测试 — 预期 FAIL**

```bash
cd backend && python -m pytest tests/test_lovart_queue.py::LovartQueueGenerationTests -v
```

- [ ] **Step 3: 在 `LovartQueue` 增加方法**

```python
def submit_generation(self, payload: dict, runner: Callable[[dict], None]) -> str:
    client_id = (payload.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("client_id required")
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    job = {
        "job_id": job_id,
        "client_id": client_id,
        "kind": payload.get("kind", "variants"),
        "status": "queued",
        "priority": PRIORITY_HIGH,
        "position": 0,
        "progress": {"current": 0, "total": int(payload.get("count") or 3)},
        "eta_seconds": None,
        "variants": None,
        "error": None,
        "created_at": now,
        "payload": payload,
    }
    with self._jobs_lock:
        for j in self._jobs.values():
            if j["client_id"] == client_id and j["priority"] == PRIORITY_HIGH and j["status"] in ("queued", "running"):
                raise DuplicateHighJobError(j["job_id"])
        self._jobs[job_id] = job

    def fn():
        self._execute_generation_job(job_id, runner)

    try:
        pos = self._enqueue(PRIORITY_HIGH, fn, job_id=job_id, label=job["kind"])
    except QueueFullError:
        with self._jobs_lock:
            self._jobs.pop(job_id, None)
        raise
    with self._jobs_lock:
        self._jobs[job_id]["position"] = pos
    return job_id

def get_job(self, job_id: str) -> Optional[dict]:
    with self._jobs_lock:
        job = self._jobs.get(job_id)
        return self._public_view(job) if job else None

def list_jobs(self, client_id: str) -> list[dict]:
    now = time.time()
    with self._jobs_lock:
        out = []
        for j in self._jobs.values():
            if j["client_id"] != client_id:
                continue
            if j["status"] in ("queued", "running"):
                out.append(self._public_view(j))
            elif now - j.get("finished_at", 0) < self.job_ttl:
                out.append(self._public_view(j))
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

def _execute_generation_job(self, job_id: str, runner: Callable[[dict], None]) -> None:
    with self._jobs_lock:
        job = self._jobs[job_id]
        job["status"] = "running"
        job["position"] = 0
        job["started_at"] = time.time()
    try:
        runner(job)
        with self._jobs_lock:
            job["status"] = "done"
            job["finished_at"] = time.time()
    except Exception as e:
        with self._jobs_lock:
            job["status"] = "failed"
            job["error"] = str(e)
            job["finished_at"] = time.time()
    finally:
        self._purge_expired()
```

实现 `_public_view`：计算 `queue_depth`、`eta_seconds`（`eta_avg * position`）、`position`（queued 时按 heap 重算）。

- [ ] **Step 4: pytest 全部 PASS**

```bash
cd backend && python -m pytest tests/test_lovart_queue.py -v
```

- [ ] **Step 5: Commit** — `feat: generation job registry and duplicate-high guard`

---

### Task 3: `app.py` 配置与全局队列单例

**Files:**
- Modify: `backend/app.py`（约 112–170 行 `_reload_runtime_env` 与模块级变量）

- [ ] **Step 1: 在 `_reload_runtime_env` 增加变量**

```python
global LOVART_QUEUE_MAX, LOVART_JOB_TTL, LOVART_JOB_MAX_SECONDS, LOVART_ETA_AVG_SECONDS, lovart_queue
LOVART_QUEUE_MAX = max(1, int(os.environ.get("LOVART_QUEUE_MAX", "20")))
LOVART_JOB_TTL = max(60, int(os.environ.get("LOVART_JOB_TTL", "3600")))
LOVART_JOB_MAX_SECONDS = max(60, int(os.environ.get("LOVART_JOB_MAX_SECONDS", "1800")))
LOVART_ETA_AVG_SECONDS = max(10, int(os.environ.get("LOVART_ETA_AVG_SECONDS", "90")))
```

- [ ] **Step 2: 模块末尾初始化（或 reload 时重建）**

```python
from lovart_queue import LovartQueue

def _make_lovart_queue():
    return LovartQueue(
        max_workers=LOVART_MAX_CONCURRENCY,
        queue_max=LOVART_QUEUE_MAX,
        job_ttl=LOVART_JOB_TTL,
        job_max_seconds=LOVART_JOB_MAX_SECONDS,
        eta_avg_seconds=LOVART_ETA_AVG_SECONDS,
    )

lovart_queue = _make_lovart_queue()
```

`_reload_runtime_env` 末尾：`lovart_queue = _make_lovart_queue()`（测试机热更新 `.env` 时生效）。

- [ ] **Step 3: 删除 `LOVART_GENERATION_LOCK` 及 `call_lovart` 内 `with LOVART_GENERATION_LOCK:`**

```python
# call_lovart 内 — 删除 with 块，直接 try/client.generate_image
for attempt in range(LOVART_TASK_RETRY):
    try:
        image_url, error = client.generate_image(...)
```

- [ ] **Step 4: 手动验证服务仍能启动**

```bash
cd /Users/jenson/eva/aizhushou_Age && ./dev.sh
# 另开终端
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/
```

Expected: `200`

- [ ] **Step 5: Commit** — `feat: wire Lovart queue env and remove generation lock`

---

### Task 4: `run_generation_job` 业务逻辑

**Files:**
- Modify: `backend/app.py`（`generate_variants` 附近新增函数）

- [ ] **Step 1: 提取 `execute_generation_from_payload(job: dict) -> None`**

从 `_handle_generate_variants` / `_handle_generate_with_prompt` 复制逻辑：
- 解析 `payload`（prompt/summary、project、count、ratio、image_paths、mode、image_backend）
- `lovart_project_required_error` → `raise ValueError`
- 非 lovart：`generate_variants(...)` 同步在 runner 内执行（不入队，由 handler 短路，见 Task 5）
- lovart：循环 `count` 次 `call_image_generator`，每轮更新 `job["progress"]["current"]`（通过 `lovart_queue.update_progress(job_id, current)` 或在 job dict 上改，queue 模块提供 `set_progress(job_id, current, total)`）
- `download_image` → `variants` 列表
- `add_history(entry)`
- 写入 `job["variants"]`

- [ ] **Step 2: 整 job 超时**

在 runner 开头记录 `started = time.time()`，每张图前检查 `time.time() - started > LOVART_JOB_MAX_SECONDS` → `raise TimeoutError("任务超时")`

- [ ] **Step 3: Commit** — `feat: extract generation job runner from handlers`

---

### Task 5: HTTP API 路由

**Files:**
- Modify: `backend/app.py`（`do_GET` / `do_POST` / `do_OPTIONS`）

- [ ] **Step 1: OPTIONS 增加 CORS 路径**

```python
'/api/generation/jobs',
```

`Access-Control-Allow-Headers` 增加 `X-Client-Id`。

- [ ] **Step 2: `do_GET` 分支**

```python
elif path.startswith('/api/generation/jobs/'):
    job_id = path.rsplit('/', 1)[-1]
    self._handle_generation_job_get(job_id)
elif path == '/api/generation/jobs':
    params = self._query_params()
    client_id = (params.get("client_id", [""])[0] or "").strip()
    if not client_id:
        self._send_json({"error": "缺少 client_id"}, status=400)
        return
    self._send_json({"jobs": lovart_queue.list_jobs(client_id)})
```

- [ ] **Step 3: `_handle_generation_jobs_post`**

解析 multipart（复用 `parse_multipart`）：
- 必填 `client_id`、`kind`
- `normalize_image_backend` → 若非 `lovart`，**同步**调用 `execute_generation_from_payload` 并直接返回 `{variants}`（与旧 API 兼容，不入队）
- lovart：`lovart_queue.submit_generation(payload, execute_generation_from_payload)` 
- `DuplicateHighJobError` → 409 + `job_id`
- `QueueFullError` → 503
- 成功 → 201 + `{ok, job_id, status, position, status_url}`

- [ ] **Step 4: `_handle_generation_job_get`**

`lovart_queue.get_job(job_id)` → 404 或 JSON

- [ ] **Step 5: `post_routes` 注册**

```python
'/api/generation/jobs': self._handle_generation_jobs_post,
```

- [ ] **Step 6: curl 冒烟（无 Lovart Key 时测 400/错误路径）**

```bash
curl -s -X POST http://127.0.0.1:8000/api/generation/jobs \
  -F 'client_id=test1' -F 'kind=with_prompt' -F 'prompt=hello' -F 'count=1' | jq .
```

- [ ] **Step 7: Commit** — `feat: add /api/generation/jobs endpoints`

---

### Task 6: 低优 Lovart — smart-cutout & layout-extend

**Files:**
- Modify: `backend/app.py`（`run_ai_extract_subject`、`layout-extend` 的 `ai_background_fn`）

- [ ] **Step 1: `call_img2img_with_retry` 在 lovart 后端走队列**

```python
def call_img2img_with_retry(...):
    backend = normalize_image_backend(image_backend)
    def _do():
        return _call_img2img_once(...)  # 现有循环逻辑
    if backend == "lovart":
        return lovart_queue.run_sync(PRIORITY_LOW, _do, label="img2img")
    return _do()
```

从 `lovart_queue` import `PRIORITY_LOW`。

- [ ] **Step 2: 确认 `call_lovart` 仅被 worker 线程调用**

生图 runner、`run_sync` 回调内调用 — 无其它线程直接并发调用。

- [ ] **Step 3: 手动：smart-cutout 走 lovart 时日志含 `[Queue]`**

- [ ] **Step 4: Commit** — `feat: route Lovart img2img through low-priority queue`

---

### Task 7: 前端 `client_id` + 异步生图

**Files:**
- Modify: `backend/templates/index.html`

- [ ] **Step 1: 增加工具函数（`<script>` 靠前位置）**

```javascript
function getClientId() {
    var key = 'aizhushou_client_id';
    var id = localStorage.getItem(key);
    if (!id) {
        id = 'c_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
        localStorage.setItem(key, id);
    }
    return id;
}

function appendClientId(formData) {
    formData.append('client_id', getClientId());
}

var activeGenerationPoll = null;

function updateLoadingMessage(text) {
    var el = document.querySelector('.loading-text');
    if (el) el.textContent = text;
}

async function pollGenerationJob(jobId, onDone) {
    var maxWait = 1800;
    var started = Date.now();
    while (Date.now() - started < maxWait * 1000) {
        var res = await fetch('/api/generation/jobs/' + encodeURIComponent(jobId));
        var data = await res.json();
        if (data.status === 'queued') {
            updateLoadingMessage('排队中，前面还有 ' + (data.position || 0) + ' 人…');
        } else if (data.status === 'running') {
            var p = data.progress || {};
            updateLoadingMessage('生成中 ' + (p.current || 0) + '/' + (p.total || '?') + '…');
        } else if (data.status === 'done') {
            onDone(null, data);
            return;
        } else if (data.status === 'failed') {
            onDone(new Error(data.error || '生成失败'), data);
            return;
        }
        await new Promise(function(r) { setTimeout(r, 2500); });
    }
    onDone(new Error('等待超时，请打开「生图记录」查看任务状态'));
}

async function submitGenerationJob(formData, kind) {
    formData.append('kind', kind);
    appendClientId(formData);
    var res = await fetch('/api/generation/jobs', { method: 'POST', body: formData });
    var data = await res.json();
    if (res.status === 409 && data.job_id) {
        if (confirm('您已有进行中的生图任务，是否查看该任务？')) {
            return { job_id: data.job_id };
        }
        throw new Error(data.error || '已有任务进行中');
    }
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
}
```

- [ ] **Step 2: 改 `generateWithKeyword`**

- `POST /api/generation/jobs`，`kind=with_prompt`
- `submitGenerationJob` → `pollGenerationJob` → `renderVariants(data.variants)`
- 409/503 `alert` 友好文案

- [ ] **Step 3: 改 `generateVariants`**

- `kind=variants`，同样轮询

- [ ] **Step 4: 页面 `load` 时 `refreshGenerationJobs()`**

`GET /api/generation/jobs?client_id=` → 若有 `queued/running`，恢复 `loadingCard` 并 `pollGenerationJob`

- [ ] **Step 5: 浏览器手测** — 提交后见「排队中」文案

- [ ] **Step 6: Commit** — `feat: async generation UI with queue polling`

---

### Task 8: 历史抽屉「进行中」列表

**Files:**
- Modify: `backend/templates/index.html`（`historyDrawer` / `loadHistory` 附近）

- [ ] **Step 1: 在 `#historyList` 上方增加容器**

```html
<div id="generationJobsPanel" style="margin-bottom:16px;"></div>
```

- [ ] **Step 2: `renderGenerationJobsPanel(jobs)`**

显示 `queued`/`running` 任务：kind、position、进度；点击继续轮询。

- [ ] **Step 3: `toggleHistory` / `loadHistory` 时调用 `refreshGenerationJobs()`**

- [ ] **Step 4: Commit** — `feat: show in-progress generation jobs in history drawer`

---

### Task 9: 配置与文档

**Files:**
- Modify: `.env.example`
- Modify: `README.md`, `AGENTS.md`, `ENVIRONMENT.md`

- [ ] **Step 1: `.env.example` 增加 5 个变量**（见 spec）

- [ ] **Step 2: `AGENTS.md` API 表增加三行** `POST/GET /api/generation/jobs`

- [ ] **Step 3: `ENVIRONMENT.md` 增加「多人测试与排队」小节** — 重启丢任务、每人 1 任务、队列满 503

- [ ] **Step 4: Commit** — `docs: Lovart generation queue configuration`

---

### Task 10: 旧端点处理（可选兼容）

**Files:**
- Modify: `backend/app.py` — `_handle_generate_variants` / `_handle_generate_with_prompt`

- [ ] **Step 1: 两 handler 顶部 lovart 时返回 410 或 JSON 提示**

```python
if normalize_image_backend(fields.get("image_backend")) == "lovart":
    self._send_json({
        "error": "请使用新版 /api/generation/jobs（前端已更新）",
        "migration": "/api/generation/jobs",
    }, status=410)
    return
```

或保留：内部构建 payload 调 `submit_generation` + 同步轮询 `get_job` 直到 done（兼容旧脚本）。**推荐 410**，因 Task 7 已改前端。

- [ ] **Step 2: Commit** — `chore: deprecate sync generate endpoints for lovart`

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 统一队列 + `LOVART_MAX_CONCURRENCY` | 1, 3 |
| high/low 优先级 | 1, 6 |
| 全局 `LOVART_QUEUE_MAX` | 2, 5 |
| 每人 1 high → 409 | 2, 5 |
| `POST/GET /api/generation/jobs` | 5 |
| 异步轮询 + 任务列表 | 7, 8 |
| smart-cutout / layout low | 6 |
| 移除 `LOVART_GENERATION_LOCK` | 3 |
| 非 Lovart 不入队 | 5 |
| job 超时 / 单张失败继续 | 4 |
| `.env` + 文档 | 9 |
| 日志 `[Queue]` | 1–2 中 `print` 在 enqueue/start/done |

---

## Manual test script（实现完成后）

1. 两浏览器各生成 `client_id`，同时点生成 → 第二个 `position >= 1`。
2. 同一浏览器连点两次 → 第二次 409。
3. high 生图排队时触发 smart-cutout lovart 兜底 → high 先完成。
4. `./deploy.sh restart` → 进行中任务 `failed` 文案含「重启」。
5. 测试机 `./deploy.sh remote` 后两人并发 — 无 Lovart 并发满错误。

---

## Plan self-review

- No TBD / placeholder steps.
- Types consistent: `job_id` 12 hex、`status` 枚举与 spec 一致。
- `edit` kind：若 `startEditImage` 仍走 `/api/edit-image` 同步，spec 中 `edit` 为 high — **实现时二选一**：(a) edit-image lovart 路径改 `run_sync(HIGH)`；或 (b) 将修图纳入 `kind=edit` 的 generation jobs（Task 7 扩展）。**默认 (a)** 在 Task 6 后对 `_handle_edit_image_impl` 中 `call_img2img_with_retry` 使用 `run_sync(PRIORITY_HIGH, ...)`（修图属 high）。

---

*Plan complete.*
