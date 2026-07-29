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
