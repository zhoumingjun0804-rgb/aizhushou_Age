# GPT 生图独立排队与并发优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 GPT 主生图从 Lovart 串行队列拆出，独立限并发，并支持单 job 多张变体并行，且不突破全站 GPT API 上限。

**Architecture:** 复用 `LovartQueue` 类双实例（`lovart_queue` / `gpt_queue`）；`app.py` 按 `image_backend` 分流提交，GET 聚合两队列；跨队列检查每人 1 个 high 任务；进程内 `threading.Semaphore(GPT_MAX_CONCURRENCY)` 限制每一次 GPT Image API 调用；GPT job 内用 `ThreadPoolExecutor` 并行变体。

**Tech Stack:** Python 3.10+、`threading` / `concurrent.futures`、现有 `http.server`、`unittest`

**Spec:** `docs/superpowers/specs/2026-07-29-gpt-generation-queue-design.md`

---

## File map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/lovart_queue.py` | Modify | 增加 `has_active_high_job(client_id)`；worker 线程名可配置（可选） |
| `backend/gpt_slot.py` | Create | 全局 GPT API semaphore：`init` / `acquire` / `release` / context manager |
| `backend/tests/test_gpt_slot.py` | Create | semaphore 上限测试 |
| `backend/tests/test_generation_queue_routing.py` | Create | 双队列分流、跨队列 409、list/get 聚合（可测纯函数） |
| `backend/tests/test_gpt_parallel_variants.py` | Create | GPT job 内并行进度 / 槽位占用 |
| `backend/app.py` | Modify | 双队列配置、分流、聚合、execute 并行、slot 包裹 call_gpt |
| `.env.example` | Modify | GPT_* 变量 |
| `ENVIRONMENT.md` / `README.md` | Modify | 文档一行说明 |
| `docs/superpowers/specs/2026-07-29-gpt-generation-queue-design.md` | Modify | 状态改为「已实现」（全部任务完成后） |

---

### Task 1: GPT API 全局槽位（`gpt_slot`）

**Files:**
- Create: `backend/gpt_slot.py`
- Create: `backend/tests/test_gpt_slot.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_gpt_slot.py
import threading
import time
import unittest

import gpt_slot


class GptSlotTests(unittest.TestCase):
    def test_semaphore_limits_concurrent_holders(self):
        gpt_slot.configure(2)
        held = []
        lock = threading.Lock()
        release_gate = threading.Event()

        def worker():
            with gpt_slot.hold():
                with lock:
                    held.append(1)
                release_gate.wait(timeout=2)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.15)
        with lock:
            self.assertEqual(len(held), 2)
        release_gate.set()
        for t in threads:
            t.join(timeout=2)
        with lock:
            self.assertEqual(len(held), 3)
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_gpt_slot -v
```

Expected: `ModuleNotFoundError: No module named 'gpt_slot'`

- [ ] **Step 3: 实现 `gpt_slot.py`**

```python
# backend/gpt_slot.py
"""全站 GPT Image API 并发槽位（进程内 Semaphore）。"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_lock = threading.Lock()
_semaphore: Optional[threading.Semaphore] = None
_limit = 1


def configure(limit: int) -> None:
    """重置槽位上限（启动与热加载 .env 时调用）。"""
    global _semaphore, _limit
    n = max(1, int(limit))
    with _lock:
        _limit = n
        _semaphore = threading.Semaphore(n)


def current_limit() -> int:
    return _limit


@contextmanager
def hold() -> Iterator[None]:
    sem = _semaphore
    if sem is None:
        configure(1)
        sem = _semaphore
    assert sem is not None
    sem.acquire()
    try:
        yield
    finally:
        sem.release()
```

- [ ] **Step 4: 再跑测试确认通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_gpt_slot -v
```

Expected: OK

- [ ] **Step 5: Commit**

```bash
git add backend/gpt_slot.py backend/tests/test_gpt_slot.py
git commit -m "$(cat <<'EOF'
feat: 增加全站 GPT Image API 并发槽位

