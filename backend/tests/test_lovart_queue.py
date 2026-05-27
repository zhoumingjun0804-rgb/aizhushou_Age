"""Lovart 队列单元测试。"""
import threading
import time
import unittest

from lovart_queue import (
    DuplicateHighJobError,
    LovartQueue,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    QueueFullError,
)


class LovartQueuePriorityTests(unittest.TestCase):
    def test_heap_pops_high_before_low(self):
        import heapq
        from lovart_queue import _HeapItem, _PRIORITY_RANK

        q = LovartQueue(max_workers=0, queue_max=10, job_ttl=60, eta_avg_seconds=1)
        with q._heap_lock:
            heapq.heappush(
                q._heap,
                _HeapItem((_PRIORITY_RANK[PRIORITY_LOW], 1), 1, "a", PRIORITY_LOW, lambda: None, "low"),
            )
            heapq.heappush(
                q._heap,
                _HeapItem((_PRIORITY_RANK[PRIORITY_HIGH], 2), 2, "b", PRIORITY_HIGH, lambda: None, "high"),
            )
            first = heapq.heappop(q._heap)
        self.assertEqual(first.priority, PRIORITY_HIGH)


class LovartQueueGenerationTests(unittest.TestCase):
    def test_duplicate_high_raises(self):
        q = LovartQueue(max_workers=1, queue_max=10, job_ttl=60, eta_avg_seconds=1)
        blocker = threading.Event()

        def slow(_job):
            blocker.wait(timeout=2)

        payload = {"client_id": "c1", "kind": "variants", "count": 1}
        j1 = q.submit_generation(payload, runner=slow)
        with self.assertRaises(DuplicateHighJobError) as ctx:
            q.submit_generation(payload, runner=lambda _j: None)
        self.assertEqual(ctx.exception.job_id, j1)
        blocker.set()
        time.sleep(0.2)

    def test_queue_full(self):
        q = LovartQueue(max_workers=1, queue_max=1, job_ttl=60, eta_avg_seconds=1)
        hold = threading.Event()
        started = threading.Event()

        def block():
            started.set()
            hold.wait(timeout=5)

        t = threading.Thread(
            target=lambda: q.run_sync(PRIORITY_LOW, block, label="block"),
            daemon=True,
        )
        t.start()
        started.wait(timeout=2)
        with self.assertRaises(QueueFullError):
            q._enqueue(PRIORITY_LOW, lambda: None, job_id="x", label="x")
        hold.set()
        t.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
