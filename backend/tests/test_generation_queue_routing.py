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