用进程内 Semaphore 限制同时打到 Azure/官方的 GPT 生图请求数。
EOF
)"
```

---

### Task 2: 队列增加 `has_active_high_job`

**Files:**
- Modify: `backend/lovart_queue.py`
- Modify: `backend/tests/test_lovart_queue.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_lovart_queue.py` 的 `LovartQueueGenerationTests` 中追加：

```python
    def test_has_active_high_job(self):
        q = LovartQueue(max_workers=1, queue_max=10, job_ttl=60, eta_avg_seconds=1)
        blocker = threading.Event()

        def slow(_job):
            blocker.wait(timeout=2)

        self.assertIsNone(q.has_active_high_job("c1"))
        j1 = q.submit_generation({"client_id": "c1", "kind": "variants", "count": 1}, runner=slow)
        self.assertEqual(q.has_active_high_job("c1"), j1)
        self.assertIsNone(q.has_active_high_job("c2"))
        blocker.set()
        time.sleep(0.2)
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_lovart_queue.LovartQueueGenerationTests.test_has_active_high_job -v
```

Expected: `AttributeError: ... has_active_high_job`

- [ ] **Step 3: 在 `LovartQueue` 增加方法**

```python
    def has_active_high_job(self, client_id: str) -> Optional[str]:
        """若该 client 有 queued/running 的 high 任务，返回 job_id，否则 None。"""
        cid = (client_id or "").strip()
        if not cid:
            return None
        with self._jobs_lock:
            for j in self._jobs.values():
                if (
                    j.get("client_id") == cid
                    and j.get("priority") == PRIORITY_HIGH
                    and j.get("status") in ("queued", "running")
                ):
                    return j["job_id"]
        return None
```

（放在 `submit_generation` 附近；`submit_generation` 内现有 duplicate 检查可改为调用本方法，保持行为不变。）

- [ ] **Step 4: 测试通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_lovart_queue -v
```

Expected: OK

- [ ] **Step 5: Commit**

```bash
git add backend/lovart_queue.py backend/tests/test_lovart_queue.py
git commit -m "$(cat <<'EOF'
feat: LovartQueue 支持查询 client 是否已有 high 任务

供双队列跨实例做每人一任务检查。
EOF
)"
```

---

### Task 3: 路由/聚合纯函数 + 测试

**Files:**
- Create: `backend/generation_queues.py`（小模块：选队列、跨队列 duplicate、合并 list）
- Create: `backend/tests/test_generation_queue_routing.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_generation_queue_routing.py
import threading
import time
import unittest

from generation_queues import (
    find_job,
    list_client_jobs,
    queue_for_backend,
    raise_if_duplicate_high,
)
from lovart_queue import DuplicateHighJobError, LovartQueue


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.lovart = LovartQueue(max_workers=1, queue_max=10, job_ttl=60, eta_avg_seconds=1)
        self.gpt = LovartQueue(max_workers=2, queue_max=10, job_ttl=60, eta_avg_seconds=1)

    def test_queue_for_backend(self):
        self.assertIs(queue_for_backend("gpt", self.lovart, self.gpt), self.gpt)
        self.assertIs(queue_for_backend("lovart", self.lovart, self.gpt), self.lovart)
        self.assertIs(queue_for_backend("dreamina", self.lovart, self.gpt), self.lovart)

    def test_cross_queue_duplicate(self):
        blocker = threading.Event()

        def slow(_job):
            blocker.wait(timeout=2)

        j1 = self.lovart.submit_generation(
            {"client_id": "c1", "kind": "variants", "count": 1}, runner=slow
        )
        with self.assertRaises(DuplicateHighJobError) as ctx:
            raise_if_duplicate_high("c1", self.lovart, self.gpt)
        self.assertEqual(ctx.exception.job_id, j1)
        blocker.set()

    def test_find_and_list_merge(self):
        done = threading.Event()

        def quick(job):
            done.set()

        j_l = self.lovart.submit_generation(
            {"client_id": "c1", "kind": "variants", "count": 1}, runner=quick
        )
        done.wait(timeout=2)
        time.sleep(0.05)
        j_g = self.gpt.submit_generation(
            {"client_id": "c1", "kind": "variants", "count": 1},
            runner=lambda _j: None,
        )
        self.assertEqual(find_job(j_l, self.lovart, self.gpt)["job_id"], j_l)
        self.assertEqual(find_job(j_g, self.lovart, self.gpt)["job_id"], j_g)
        self.assertIsNone(find_job("missing", self.lovart, self.gpt))
        ids = [j["job_id"] for j in list_client_jobs("c1", self.lovart, self.gpt)]
        self.assertIn(j_l, ids)
        self.assertIn(j_g, ids)
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_generation_queue_routing -v
```

