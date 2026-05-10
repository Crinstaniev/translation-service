#!/usr/bin/env bash
set -euo pipefail

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

source "${API_VENV_PATH:-.venv/bin/activate}"

exec python -m app.main
