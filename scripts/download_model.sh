#!/usr/bin/env bash
set -euo pipefail

export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$HOME/models/modelscope-cache}"
export MODEL_ID="${MODEL_ID:-Tencent-Hunyuan/HY-MT1.5-7B-GPTQ-Int4}"
export MODEL_DIR="${MODEL_DIR:-$HOME/models/HY-MT1.5-7B-GPTQ-Int4}"

mkdir -p "${MODELSCOPE_CACHE}"
mkdir -p "${MODEL_DIR}"

if ! command -v modelscope >/dev/null 2>&1; then
  echo "modelscope CLI not found. Install it first with:" >&2
  echo "  pip install modelscope" >&2
  exit 1
fi

exec modelscope download \
  --model "${MODEL_ID}" \
  --local_dir "${MODEL_DIR}"
