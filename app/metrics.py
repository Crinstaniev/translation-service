from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time


@dataclass(slots=True)
class _SecondBucket:
    second: int
    request_count: int = 0
    request_errors: int = 0
    request_latency_sum_ms: float = 0.0
    request_latency_max_ms: float = 0.0
    translation_started: int = 0
    translation_completed: int = 0
    translation_failed: int = 0
    streaming_completed: int = 0
    input_chars: int = 0
    output_chars: int = 0


class MetricsCollector:
    def __init__(self, retention_seconds: int = 300) -> None:
        self._retention_seconds = retention_seconds
        self._buckets: deque[_SecondBucket] = deque()
        self._lock = threading.Lock()
        self._active_requests = 0
        self._active_translations = 0
        self._started_at = time.time()
        self._totals = _SecondBucket(second=0)

    def track_request_start(self) -> None:
        with self._lock:
            self._active_requests += 1

    def track_request_end(self, *, status_code: int, duration_ms: float) -> None:
        with self._lock:
            bucket = self._get_bucket_locked()
            bucket.request_count += 1
            if status_code >= 400:
                bucket.request_errors += 1
            bucket.request_latency_sum_ms += duration_ms
            bucket.request_latency_max_ms = max(bucket.request_latency_max_ms, duration_ms)

            self._totals.request_count += 1
            if status_code >= 400:
                self._totals.request_errors += 1
            self._totals.request_latency_sum_ms += duration_ms
            self._totals.request_latency_max_ms = max(self._totals.request_latency_max_ms, duration_ms)
            self._active_requests = max(0, self._active_requests - 1)

    def track_translation_start(self, *, input_chars: int) -> float:
        started_at = time.perf_counter()
        with self._lock:
            bucket = self._get_bucket_locked()
            bucket.translation_started += 1
            bucket.input_chars += input_chars

            self._totals.translation_started += 1
            self._totals.input_chars += input_chars
            self._active_translations += 1
        return started_at

    def track_translation_end(
        self,
        started_at: float,
        *,
        success: bool,
        output_chars: int,
        is_streaming: bool,
    ) -> None:
        _ = started_at
        with self._lock:
            bucket = self._get_bucket_locked()
            if success:
                bucket.translation_completed += 1
                bucket.output_chars += output_chars
                if is_streaming:
                    bucket.streaming_completed += 1
                self._totals.translation_completed += 1
                self._totals.output_chars += output_chars
                if is_streaming:
                    self._totals.streaming_completed += 1
            else:
                bucket.translation_failed += 1
                self._totals.translation_failed += 1
            self._active_translations = max(0, self._active_translations - 1)

    def snapshot(self, window_seconds: int = 10) -> dict:
        with self._lock:
            self._prune_locked()
            now = int(time.time())
            window_start = now - max(1, window_seconds) + 1
            window_buckets = [bucket for bucket in self._buckets if bucket.second >= window_start]
            seconds = max(1, window_seconds)

            request_count = sum(bucket.request_count for bucket in window_buckets)
            request_errors = sum(bucket.request_errors for bucket in window_buckets)
            translation_started = sum(bucket.translation_started for bucket in window_buckets)
            translation_completed = sum(bucket.translation_completed for bucket in window_buckets)
            translation_failed = sum(bucket.translation_failed for bucket in window_buckets)
            streaming_completed = sum(bucket.streaming_completed for bucket in window_buckets)
            input_chars = sum(bucket.input_chars for bucket in window_buckets)
            output_chars = sum(bucket.output_chars for bucket in window_buckets)
            latency_sum_ms = sum(bucket.request_latency_sum_ms for bucket in window_buckets)
            latency_max_ms = max((bucket.request_latency_max_ms for bucket in window_buckets), default=0.0)

            avg_latency_ms = latency_sum_ms / request_count if request_count else 0.0
            uptime_seconds = max(0.0, time.time() - self._started_at)
            total_avg_latency_ms = (
                self._totals.request_latency_sum_ms / self._totals.request_count
                if self._totals.request_count
                else 0.0
            )

            return {
                "window_seconds": seconds,
                "uptime_seconds": round(uptime_seconds, 3),
                "active_requests": self._active_requests,
                "active_translations": self._active_translations,
                "rates": {
                    "qps": round(request_count / seconds, 3),
                    "tps": round(translation_completed / seconds, 3),
                    "started_tps": round(translation_started / seconds, 3),
                    "stream_tps": round(streaming_completed / seconds, 3),
                    "error_qps": round(request_errors / seconds, 3),
                    "failed_tps": round(translation_failed / seconds, 3),
                    "input_cps": round(input_chars / seconds, 3),
                    "output_cps": round(output_chars / seconds, 3),
                },
                "latency_ms": {
                    "avg": round(avg_latency_ms, 3),
                    "max": round(latency_max_ms, 3),
                },
                "window_totals": {
                    "requests": request_count,
                    "request_errors": request_errors,
                    "translations_started": translation_started,
                    "translations_completed": translation_completed,
                    "translations_failed": translation_failed,
                    "streaming_completed": streaming_completed,
                    "input_chars": input_chars,
                    "output_chars": output_chars,
                },
                "lifetime_totals": {
                    "requests": self._totals.request_count,
                    "request_errors": self._totals.request_errors,
                    "translations_started": self._totals.translation_started,
                    "translations_completed": self._totals.translation_completed,
                    "translations_failed": self._totals.translation_failed,
                    "streaming_completed": self._totals.streaming_completed,
                    "input_chars": self._totals.input_chars,
                    "output_chars": self._totals.output_chars,
                    "avg_latency_ms": round(total_avg_latency_ms, 3),
                    "max_latency_ms": round(self._totals.request_latency_max_ms, 3),
                },
            }

    def _get_bucket_locked(self) -> _SecondBucket:
        now = int(time.time())
        if self._buckets and self._buckets[-1].second == now:
            bucket = self._buckets[-1]
        else:
            bucket = _SecondBucket(second=now)
            self._buckets.append(bucket)
        self._prune_locked()
        return bucket

    def _prune_locked(self) -> None:
        cutoff = int(time.time()) - self._retention_seconds
        while self._buckets and self._buckets[0].second < cutoff:
            self._buckets.popleft()
