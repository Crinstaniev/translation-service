#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${RUN_DIR:-$ROOT_DIR/.run}"

stop_pid_file() {
  local name=$1
  local pid_file=$2

  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi

  local pid
  pid="$(tr -d '[:space:]' <"$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping $name (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "Force stopping $name (PID $pid)..."
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi

  rm -f "$pid_file"
}

stop_by_pattern() {
  local name=$1
  local pattern=$2
  local pids

  pids="$(pgrep -f "$pattern" || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  echo "Stopping $name by process pattern..."
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" 2>/dev/null || true
  done <<<"$pids"
}

stop_pid_file "Vue frontend" "$RUN_DIR/web.pid"
stop_pid_file "Translation API" "$RUN_DIR/api.pid"
stop_pid_file "vLLM" "$RUN_DIR/vllm.pid"

stop_by_pattern "Vue frontend" "node .*/web/node_modules/.bin/vite"
stop_by_pattern "Translation API" "python -m app.main"
stop_by_pattern "vLLM" "vllm serve /home/crinstaniev/models/HY-MT1.5-7B-GPTQ-Int4"

echo "All matching services have been stopped."