Expected: `ModuleNotFoundError: generation_queues`

- [ ] **Step 3: 实现 `generation_queues.py`**

```python
# backend/generation_queues.py
"""双队列路由与查询聚合（Lovart / GPT）。"""
from __future__ import annotations

from typing import Any, Optional

from lovart_queue import DuplicateHighJobError, LovartQueue


def queue_for_backend(
    backend: str,
    lovart_queue: LovartQueue,
    gpt_queue: LovartQueue,
) -> LovartQueue:
    if (backend or "").strip().lower() == "gpt":
        return gpt_queue
    return lovart_queue


def raise_if_duplicate_high(
    client_id: str,
    lovart_queue: LovartQueue,
    gpt_queue: LovartQueue,
) -> None:
    for q in (lovart_queue, gpt_queue):
        existing = q.has_active_high_job(client_id)
        if existing:
            raise DuplicateHighJobError(existing)


def find_job(
    job_id: str,
    lovart_queue: LovartQueue,
    gpt_queue: LovartQueue,
) -> Optional[dict[str, Any]]:
    return lovart_queue.get_job(job_id) or gpt_queue.get_job(job_id)


def list_client_jobs(
    client_id: str,
    lovart_queue: LovartQueue,
    gpt_queue: LovartQueue,
) -> list[dict[str, Any]]:
    merged = lovart_queue.list_jobs(client_id) + gpt_queue.list_jobs(client_id)
    # 同一 job 不应出现两次；按 created_at 降序
    by_id: dict[str, dict[str, Any]] = {}
    for j in merged:
        by_id[j["job_id"]] = j
    out = list(by_id.values())
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out
```

- [ ] **Step 4: 测试通过**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_generation_queue_routing -v
```

Expected: OK

- [ ] **Step 5: Commit**

```bash
git add backend/generation_queues.py backend/tests/test_generation_queue_routing.py
git commit -m "$(cat <<'EOF'
feat: 生图双队列路由与查询聚合

按 backend 选队列，跨队列检查每人一任务，合并 list/get。
EOF
)"
```

---

### Task 4: `app.py` 双队列配置与 HTTP 分流

**Files:**
- Modify: `backend/app.py`（配置区、`_reload_runtime_env`、`_handle_generation_jobs_post`、GET handlers）

- [ ] **Step 1: 增加 GPT 队列配置与工厂**

在现有 Lovart 队列配置旁增加（默认与规格一致）：

```python
GPT_MAX_CONCURRENCY = max(1, int(os.environ.get("GPT_MAX_CONCURRENCY", "4")))
GPT_QUEUE_MAX = max(1, int(os.environ.get("GPT_QUEUE_MAX", "20")))
GPT_ETA_AVG_SECONDS = max(10, int(os.environ.get("GPT_ETA_AVG_SECONDS", "45")))
GPT_VARIANT_PARALLEL = max(1, int(os.environ.get("GPT_VARIANT_PARALLEL", "4")))
```

```python
def _make_lovart_queue() -> LovartQueue:
    return LovartQueue(
        max_workers=LOVART_MAX_CONCURRENCY,
        queue_max=LOVART_QUEUE_MAX,
        job_ttl=LOVART_JOB_TTL,
        job_max_seconds=LOVART_JOB_MAX_SECONDS,
        eta_avg_seconds=LOVART_ETA_AVG_SECONDS,
    )


