# 翻译服务（vLLM + HY-MT1.5）

本项目是一个基于 FastAPI 的翻译服务，提供：

- 同步翻译接口（`POST /translate`）
- 流式翻译接口（`POST /translate/stream`，SSE）
- 实时指标（`GET /metrics/realtime`）
- 历史记录与实时事件（`/translations/*`）

同时包含一个 Vue 前端用于在线提交测试翻译与查看翻译历史。
此外已整合 `pdf-manga-translator` 流水线，用于 PDF OCR 提取、批量翻译、回生 PDF。

## 目录结构

- `app/`：后端服务代码（FastAPI）
- `web/`：前端页面（Vue + Vite）
- `scripts/`：本地启动/测试脚本
- `pdf_manga_translator/`：PDF/漫画翻译流水线核心模块
- `prompts/`：批量翻译提示词与术语表
- `docs/`：接口文档与部署说明
- `docker-compose.yml`：一键启动（vLLM + API + Web）
- `docker-compose.paddleocr.yml`：PaddleOCR-VL 服务编排

## 先决条件

- Python 3.12+
- Node.js 18+
- Docker（若使用 docker compose）
- 已准备可访问的 vLLM 后端（默认模型名：`hy-mt15-7b`）

> 如果你本地没有 GPU 或 vLLM，API 可先运行起来，但翻译接口会因为后端不可达而失败。

## 本地开发启动

1. 安装 Python 依赖（建议用 venv）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. 安装前端依赖

```bash
cd web
npm ci
cd ..
```

3. 配置环境变量

```bash
cp .env.example .env
cp web/.env.example web/.env
```

4. 启动（默认前端 + 后端；可选是否同时启动 vLLM）

```bash
# 如果你已独立启动 vLLM，直接运行：
./scripts/start_dev.sh

# 也可显式打开 vLLM 启动入口
START_VLLM=true ./scripts/start_dev.sh
```

脚本会启动：

- API：默认 `http://127.0.0.1:8010`
- Web：默认 `http://127.0.0.1:5173`

## Docker 启动（推荐）

1. 复制环境文件

```bash
cp .env.docker.example .env.docker
```

2. 根据机器实际情况修改 `.env.docker` 中的路径与参数（如模型路径、GPU 资源、端口）

3. 启动

```bash
docker compose --env-file .env.docker up --build
```

服务端口：

- Web：`5173`
- API：`8010`
- vLLM：`8000`

## API 说明

- `GET /health`：服务健康检查
- `GET /metrics/realtime`：实时指标
- `GET /translations/history`：内存中的历史记录
- `GET /translations/realtime`：SSE 实时翻译事件
- `POST /translate`：同步翻译
- `POST /translate/stream`：流式翻译（SSE）

详细字段约束与请求/响应示例见：`docs/translation-api-reference.md`

## 可配置环境变量（后端）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TRANSLATION_SERVICE_HOST` | `0.0.0.0` | API 监听主机 |
| `TRANSLATION_SERVICE_PORT` | `8010` | API 监听端口 |
| `VLLM_BASE_URL` | `http://127.0.0.1:8000` | vLLM 服务地址 |
| `VLLM_MODEL_NAME` | `hy-mt15-7b` | 翻译模型名 |
| `VLLM_REQUEST_TIMEOUT_SECONDS` | `60` | 翻译超时 |
| `TRANSLATION_TEMPERATURE` | `0.3` | 采样温度 |
| `TRANSLATION_TOP_P` | `0.6` | Top-p |
| `TRANSLATION_TOP_K` | `20` | Top-k |
| `TRANSLATION_REPETITION_PENALTY` | `1.05` | 重复惩罚 |
| `TRANSLATION_HISTORY_LIMIT` | `500` | 历史记录上限 |
| `STRICT_HEALTHCHECK` | `false` | 严格健康检查 |
| `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | 允许跨域来源 |

## 运行测试

```bash
pytest
cd web && npm test
```

## PDF/漫画翻译流水线

整合后的批处理脚本：

- `scripts/batch-pdf-to-markdown.py`：PDF -> Markdown（调用 PaddleOCR-VL）
- `scripts/batch-translate-markdown.py`：Markdown -> 中文 Markdown（调用本仓翻译 API）
- `scripts/batch-markdown-to-pdf.py`：中文 Markdown -> HTML/PDF

便捷命令：

```bash
scripts/batch-pdf-to-markdown.py input/pdfs --output-dir out/markdown
scripts/batch-translate-markdown.py out/markdown --output-dir out/translated
scripts/batch-markdown-to-pdf.py out/translated --output-dir out/pdf --asset-root out/markdown
```

PaddleOCR 相关配置：

- `docker-compose.paddleocr.yml`
- `vlm_server_config.yaml`

更多背景和流程细节见：

- `pdf_manga_translator/` 目录下的实现和测试用例

## Git 仓库

- 公开仓库：`https://github.com/Crinstaniev/translation-service`
