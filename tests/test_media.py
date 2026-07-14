import threading
import time
from unittest import TestCase

import tests.arlo
from pyaarlo.media import ArloMediaDownloader
from pyaarlo.util import BandwidthLimiter


class TestBandwidthLimiter(TestCase):
    def test_burst_then_throttle(self):
        # 100 KB/s with a 100 KB burst allowance.
        limiter = BandwidthLimiter(100_000)

        # First chunk fits inside the burst: no sleep.
        start = time.monotonic()
        limiter.throttle(100_000)
        self.assertLess(time.monotonic() - start, 0.1)

        # Bucket is now empty; the next 50 KB must wait ~0.5s.
        start = time.monotonic()
        limiter.throttle(50_000)
        elapsed = time.monotonic() - start
        self.assertGreater(elapsed, 0.3)
        self.assertLess(elapsed, 1.0)


class TestDownloaderPool(TestCase):
    def _downloader(self, workers):
        arlo = tests.arlo.PyArlo(
            media_download_workers=workers, media_download_rate_limit=0
        )
        return ArloMediaDownloader(arlo, "${N}")

    def test_downloads_use_multiple_workers(self):
        downloader = self._downloader(workers=3)
        seen_threads = []

        def fake_download(_media):
            seen_threads.append(threading.current_thread().name)
            time.sleep(0.2)
            return 0

        downloader._download = fake_download
        downloader.start()
        for _ in range(6):
            downloader.queue_download(object())

        deadline = time.monotonic() + 5
        while downloader.processing and time.monotonic() < deadline:
            time.sleep(0.05)
        downloader.stop()

        self.assertEqual(len(seen_threads), 6)
        self.assertGreater(len(set(seen_threads)), 1)

    def test_single_worker_drains_queue(self):
        downloader = self._downloader(workers=1)
        count = []

        def fake_download(_media):
            count.append(1)
            return 0

        downloader._download = fake_download
        downloader.start()
        for _ in range(4):
            downloader.queue_download(object())

        deadline = time.monotonic() + 5
        while downloader.processing and time.monotonic() < deadline:
            time.sleep(0.05)
        downloader.stop()

        self.assertEqual(len(count), 4)
