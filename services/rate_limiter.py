import threading
import time
from collections import deque


class InMemoryRateLimiter:

    # Cada cuántos segundos se descartan los buckets sin actividad (las claves por
    # sesión son efímeras: sin esta limpieza la memoria crecería sin límite).
    SWEEP_INTERVAL_SECONDS = 60

    def __init__(self):
        self._buckets = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def allow(self, key, limit, window_seconds):
        now = time.monotonic()
        window_start = now - window_seconds

        with self._lock:
            self._sweep(now, window_start)

            bucket = self._buckets.setdefault(key, deque())

            # Keep only entries in current window.
            while bucket and bucket[0] <= window_start:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after

            bucket.append(now)
            return True, 0

    def _sweep(self, now, window_start):
        if now - self._last_sweep < self.SWEEP_INTERVAL_SECONDS:
            return

        self._last_sweep = now

        for key in [k for k, b in self._buckets.items() if not b or b[-1] <= window_start]:
            del self._buckets[key]
