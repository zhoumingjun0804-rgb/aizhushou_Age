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


def owning_queue(
    job_id: str,
    lovart_queue: LovartQueue,
    gpt_queue: LovartQueue,
    backend: str = "",
) -> LovartQueue:
    """返回持有 job 的队列，避免热加载后写入新队列。"""
    with lovart_queue._jobs_lock:
        if job_id in lovart_queue._jobs:
            return lovart_queue
    with gpt_queue._jobs_lock:
        if job_id in gpt_queue._jobs:
            return gpt_queue
    return queue_for_backend(backend, lovart_queue, gpt_queue)


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
    by_id: dict[str, dict[str, Any]] = {}
    for j in merged:
        by_id[j["job_id"]] = j
    out = list(by_id.values())
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out
