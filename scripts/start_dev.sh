#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/.run}"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

API_HOST="${TRANSLATION_SERVICE_HOST:-127.0.0.1}"
API_PORT="${TRANSLATION_SERVICE_PORT:-8010}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-5173}"
VLLM_BASE_URL="${VLLM_BASE_URL:-http://127.0.0.1:8000}"
START_VLLM="${START_VLLM:-false}"

mkdir -p "$LOG_DIR"

cleanup() {
  local exit_code=$?
  if [[ -n "${WEB_PID:-}" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${VLLM_PID:-}" ]] && kill -0 "$VLLM_PID" 2>/dev/null; then
    kill "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
  fi
  exit "$exit_code"
}

wait_for_http() {
  local url=$1
  local name=$2
  local retries="${3:-60}"
  local sleep_seconds="${4:-1}"

  for ((i=0; i<retries; i++)); do
    if curl --noproxy '*' -fsS "$url" >/dev/null 2>&1; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep "$sleep_seconds"
  done

  echo "$name failed to become ready: $url" >&2
  return 1
}

trap cleanup INT TERM EXIT

if [[ "$START_VLLM" == "true" ]]; then
  echo "Starting vLLM..."
  (
    cd "$ROOT_DIR"
    ./scripts/start_vllm.sh
  ) >"$LOG_DIR/vllm.log" 2>&1 &
  VLLM_PID=$!
  echo "vLLM PID: $VLLM_PID"
  wait_for_http "$VLLM_BASE_URL/v1/models" "vLLM"
else
  echo "Skipping vLLM startup. Expecting an existing server at $VLLM_BASE_URL"
fi

echo "Starting translation API..."
(
  cd "$ROOT_DIR"
  ./scripts/run_api.sh
) >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!
echo "API PID: $API_PID"
wait_for_http "http://$API_HOST:$API_PORT/health" "Translation API"

echo "Starting Vue frontend..."
(
  cd "$ROOT_DIR/web"
  export VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://$API_HOST:$API_PORT}"
  npm run dev -- --host "$WEB_HOST" --port "$WEB_PORT"
) >"$LOG_DIR/web.log" 2>&1 &
WEB_PID=$!
echo "Web PID: $WEB_PID"
wait_for_http "http://$WEB_HOST:$WEB_PORT" "Vue frontend"

cat <<EOF

Development stack is running.
Frontend: http://$WEB_HOST:$WEB_PORT
API:      http://$API_HOST:$API_PORT
vLLM:     $VLLM_BASE_URL

Logs:
  $LOG_DIR/web.log
  $LOG_DIR/api.log
  $LOG_DIR/vllm.log

Press Ctrl+C to stop all started processes.
EOF

wait "$WEB_PID"
