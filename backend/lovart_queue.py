"""内存 Lovart 任务队列（优先级 + 限并发 worker）。"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

PRIORITY_HIGH = "high"
PRIORITY_LOW = "low"
_PRIORITY_RANK = {PRIORITY_HIGH: 0, PRIORITY_LOW: 1}


class QueueFullError(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"队列已满（最多 {limit} 个），请稍后再试")


class DuplicateHighJobError(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"已有进行中的生图任务: {job_id}")


@dataclass(order=True)
class _HeapItem:
    sort_key: tuple[int, int]
    seq: int = field(compare=False)
    job_id: str = field(compare=False)
    priority: str = field(compare=False)
    fn: Callable[[], None] = field(compare=False)
    label: str = field(compare=False, default="")


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
        self.job_ttl = max(60, job_ttl)
        self.job_max_seconds = max(60, job_max_seconds)
        self.eta_avg_seconds = max(10, eta_avg_seconds)
        self._seq_counter = itertools.count()
        self._heap: list[_HeapItem] = []
        self._heap_lock = threading.Lock()
        self._cond = threading.Condition(self._heap_lock)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._in_flight = 0
        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"lovart-worker-{i}",
                daemon=True,
            )
            t.start()

    def _next_seq(self) -> int:
        return next(self._seq_counter)

    def _count_active_locked(self) -> int:
        return len(self._heap) + self._in_flight

    def _queue_depth(self) -> int:
        with self._jobs_lock:
            active = sum(
                1
                for j in self._jobs.values()
                if j.get("status") in ("queued", "running")
            )
        return active

    def stats(self) -> dict[str, int]:
        """公开队列计数：queued / running / active。"""
        with self._jobs_lock:
            queued = sum(1 for j in self._jobs.values() if j.get("status") == "queued")
            running = sum(1 for j in self._jobs.values() if j.get("status") == "running")
        return {
            "queued": queued,
            "running": running,
            "active": queued + running,
            "max_workers": self.max_workers,
        }
    def _position_for_job_locked(self, job_id: str) -> int:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return 0
            if job.get("status") == "running":
                return 0
        ordered = sorted(self._heap, key=lambda x: x.sort_key)
        ids = [x.job_id for x in ordered]
        if job_id in ids:
            return ids.index(job_id) + self._in_flight
        return 0

    def _enqueue(
        self,
        priority: str,
        fn: Callable[[], None],
        *,
        job_id: str,
        label: str = "",
    ) -> int:
        with self._heap_lock:
            if self._count_active_locked() >= self.queue_max:
                raise QueueFullError(self.queue_max)
            seq = self._next_seq()
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
            position = self._position_for_job_locked(job_id)
            print(
                f"[Queue] enqueued job={job_id} priority={priority} "
                f"position={position} label={label or '-'}"
            )
            self._cond.notify()
            return position

    def _worker_loop(self) -> None:
        while True:
            with self._heap_lock:
                while not self._heap:
                    self._cond.wait()
                item = heapq.heappop(self._heap)
            self._run_item(item)

    def _run_item(self, item: _HeapItem) -> None:
        with self._heap_lock:
            self._in_flight += 1
        print(f"[Queue] started job={item.job_id} worker label={item.label or '-'}")
        try:
            item.fn()
        except Exception as e:
            print(f"[Queue] task error job={item.job_id}: {e}")
        finally:
            with self._heap_lock:
                self._in_flight -= 1

    def run_sync(
        self,
        priority: str,
        fn: Callable[[], Any],
        *,
        label: str = "",
    ) -> Any:
        """入队并阻塞直到 fn 完成（用于 Lovart 兜底 / 阔图等）。"""
        done = threading.Event()
        box: dict[str, Any] = {}

        def wrapper() -> None:
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                done.set()

        job_id = uuid.uuid4().hex[:12]
        self._enqueue(priority, wrapper, job_id=job_id, label=label)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")

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

    def submit_generation(
        self,
        payload: dict[str, Any],
        runner: Callable[[dict[str, Any]], None],
    ) -> str:
        client_id = (payload.get("client_id") or "").strip()
        if not client_id:
            raise ValueError("缺少 client_id")

        existing = self.has_active_high_job(client_id)
        if existing:
            raise DuplicateHighJobError(existing)

        job_id = uuid.uuid4().hex[:12]
        total = int(payload.get("count") or 1)
        now = time.time()
        job: dict[str, Any] = {
            "job_id": job_id,
            "client_id": client_id,
            "kind": payload.get("kind", "variants"),
            "status": "queued",
            "priority": PRIORITY_HIGH,
            "position": 0,
            "progress": {"current": 0, "total": total},
            "eta_seconds": None,
            "variants": None,
            "error": None,
            "created_at": now,
            "payload": payload,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job

        def work() -> None:
            self._execute_generation_job(job_id, runner)

        try:
            position = self._enqueue(
                PRIORITY_HIGH, work, job_id=job_id, label=job["kind"]
            )
        except QueueFullError:
            with self._jobs_lock:
                self._jobs.pop(job_id, None)
            raise

        with self._jobs_lock:
            self._jobs[job_id]["position"] = position
        return job_id

    def set_progress(self, job_id: str, current: int, total: Optional[int] = None) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            prog = job.setdefault("progress", {"current": 0, "total": 1})
            prog["current"] = current
            if total is not None:
                prog["total"] = total
        print(f"[Queue] progress job={job_id} {current}/{prog.get('total', '?')}")

    def set_variants(self, job_id: str, variants: list[dict]) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job:
                job["variants"] = variants

    def fail_job(self, job_id: str, error: str) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = error
            job["finished_at"] = time.time()
        print(f"[Queue] failed job={job_id} error={error}")

    def _execute_generation_job(
        self,
        job_id: str,
        runner: Callable[[dict[str, Any]], None],
    ) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "running"
            job["position"] = 0
            job["started_at"] = time.time()

        started = time.time()
        try:
            runner(job)
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if not job:
                    return
                if job.get("status") == "running":
                    job["status"] = "done"
                    job["finished_at"] = time.time()
            duration = int(time.time() - started)
            print(f"[Queue] done job={job_id} duration={duration}s")
        except Exception as e:
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if job and job.get("status") != "failed":
                    job["status"] = "failed"
                    job["error"] = str(e)
                    job["finished_at"] = time.time()
            print(f"[Queue] failed job={job_id} error={e}")
        finally:
            self._purge_expired()

    def check_job_timeout(self, job_id: str) -> bool:
        """若超时则标记 failed，返回是否已超时。"""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") != "running":
                return False
            started = job.get("started_at") or 0
        if not started:
            return False
        if time.time() - started > self.job_max_seconds:
            self.fail_job(job_id, f"任务超时（超过 {self.job_max_seconds} 秒）")
            return True
        return False

    def _purge_expired(self) -> None:
        now = time.time()
        with self._jobs_lock:
            expired = [
                jid
                for jid, j in self._jobs.items()
                if j.get("status") in ("done", "failed")
                and now - j.get("finished_at", now) > self.job_ttl
            ]
            for jid in expired:
                self._jobs.pop(jid, None)

    def _public_view(self, job: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not job:
            return None
        job_id = job["job_id"]
        status = job.get("status", "queued")
        position = 0
        if status == "queued":
            position = self._position_for_job_locked(job_id)
        eta = None
        if status == "queued" and position > 0:
            eta = position * self.eta_avg_seconds
        elif status == "running":
            prog = job.get("progress") or {}
            remaining = max(0, int(prog.get("total", 1)) - int(prog.get("current", 0)))
            eta = remaining * self.eta_avg_seconds

        return {
            "job_id": job_id,
            "client_id": job.get("client_id"),
            "kind": job.get("kind"),
            "status": status,
            "priority": job.get("priority"),
            "position": position,
            "progress": job.get("progress"),
            "eta_seconds": eta,
            "variants": job.get("variants"),
            "size_notice": job.get("size_notice"),
            "error": job.get("error"),
            "queue_depth": self._queue_depth(),
            "created_at": job.get("created_at"),
        }

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job:
            self.check_job_timeout(job_id)
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        return self._public_view(job)

    def list_jobs(self, client_id: str) -> list[dict[str, Any]]:
        now = time.time()
        with self._jobs_lock:
            items = list(self._jobs.values())
        out: list[dict[str, Any]] = []
        for j in items:
            if j.get("client_id") != client_id:
                continue
            if j.get("status") in ("queued", "running"):
                view = self._public_view(j)
                if view:
                    out.append(view)
            elif now - j.get("finished_at", 0) < self.job_ttl:
                view = self._public_view(j)
                if view:
                    out.append(view)
        out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return out
