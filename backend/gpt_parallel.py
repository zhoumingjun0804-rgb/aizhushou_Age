"""GPT 生图 job 内多张并行（共享全站槽位）。"""
from __future__ import annotations

import concurrent.futures
from typing import Callable, Optional


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
