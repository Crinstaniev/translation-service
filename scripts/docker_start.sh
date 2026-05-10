#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker build -f Dockerfile.api -t "${API_IMAGE:-translation-service-api:local}" .
docker build -f web/Dockerfile -t "${WEB_IMAGE:-translation-service-web:local}" web

exec docker compose up