def _make_gpt_queue() -> LovartQueue:
    return LovartQueue(
        max_workers=GPT_MAX_CONCURRENCY,
        queue_max=GPT_QUEUE_MAX,
        job_ttl=LOVART_JOB_TTL,
        job_max_seconds=LOVART_JOB_MAX_SECONDS,
        eta_avg_seconds=GPT_ETA_AVG_SECONDS,
    )


lovart_queue = _make_lovart_queue()
gpt_queue = _make_gpt_queue()

import gpt_slot
gpt_slot.configure(GPT_MAX_CONCURRENCY)
```

在 `_reload_runtime_env` 中同步刷新上述全局变量，并：

```python
    global gpt_queue, GPT_MAX_CONCURRENCY, GPT_QUEUE_MAX, GPT_ETA_AVG_SECONDS, GPT_VARIANT_PARALLEL
    # ... 重新读 env ...
    lovart_queue = _make_lovart_queue()
    gpt_queue = _make_gpt_queue()
    gpt_slot.configure(GPT_MAX_CONCURRENCY)
```

（与现网一致：热加载会重建队列；接受进行中任务丢失。）

Import：

```python
from generation_queues import (
    find_job,
    list_client_jobs,
    queue_for_backend,
    raise_if_duplicate_high,
)
```

- [ ] **Step 2: 改 POST / GET**

`_handle_generation_jobs_post` 在 `build_generation_payload` 之后：

```python
            backend = normalize_image_backend(payload.get("image_backend"))
            raise_if_duplicate_high(client_id, lovart_queue, gpt_queue)
            target_q = queue_for_backend(backend, lovart_queue, gpt_queue)
            # 目标队列自身 submit_generation 仍会检查本队列 duplicate（幂等）
            job_id = target_q.submit_generation(payload, execute_generation_job)
            view = target_q.get_job(job_id)
```

注意：`submit_generation` 内部仍有本队列 duplicate 检查；跨队列靠 `raise_if_duplicate_high` 先拦。为避免 race，保持先 `raise_if_duplicate_high` 再 submit。

GET 单任务：

```python
        view = find_job(job_id, lovart_queue, gpt_queue)
```

GET 列表：

```python
            self._send_json({"jobs": list_client_jobs(client_id, lovart_queue, gpt_queue)})
```

- [ ] **Step 3: 手工冒烟（可选）**

启动 `./dev.sh`，分别用 GPT / Lovart 各提交一单，确认另一 backend 不受堵（日志里两队列 worker 可同时 started）。

- [ ] **Step 4: Commit**

```bash
git add backend/app.py
git commit -m "$(cat <<'EOF'
feat: GPT 与 Lovart 生图分队列提交与查询聚合

