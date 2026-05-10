#!/usr/bin/env bash
set -euo pipefail

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$HOME/models/modelscope-cache}"
export VLLM_USE_MODELSCOPE="${VLLM_USE_MODELSCOPE:-true}"

VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-$HOME/models/HY-MT1.5-7B-GPTQ-Int4}"
VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-hy-mt15-7b}"
VLLM_PORT="${VLLM_PORT:-8000}"

# Detailed request logging controls.
# INFO logs request metadata. DEBUG adds prompt/token-level details.
export VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-DEBUG}"
VLLM_ENABLE_LOG_REQUESTS="${VLLM_ENABLE_LOG_REQUESTS:-true}"
VLLM_ENABLE_LOG_OUTPUTS="${VLLM_ENABLE_LOG_OUTPUTS:-true}"
VLLM_ENABLE_LOG_DELTAS="${VLLM_ENABLE_LOG_DELTAS:-true}"
VLLM_MAX_LOG_LEN="${VLLM_MAX_LOG_LEN:-}"

source "${VLLM_VENV_PATH:-$HOME/services/vllm-hy-mt/.venv/bin/activate}"

LOG_ARGS=()
if [[ "$VLLM_ENABLE_LOG_REQUESTS" == "true" ]]; then
  LOG_ARGS+=(--enable-log-requests)
else
  LOG_ARGS+=(--no-enable-log-requests)
fi

if [[ "$VLLM_ENABLE_LOG_OUTPUTS" == "true" ]]; then
  LOG_ARGS+=(--enable-log-outputs)
else
  LOG_ARGS+=(--no-enable-log-outputs)
fi

if [[ "$VLLM_ENABLE_LOG_DELTAS" == "true" ]]; then
  LOG_ARGS+=(--enable-log-deltas)
else
  LOG_ARGS+=(--no-enable-log-deltas)
fi

if [[ -n "$VLLM_MAX_LOG_LEN" ]]; then
  LOG_ARGS+=(--max-log-len "$VLLM_MAX_LOG_LEN")
fi

exec vllm serve "${VLLM_MODEL_PATH}" \
  --host 0.0.0.0 \
  --port "${VLLM_PORT}" \
  --served-model-name "${VLLM_MODEL_NAME}" \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.82 \
  --max-model-len 2048 \
  --max-num-seqs 4 \
  "${LOG_ARGS[@]}"
