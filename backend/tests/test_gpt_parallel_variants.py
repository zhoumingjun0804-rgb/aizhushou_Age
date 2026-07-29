import threading
import time
import unittest

from gpt_parallel import run_variants_parallel


class GptParallelTests(unittest.TestCase):
    def test_parallel_faster_than_serial_and_progress(self):
        progress = []
        gate = threading.Barrier(4)

        def work(idx):
            gate.wait(timeout=2)
            time.sleep(0.05)
            return {"filename": f"f{idx}", "error": None}

        t0 = time.time()
        out = run_variants_parallel(
            4,
            4,
            work,
            on_progress=lambda current, total: progress.append(current),
        )
        elapsed = time.time() - t0

        self.assertEqual(len(out), 4)
        self.assertEqual(progress[-1], 4)
        self.assertLess(elapsed, 1.0)
