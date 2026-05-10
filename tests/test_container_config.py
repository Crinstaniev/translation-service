from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_compose_defines_full_stack_services() -> None:
    compose = read_text("docker-compose.yml")

    assert "vllm:" in compose
    assert "api:" in compose
    assert "web:" in compose
    assert "vllm/vllm-openai" in compose
    assert "VLLM_BASE_URL: http://vllm:8000" in compose
    assert "target: /models/HY-MT1.5-7B-GPTQ-Int4" in compose
    assert "driver: nvidia" in compose


def test_api_dockerfile_runs_fastapi_service() -> None:
    dockerfile = read_text("Dockerfile.api")

    assert "FROM python:3.12-slim" in dockerfile
    assert "COPY pyproject.toml" in dockerfile
    assert "COPY app ./app" in dockerfile
    assert 'CMD ["python", "-m", "app.main"]' in dockerfile


def test_web_container_builds_static_site_and_proxies_api() -> None:
    dockerfile = read_text("web/Dockerfile")
    nginx = read_text("web/nginx.conf")
    app = read_text("web/src/App.vue")
    dockerignore = read_text("web/.dockerignore")

    assert "FROM node:22-alpine AS build" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "FROM nginx:" in dockerfile
    assert "COPY nginx.conf" in dockerfile

    assert "location /api/" in nginx
    assert "resolver 127.0.0.11" in nginx
    assert "set $api_upstream http://api:8010" in nginx
    assert "proxy_pass $api_upstream/" in nginx
    assert "proxy_buffering off" in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "import.meta.env.VITE_API_BASE_URL || '/api'" in app
    assert "node_modules/" in dockerignore
    assert "dist/" in dockerignore


def test_one_command_scripts_wrap_docker_compose() -> None:
    start_script = read_text("scripts/docker_start.sh")
    stop_script = read_text("scripts/docker_stop.sh")

    assert 'docker build -f Dockerfile.api -t "${API_IMAGE:-translation-service-api:local}" .' in start_script
    assert 'docker build -f web/Dockerfile -t "${WEB_IMAGE:-translation-service-web:local}" web' in start_script
    assert "docker compose up" in start_script
    assert "docker compose down" in stop_script
