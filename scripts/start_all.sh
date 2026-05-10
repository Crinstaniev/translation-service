#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${RUN_DIR:-$ROOT_DIR/.run}"
mkdir -p "$RUN_DIR"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

API_HOST="${TRANSLATION_SERVICE_HOST:-0.0.0.0}"
API_PORT="${TRANSLATION_SERVICE_PORT:-8010}"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-5173}"
VLLM_HOST="${VLLM_HOST:-0.0.0.0}"
VLLM_PORT="${VLLM_PORT:-8000}"
API_CHECK_HOST="${API_CHECK_HOST:-127.0.0.1}"
WEB_CHECK_HOST="${WEB_CHECK_HOST:-127.0.0.1}"
VLLM_CHECK_HOST="${VLLM_CHECK_HOST:-127.0.0.1}"
VLLM_BASE_URL="${VLLM_BASE_URL:-http://$VLLM_CHECK_HOST:$VLLM_PORT}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://$API_CHECK_HOST:$API_PORT}"

VLLM_PID_FILE="$RUN_DIR/vllm.pid"
API_PID_FILE="$RUN_DIR/api.pid"
WEB_PID_FILE="$RUN_DIR/web.pid"

is_pid_running() {
  local pid=$1
  kill -0 "$pid" 2>/dev/null
}

read_pid() {
  local file=$1
  if [[ -f "$file" ]]; then
    tr -d '[:space:]' <"$file"
  fi
}

contains_in_file() {
  local pattern=$1
  local file=$2

  if command -v rg >/dev/null 2>&1; then
    rg -q "$pattern" "$file"
  else
    grep -q "$pattern" "$file"
  fi
}

ensure_not_running() {
  local name=$1
  local pid_file=$2
  local pid

  pid="$(read_pid "$pid_file")"
  if [[ -n "${pid:-}" ]] && is_pid_running "$pid"; then
    echo "$name is already running with PID $pid"
    return 0
  fi

  if [[ -n "${pid:-}" ]]; then
    rm -f "$pid_file"
  fi

  return 1
}

wait_for_http_ok() {
  local url=$1
  local name=$2
  local retries=${3:-120}
  local sleep_seconds=${4:-2}
  local expected_status=${5:-200}

  for ((i = 0; i < retries; i++)); do
    status="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' "$url" || true)"
    if [[ "$status" == "$expected_status" ]]; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep "$sleep_seconds"
  done

  echo "$name failed readiness check: $url" >&2
  return 1
}

wait_for_vllm_ready() {
  local retries=${1:-240}
  local sleep_seconds=${2:-3}
  local url="http://$VLLM_CHECK_HOST:$VLLM_PORT/v1/models"
  local startup_log_pattern="Application startup complete"
  local status
  local pid

  for ((i = 0; i < retries; i++)); do
    status="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' "$url" || true)"
    if [[ "$status" == "200" ]]; then
      echo "vLLM is ready: $url"
      return 0
    fi

    pid="$(read_pid "$VLLM_PID_FILE")"
    if [[ -n "${pid:-}" ]] && ! is_pid_running "$pid"; then
      echo "vLLM process exited before readiness check passed." >&2
      return 1
    fi

    if [[ -f "$RUN_DIR/vllm.log" ]] && contains_in_file "$startup_log_pattern" "$RUN_DIR/vllm.log"; then
      echo "vLLM startup log detected; continue startup (models endpoint status: ${status:-unknown})."
      return 0
    fi

    sleep "$sleep_seconds"
  done

  echo "vLLM failed readiness check: $url" >&2
  return 1
}

start_vllm() {
  if ensure_not_running "vLLM" "$VLLM_PID_FILE"; then
    return 0
  fi

  echo "Starting vLLM..."
  nohup bash -lc "cd '$ROOT_DIR' && ./scripts/start_vllm.sh" >"$RUN_DIR/vllm.log" 2>&1 &
  echo $! >"$VLLM_PID_FILE"
  echo "vLLM PID: $(cat "$VLLM_PID_FILE")"
  wait_for_vllm_ready 240 3
}

start_api() {
  if ensure_not_running "Translation API" "$API_PID_FILE"; then
    return 0
  fi

  echo "Starting translation API..."
  nohup bash -lc "cd '$ROOT_DIR' && ./scripts/run_api.sh" >"$RUN_DIR/api.log" 2>&1 &
  echo $! >"$API_PID_FILE"
  echo "API PID: $(cat "$API_PID_FILE")"
  wait_for_http_ok "http://$API_CHECK_HOST:$API_PORT/health" "Translation API" 60 2
}

start_web() {
  if ensure_not_running "Vue frontend" "$WEB_PID_FILE"; then
    return 0
  fi

  echo "Starting Vue frontend..."
  nohup bash -lc "cd '$ROOT_DIR/web' && export VITE_API_BASE_URL='$VITE_API_BASE_URL' && npm run dev -- --host '$WEB_HOST' --port '$WEB_PORT'" >"$RUN_DIR/web.log" 2>&1 &
  echo $! >"$WEB_PID_FILE"
  echo "Web PID: $(cat "$WEB_PID_FILE")"
  wait_for_http_ok "http://$WEB_CHECK_HOST:$WEB_PORT" "Vue frontend" 60 2
}

start_vllm
start_api
start_web

cat <<EOF

All services are running.
Frontend (host Chrome): http://localhost:$WEB_PORT
API (host Chrome):      http://localhost:$API_PORT
vLLM:                   http://localhost:$VLLM_PORT

PID files:
  $VLLM_PID_FILE
  $API_PID_FILE
  $WEB_PID_FILE

Logs:
  $RUN_DIR/vllm.log
  $RUN_DIR/api.log
  $RUN_DIR/web.log
EOF