主生图按 backend 入对应内存队列，跨队列限制每人一任务。
EOF
)"
```

---

### Task 5: `call_gpt` 走槽位 + GPT job 内并行

**Files:**
- Modify: `backend/app.py`（`call_gpt`、`execute_generation_job`）
- Create: `backend/tests/test_gpt_parallel_variants.py`

- [ ] **Step 1: 写并行进度测试（mock `call_image_generator`）**

将可测逻辑抽成 `run_gpt_variants_parallel(...)` 放在 `app.py`（或 `gpt_parallel.py`），避免测整个 HTTP。推荐新建小函数文件以保持 `app.py` 可测性：

Create: `backend/gpt_parallel.py`

```python
# backend/gpt_parallel.py
"""GPT 生图 job 内多张并行（共享全站槽位）。"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Callable, Optional


def run_variants_parallel(
    count: int,
    parallel: int,
    worker_fn: Callable[[int], dict],
    *,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """并行执行 count 次 worker_fn(index)，按完成数回调进度；返回按 index 排序的结果。"""
    total = max(1, int(count))
    pool_size = max(1, min(int(parallel), total))
    results: list[Optional[dict]] = [None] * total
    done_count = 0

    def _wrap(idx: int) -> tuple[int, dict]:
        if should_abort and should_abort():
            return idx, {"filename": None, "error": "任务已取消或超时"}
        return idx, worker_fn(idx)

    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as ex:
        futs = [ex.submit(_wrap, i) for i in range(total)]
        for fut in concurrent.futures.as_completed(futs):
            idx, item = fut.result()
            results[idx] = item
            done_count += 1
            if on_progress:
                on_progress(done_count, total)
            if should_abort and should_abort():
                break

    return [r if r is not None else {"filename": None, "error": "未完成"} for r in results]
```

测试：

```python
# backend/tests/test_gpt_parallel_variants.py
import threading
import time
import unittest

from gpt_parallel import run_variants_parallel


class GptParallelTests(unittest.TestCase):
    def test_parallel_faster_than_serial_and_progress(self):
        progress = []
        gate = threading.Barrier(4)

        def work(_idx):
            gate.wait(timeout=2)
            time.sleep(0.05)
            return {"filename": f"f{_idx}", "error": None}

        t0 = time.time()
        out = run_variants_parallel(
            4,
            4,
            work,
            on_progress=lambda c, t: progress.append(c),
        )
        elapsed = time.time() - t0
        self.assertEqual(len(out), 4)
        self.assertEqual(progress[-1], 4)
        self.assertLess(elapsed, 1.0)  # 串行约 0.2*4 + barrier；并行应明显更短
```

- [ ] **Step 2: 实现 `gpt_parallel.py` 并跑测试**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_gpt_parallel_variants -v
```

Expected: OK

- [ ] **Step 3: `call_gpt` 包裹 `gpt_slot.hold()`**

在 `call_gpt` 内真正发起 HTTP 前：

```python
    import gpt_slot
    with gpt_slot.hold():
        # 现有 GptImageClient 调用
        ...
```

保证：主生图并行、扩边、AI 提取等所有走 `call_gpt` / `call_image_generator(..., backend=gpt)` 的路径共享同一槽位。若 `call_gpt` 是唯一 GPT 生图入口则只改这一处即可。

- [ ] **Step 4: 改 `execute_generation_job`**

伪代码：

```python
    if backend == "gpt":
        def one(idx: int) -> dict:
            # 与原循环体相同：call_image_generator → download → logo → variant_entry
            ...
            return variant_dict_or_error

        variants = run_variants_parallel(
            count,
            GPT_VARIANT_PARALLEL,
            one,
            on_progress=lambda cur, tot: lovart_queue.set_progress(job_id, cur, tot)
            # 注意：job 可能在 gpt_queue 上，应使用「执行该 job 的队列」set_progress
        )
    else:
        # 原串行 for 循环，用 lovart_queue.set_progress
```

**重要：** `execute_generation_job` 当前写死 `lovart_queue.set_progress` / `fail_job` / `set_variants`。改为通过 `job` 所属队列操作：

方案：在 `submit_generation` 的 payload 或 job dict 不存队列引用；在 `execute_generation_job` 开头：

```python
    q = queue_for_backend(backend, lovart_queue, gpt_queue)
```

之后全部 `q.set_progress` / `q.fail_job` / `q.set_variants` / `q.check_job_timeout`。

（`_execute_generation_job` 在 runner 返回后会把仍为 running 的标成 done；`execute_generation_job` 里若已自行标 done，保持与现逻辑一致即可。）

- [ ] **Step 5: 跑相关单测**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest tests.test_gpt_slot tests.test_lovart_queue tests.test_generation_queue_routing tests.test_gpt_parallel_variants -v
```

Expected: OK

- [ ] **Step 6: Commit**

```bash
git add backend/gpt_parallel.py backend/tests/test_gpt_parallel_variants.py backend/app.py
git commit -m "$(cat <<'EOF'
feat: GPT 生图走全局槽位且 job 内变体并行

call_gpt 统一 acquire 槽位；多张变体 ThreadPool 并行且不超全站上限。
EOF
)"
```

---

### Task 6: 环境变量与文档

**Files:**
- Modify: `.env.example`
- Modify: `ENVIRONMENT.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-29-gpt-generation-queue-design.md`（状态 → 已实现）

- [ ] **Step 1: `.env.example` 在 Lovart 队列变量后追加**

```bash
# GPT 生图队列（与 Lovart 分池；MAX_CONCURRENCY 同时是全站 GPT API 上限）
GPT_MAX_CONCURRENCY=4
GPT_QUEUE_MAX=20
GPT_ETA_AVG_SECONDS=45
GPT_VARIANT_PARALLEL=4
```

- [ ] **Step 2: `README.md` 配置表增加一行**

```markdown
| `GPT_MAX_CONCURRENCY` / `GPT_QUEUE_MAX` | GPT 生图 worker/API 并发（默认 4）与排队上限（默认 20） |
```

- [ ] **Step 3: `ENVIRONMENT.md` 在 Lovart 排队段落后补一段**

说明：GPT 走独立内存队列；`GPT_MAX_CONCURRENCY` 默认 4；与 Lovart 互不占用 worker；`GPT_VARIANT_PARALLEL` 控制单任务内并行张数；热加载/PM2 reload 仍会清空两队列。

- [ ] **Step 4: 规格状态改为「已实现」**

- [ ] **Step 5: Commit**

```bash
git add .env.example ENVIRONMENT.md README.md docs/superpowers/specs/2026-07-29-gpt-generation-queue-design.md
git commit -m "$(cat <<'EOF'
docs: 补充 GPT 生图队列环境变量说明

同步 .env.example / README / ENVIRONMENT，规格标为已实现。
EOF
)"
```

---

### Task 7: 回归与手测清单

- [ ] **Step 1: 全量相关单测**

```bash
cd /Users/jenson/eva/aizhushou_Age/backend && python -m unittest discover -s tests -v
```

Expected: 全绿（若有无关失败，记录但不在本计划范围修）

- [ ] **Step 2: 手测清单（开发机）**

1. 选 GPT，`count=1`，正常出图。
2. 选 GPT，`count=4`，进度可较快到 4；日志可见多请求重叠。
3. 浏览器 A 提交 Lovart、浏览器 B 提交 GPT，二者可同时 running。
4. 同一浏览器先 GPT 未完成再点一次 → 409。
5. 临时设 `GPT_MAX_CONCURRENCY=1`，多用户 GPT 可见排队 position。
6. Lovart 仍默认串行，不因 GPT 配置变大。

- [ ] **Step 3: 若手测通过且无额外代码改动，无需再 commit；有修 bug 则单独 commit**

---

## Spec coverage（自检）

| 规格要求 | 任务 |
|----------|------|
| 双队列 lovart/gpt | Task 3–4 |
| `GPT_MAX_CONCURRENCY` worker=4 | Task 4 |
| 全局 Semaphore | Task 1 + Task 5 `call_gpt` |
| job 内并行 + `GPT_VARIANT_PARALLEL` | Task 5 |
| 跨队列每人 1 high | Task 2–4 |
| GET 聚合 | Task 3–4 |
| Lovart 串行不变 | Task 5 else 分支 |
| 同步扩边/提取不强制入队，但可共享槽位 | Task 5 `call_gpt` hold |
| 文档 / env | Task 6 |
| 测试要点 | Task 1–5、7 |

无 Redis、无按项目组分池、无前端必改 — 符合非目标。
