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
