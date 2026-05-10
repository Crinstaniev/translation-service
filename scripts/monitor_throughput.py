#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import sys
import time
from urllib import error, parse, request


ACCESS_LOG_PATTERN = re.compile(
    r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/[^"]+"\s+(?P<status>\d{3})'
)
VLLM_THROUGHPUT_PATTERN = re.compile(
    r"Avg prompt throughput:\s*(?P<prompt_tps>\d+(?:\.\d+)?)\s*tokens/s,\s*"
    r"Avg generation throughput:\s*(?P<output_tps>\d+(?:\.\d+)?)\s*tokens/s,\s*"
    r"Running:\s*(?P<running>\d+)\s*reqs,\s*"
    r"Waiting:\s*(?P<waiting>\d+)\s*reqs,\s*"
    r"GPU KV cache usage:\s*(?P<kv_usage>\d+(?:\.\d+)?)%,\s*"
    r"Prefix cache hit rate:\s*(?P<prefix_hit_rate>\d+(?:\.\d+)?)%"
)


def fetch_metrics(base_url: str, window_seconds: int, timeout: float) -> dict:
    query = parse.urlencode({"window": window_seconds})
    url = f"{base_url.rstrip('/')}/metrics/realtime?{query}"
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def format_metrics_line(metrics: dict) -> str:
    rates = metrics["rates"]
    latency = metrics["latency_ms"]
    totals = metrics["window_totals"]
    return (
        f"uptime={metrics['uptime_seconds']:.0f}s "
        f"active={metrics['active_requests']}/{metrics['active_translations']} "
        f"qps={rates['qps']:.2f} "
        f"tps={rates['tps']:.2f} "
        f"start_tps={rates['started_tps']:.2f} "
        f"err_qps={rates['error_qps']:.2f} "
        f"in_cps={rates['input_cps']:.2f} "
        f"out_cps={rates['output_cps']:.2f} "
        f"lat_avg={latency['avg']:.1f}ms "
        f"lat_max={latency['max']:.1f}ms "
        f"req={totals['requests']} "
        f"ok_tx={totals['translations_completed']} "
        f"fail_tx={totals['translations_failed']}"
    )


def format_log_line(snapshot: dict) -> str:
    return (
        f"mode=log "
        f"window={snapshot['window_seconds']}s "
        f"qps={snapshot['qps']:.2f} "
        f"tps={snapshot['tps']:.2f} "
        f"stream_tps={snapshot['stream_tps']:.2f} "
        f"err_qps={snapshot['error_qps']:.2f} "
        f"req={snapshot['requests']} "
        f"tx={snapshot['translations']} "
        f"stream={snapshot['stream_translations']} "
        f"errors={snapshot['errors']}"
    )


def format_vllm_line(snapshot: dict) -> str:
    return (
        f"mode=vllm "
        f"prompt_tps={snapshot['prompt_tps']:.1f} "
        f"output_tps={snapshot['output_tps']:.1f} "
        f"total_tps={snapshot['total_tps']:.1f} "
        f"running={snapshot['running']} "
        f"waiting={snapshot['waiting']} "
        f"kv={snapshot['kv_usage']:.1f}% "
        f"prefix_hit={snapshot['prefix_hit_rate']:.1f}%"
    )


def parse_access_log(line: str) -> dict | None:
    match = ACCESS_LOG_PATTERN.search(line)
    if match is None:
        return None
    path = match.group("path")
    status = int(match.group("status"))
    return {
        "path": path,
        "status": status,
        "is_translation": path in {"/translate", "/translate/stream"},
        "is_streaming": path == "/translate/stream",
        "is_error": status >= 400,
    }


def parse_vllm_throughput(line: str) -> dict | None:
    match = VLLM_THROUGHPUT_PATTERN.search(line)
    if match is None:
        return None
    prompt_tps = float(match.group("prompt_tps"))
    output_tps = float(match.group("output_tps"))
    return {
        "prompt_tps": prompt_tps,
        "output_tps": output_tps,
        "total_tps": prompt_tps + output_tps,
        "running": int(match.group("running")),
        "waiting": int(match.group("waiting")),
        "kv_usage": float(match.group("kv_usage")),
        "prefix_hit_rate": float(match.group("prefix_hit_rate")),
    }


class LogMetricsTracker:
    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = max(1, window_seconds)
        self.events: deque[tuple[float, dict]] = deque()

    def add_line(self, line: str) -> None:
        event = parse_access_log(line)
        if event is None:
            return
        now = time.time()
        self.events.append((now, event))
        self._prune(now)

    def snapshot(self) -> dict:
        now = time.time()
        self._prune(now)
        requests = len(self.events)
        translations = sum(1 for _, event in self.events if event["is_translation"])
        stream_translations = sum(1 for _, event in self.events if event["is_streaming"])
        errors = sum(1 for _, event in self.events if event["is_error"])
        seconds = self.window_seconds
        return {
            "window_seconds": seconds,
            "requests": requests,
            "translations": translations,
            "stream_translations": stream_translations,
            "errors": errors,
            "qps": requests / seconds,
            "tps": translations / seconds,
            "stream_tps": stream_translations / seconds,
            "error_qps": errors / seconds,
        }

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()


def follow_log(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                yield line
                continue
            time.sleep(0.1)


def tail_recent_matching_line(path: str, pattern: re.Pattern[str]) -> str | None:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    for line in reversed(lines):
        if pattern.search(line):
            return line
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实时监控 translation-service 吞吐指标")
    parser.add_argument(
        "--mode",
        choices=("auto", "metrics", "log", "vllm"),
        default="auto",
        help="监控模式：auto/metrics/log/vllm，默认 auto",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8010",
        help="API 基地址，默认 http://127.0.0.1:8010",
    )
    parser.add_argument(
        "--log-file",
        default=".run/api.log",
        help="access log 文件路径，默认 .run/api.log",
    )
    parser.add_argument(
        "--vllm-log-file",
        default=".run/vllm.log",
        help="vLLM 日志文件路径，默认 .run/vllm.log",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=10,
        help="统计滑动窗口秒数，默认 10",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="刷新间隔秒数，默认 1.0",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="请求超时秒数，默认 2.0",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出原始 JSON，不做终端格式化",
    )
    return parser.parse_args()


def run_metrics_mode(args: argparse.Namespace) -> int:
    while True:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            metrics = fetch_metrics(args.base_url, args.window, args.timeout)
            if args.json:
                print(json.dumps(metrics, ensure_ascii=False), flush=True)
            else:
                print(f"[{timestamp}] {format_metrics_line(metrics)}", flush=True)
        except KeyboardInterrupt:
            print("\n停止监控。", file=sys.stderr)
            return 0
        except error.URLError as exc:
            raise RuntimeError(f"metrics 请求失败: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"metrics 模式异常: {exc}") from exc
        time.sleep(args.interval)


def run_log_mode(args: argparse.Namespace) -> int:
    if not os.path.exists(args.log_file):
        raise RuntimeError(f"log 文件不存在: {args.log_file}")

    tracker = LogMetricsTracker(window_seconds=args.window)
    next_emit_at = time.time()

    try:
        for line in follow_log(args.log_file):
            tracker.add_line(line)
            now = time.time()
            if now < next_emit_at:
                continue
            snapshot = tracker.snapshot()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if args.json:
                print(json.dumps(snapshot, ensure_ascii=False), flush=True)
            else:
                print(f"[{timestamp}] {format_log_line(snapshot)}", flush=True)
            next_emit_at = now + args.interval
    except KeyboardInterrupt:
        print("\n停止监控。", file=sys.stderr)
        return 0

    return 0


def run_vllm_mode(args: argparse.Namespace) -> int:
    if not os.path.exists(args.vllm_log_file):
        raise RuntimeError(f"vLLM log 文件不存在: {args.vllm_log_file}")

    last_seen_line = tail_recent_matching_line(args.vllm_log_file, VLLM_THROUGHPUT_PATTERN)
    if last_seen_line is not None:
        snapshot = parse_vllm_throughput(last_seen_line)
        if snapshot is not None:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if args.json:
                print(json.dumps(snapshot, ensure_ascii=False), flush=True)
            else:
                print(f"[{timestamp}] {format_vllm_line(snapshot)}", flush=True)

    try:
        for line in follow_log(args.vllm_log_file):
            snapshot = parse_vllm_throughput(line)
            if snapshot is None:
                continue
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if args.json:
                print(json.dumps(snapshot, ensure_ascii=False), flush=True)
            else:
                print(f"[{timestamp}] {format_vllm_line(snapshot)}", flush=True)
    except KeyboardInterrupt:
        print("\n停止监控。", file=sys.stderr)
        return 0

    return 0


def main() -> int:
    args = parse_args()

    if args.mode == "metrics":
        return run_metrics_mode(args)
    if args.mode == "log":
        return run_log_mode(args)
    if args.mode == "vllm":
        return run_vllm_mode(args)

    try:
        return run_metrics_mode(args)
    except RuntimeError as exc:
        print(f"metrics 不可用，尝试切到 vllm 模式: {exc}", file=sys.stderr, flush=True)
    try:
        return run_vllm_mode(args)
    except RuntimeError as exc:
        print(f"vllm 不可用，切到 log 模式: {exc}", file=sys.stderr, flush=True)
        return run_log_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
