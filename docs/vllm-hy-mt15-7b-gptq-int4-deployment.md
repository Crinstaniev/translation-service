# vLLM + HY-MT1.5-7B-GPTQ-Int4 部署方案

## 目标

在当前机器上部署一个基于 `vLLM` 的本地翻译服务，模型使用 `tencent/HY-MT1.5-7B-GPTQ-Int4`，用于游戏汉化、批量文本翻译、术语约束翻译和上下文翻译。

本文档按当前机器配置定制，不追求“大模型极限性能”，重点是：

- 能稳定跑起来
- 显存占用可控
- 适合游戏文本翻译
- 后续容易接入脚本或服务

## 当前机器情况

已确认的环境信息：

- 系统：`Ubuntu 24.04.4 LTS`
- 运行环境：`WSL2`
- GPU：`NVIDIA GeForce RTX 5070 Laptop GPU`
- 显存：约 `8GB`
- 驱动：`582.05`
- CUDA：`13.0` 驱动侧可见
- CPU：`AMD Ryzen 9 8945HX`
- 内存：`16GB`
- 磁盘空余：约 `948GB`

## 为什么选这个方案

`tencent/HY-MT1.5-7B-GPTQ-Int4` 相比通用模型更适合翻译任务，原因主要有：

- 它是翻译专用模型，不是纯聊天模型
- 模型卡明确支持术语干预、上下文翻译、格式化翻译
- 有官方 `GPTQ Int4` 量化版本，适合 `8GB` 显存机器
- 对游戏汉化常见场景更友好，例如：
  - 保留占位符
  - 保留换行和格式标签
  - 利用上下文翻译短句和歧义句

对于这台机器，`7B GPTQ Int4 + vLLM` 是比较平衡的选择。`14B` 以上模型不适合作为本机常驻服务。

## 方案概览

推荐部署方式：

- Python 环境：`3.12`
- 服务框架：`vLLM`
- 模型：`tencent/HY-MT1.5-7B-GPTQ-Int4`
- 部署方式：OpenAI Compatible API Server
- 端口：`8000`
- 单卡部署：`tensor-parallel-size=1`

建议将模型和缓存都放在 Linux 文件系统中，不要放在 `/mnt/c` 下。

## 目录建议

建议在用户目录下准备以下目录：

```bash
mkdir -p ~/services/vllm-hy-mt
mkdir -p ~/models/modelscope-cache
mkdir -p ~/models/HY-MT1.5-7B-GPTQ-Int4
```

其中：

- `~/services/vllm-hy-mt` 用于放启动脚本
- `~/models/modelscope-cache` 用于放 ModelScope 缓存
- `~/models/HY-MT1.5-7B-GPTQ-Int4` 用于放模型文件

## 环境准备

### 1. 创建 Python 环境

`vLLM` 官方当前推荐使用 `uv` 或独立虚拟环境。这里用 `uv`：

```bash
cd ~/services/vllm-hy-mt
uv venv --python 3.12 .venv
source .venv/bin/activate
```

如果本机没有 `uv`，先安装：

```bash
pip install -U uv
```

### 2. 安装 vLLM

```bash
uv pip install vllm --torch-backend=auto
```

可选工具：

```bash
uv pip install modelscope httpx
```

### 3. 登录 ModelScope

如果后续需要访问受限模型，可以先登录：

```bash
modelscope login
```

如果模型公开可下载，这一步可以跳过。

### 4. 用 ModelScope 下载模型

建议先下载到本地目录，再让 `vLLM` 直接读取本地路径：

```bash
export MODELSCOPE_CACHE=$HOME/models/modelscope-cache

modelscope download \
  --model Tencent-Hunyuan/HY-MT1.5-7B-GPTQ-Int4 \
  --local_dir $HOME/models/HY-MT1.5-7B-GPTQ-Int4
```

如果 `ModelScope` 上该模型的仓库名与上面不完全一致，以实际仓库名为准；部署方式不变，最终都统一指向本地目录。

仓库里也提供了一个下载脚本：

```bash
./scripts/download_model.sh
```

如需覆盖默认路径：

```bash
MODEL_ID=Tencent-Hunyuan/HY-MT1.5-7B-GPTQ-Int4 \
MODEL_DIR=$HOME/models/HY-MT1.5-7B-GPTQ-Int4 \
MODELSCOPE_CACHE=$HOME/models/modelscope-cache \
./scripts/download_model.sh
```

## 模型选择

使用模型：

```text
tencent/HY-MT1.5-7B-GPTQ-Int4
```

参考模型页：

- https://huggingface.co/tencent/HY-MT1.5-7B-GPTQ-Int4

这个量化版更适合你的 `8GB` 显卡。`FP8` 版不适合作为这台机器的首选部署形态。

## 启动参数建议

对于当前机器，建议先用以下保守参数：

- `--gpu-memory-utilization 0.82`
- `--max-model-len 2048`
- `--max-num-seqs 4`
- `--tensor-parallel-size 1`

说明：

- `0.82` 比较保守，降低 OOM 风险
- `2048` 足够覆盖大部分游戏文本批次和上下文翻译
- `max-num-seqs` 不宜过高，否则 `8GB` 显存容易爆

如果后续运行稳定，可以逐步尝试：

- `--gpu-memory-utilization 0.85`
- `--max-model-len 3072`

## 启动命令

推荐优先使用本地目录启动，而不是让 `vLLM` 运行时在线拉取。

### 方式一：直接启动

```bash
export MODELSCOPE_CACHE=$HOME/models/modelscope-cache
export VLLM_USE_MODELSCOPE=true

source ~/services/vllm-hy-mt/.venv/bin/activate

vllm serve $HOME/models/HY-MT1.5-7B-GPTQ-Int4 \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name hy-mt15-7b \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.82 \
  --max-model-len 2048 \
  --max-num-seqs 4
```

### 方式二：写成启动脚本

建议保存为 `~/services/vllm-hy-mt/start.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

export MODELSCOPE_CACHE="$HOME/models/modelscope-cache"
export VLLM_USE_MODELSCOPE=true

source "$HOME/services/vllm-hy-mt/.venv/bin/activate"

exec vllm serve "$HOME/models/HY-MT1.5-7B-GPTQ-Int4" \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name hy-mt15-7b \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.82 \
  --max-model-len 2048 \
  --max-num-seqs 4
```

赋权：

```bash
chmod +x ~/services/vllm-hy-mt/start.sh
```

启动：

```bash
~/services/vllm-hy-mt/start.sh
```

## 连通性测试

服务启动后，先看模型列表：

```bash
curl http://127.0.0.1:8000/v1/models
```

再做一次最小翻译测试：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hy-mt15-7b",
    "messages": [
      {
        "role": "user",
        "content": "将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\nIt'\''s on the house."
      }
    ],
    "temperature": 0.3,
    "top_p": 0.6,
    "extra_body": {
      "top_k": 20,
      "repetition_penalty": 1.05
    }
  }'
```

## 推荐推理参数

根据模型卡给出的建议，这个模型适合使用较保守的采样参数：

- `temperature=0.3 ~ 0.7`
- `top_p=0.6`
- `top_k=20`
- `repetition_penalty=1.05`

对于游戏汉化，更建议偏确定性：

```text
temperature = 0.2 ~ 0.4
top_p = 0.6
top_k = 20
repetition_penalty = 1.05
```

这样能减少废话、漏翻和发散。

## 游戏汉化 Prompt 模板

### 1. 普通翻译

适用于短句、对白、UI 文本：

```text
将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：

{source_text}
```

### 2. 术语表翻译

适用于专有名词、人名、地名、技能名统一：

```text
参考下面的翻译：
{source_term_1} 翻译成 {target_term_1}
{source_term_2} 翻译成 {target_term_2}
{source_term_3} 翻译成 {target_term_3}

将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：
{source_text}
```

### 3. 上下文翻译

适用于歧义句、连续对白、剧情文本：

```text
{context}
参考上面的信息，把下面的文本翻译成中文，注意不需要翻译上文，也不要额外解释：
{source_text}
```

### 4. 格式保留翻译

适用于含变量、控制符、标签的文本：

```text
将以下文本翻译为中文。
要求：
1. 保留所有变量、占位符、控制符和标签不变
2. 保留换行结构
3. 只输出翻译结果，不要解释

待翻译文本：
{source_text}
```

建议重点保护以下内容：

- `%s`
- `%d`
- `{name}`
- `{0}`
- `<color=red>`
- `\\n`
- `\\r\\n`
- `【...】`
- `[...]`
- RPG Maker 或其他引擎中的脚本标记

## 接入建议

如果后续要接你的翻译服务，建议按下面的方式调用：

- 接口协议：OpenAI Compatible API
- 优先使用 `/v1/chat/completions`
- 单次请求不要塞太大文本块
- 推荐按“句段”或“短段落”批处理

推荐的切分策略：

- UI 文本：单条翻译
- 对白：按 1 到 5 行分组
- 剧情文本：附带前文 3 到 10 行作为 `context`
- 大段文档：先切段，再分批提交

## 性能预期

在这台机器上，这个方案的合理预期是：

- 可以作为常驻本地翻译服务
- 单请求延迟可接受
- 批量翻译吞吐中等
- 不适合高并发生产流量

影响性能的主要因素：

- 上下文长度
- 并发数
- 单次输入字符数
- 是否频繁触发显存峰值

如果出现吞吐下降或不稳定，优先做这几件事：

1. 将 `--max-model-len` 从 `2048` 降到 `1536`
2. 将 `--max-num-seqs` 从 `4` 降到 `2`
3. 把单次翻译批次切小
4. 减少上下文长度

## 风险与限制

### 1. WSL2 不是最强部署形态

当前环境可以跑，但长期稳定性和极限性能通常不如原生 Linux。

### 2. 8GB 显存空间偏紧

虽然 `GPTQ Int4` 已经显著降低了门槛，但一旦上下文太长或并发过高，仍可能触发 OOM。

### 3. 游戏汉化质量不只取决于模型

实际质量高度依赖：

- 术语表
- 上下文供给
- 文本切分
- 标签保护
- 后处理

模型只是基础能力，流程设计同样重要。

## 推荐的后续增强

建议按以下顺序增强：

1. 增加术语表注入能力
2. 增加变量和标签保护器
3. 增加上下文拼接策略
4. 增加批量文件翻译器
5. 增加翻译缓存，避免重复请求
6. 增加人工校对回写机制

## 建议的下一步

如果要继续落地，建议直接做以下工作：

1. 在本机安装 `vLLM`
2. 启动 `tencent/HY-MT1.5-7B-GPTQ-Int4`
3. 写一个最小调用脚本
4. 加上游戏文本专用 prompt 模板
5. 再做术语表和标签保护

## 当前仓库实现

当前仓库已经补上一个最小可用实现，主要文件如下：

- `app/main.py`：翻译 API 入口，提供 `/health` 和 `/translate`
- `app/main.py`：同时提供 `/translate/stream` SSE 流式接口
- `app/prompting.py`：翻译 prompt 组装逻辑
- `app/vllm_client.py`：下游 `vLLM` 调用封装
- `scripts/start_vllm.sh`：本机启动 `vLLM`
- `scripts/download_model.sh`：用 `ModelScope` 下载模型到本地目录
- `scripts/run_api.sh`：本机启动翻译 API
- `scripts/smoke_test.py`：对已启动服务做最小冒烟验证
- `tests/`：本地单元测试和 API 集成测试

## API 运行方式

### 1. 创建服务虚拟环境

```bash
cd /home/crinstaniev/dev/translation-service
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi httpx uvicorn pydantic
```

### 2. 启动 vLLM

```bash
./scripts/start_vllm.sh
```

默认会开启详细请求日志（包括请求参数和输出片段）：

- `--enable-log-requests`
- `--enable-log-outputs`
- `VLLM_LOGGING_LEVEL=DEBUG`

如果你想降低日志量，可在启动前设置：

```bash
VLLM_LOGGING_LEVEL=INFO \
VLLM_ENABLE_LOG_OUTPUTS=false \
./scripts/start_vllm.sh
```

如果想限制单条日志中 prompt/输出打印长度：

```bash
VLLM_MAX_LOG_LEN=4000 ./scripts/start_vllm.sh
```

### 3. 启动翻译 API

另开一个终端：

```bash
cd /home/crinstaniev/dev/translation-service
source .venv/bin/activate
python -m app.main
```

或直接执行：

```bash
./scripts/run_api.sh
```

### 4. 测试 API

健康检查：

```bash
curl http://127.0.0.1:8010/health
```

翻译测试：

```bash
curl http://127.0.0.1:8010/translate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "It'\''s on the house.",
    "source_lang": "en",
    "target_lang": "zh",
    "preserve_format": true
  }'
```

或运行仓库自带的冒烟脚本：

```bash
python scripts/smoke_test.py
```

## Vue 前端

仓库内新增了一个独立前端项目：

- `web/`

一键启动当前开发栈：

```bash
./scripts/start_dev.sh
```

默认行为：

- 假定 `vLLM` 已经在 `http://127.0.0.1:8000` 运行
- 启动翻译 API：`http://127.0.0.1:8010`
- 启动 Vue 前端：`http://127.0.0.1:5173`

如果希望脚本顺带启动 `vLLM`：

```bash
START_VLLM=true ./scripts/start_dev.sh
```

开发启动：

```bash
cd /home/crinstaniev/dev/translation-service/web
npm install
cp .env.example .env
npm run dev
```

默认会连接：

```text
http://127.0.0.1:8010
```

如果后端地址不同，修改：

```bash
VITE_API_BASE_URL=http://your-host:8010
```

前端使用：

- `POST /translate/stream`
- 协议：`text/event-stream`
- 事件类型：`start`、`delta`、`end`、`error`

## 一键启停

使用仓库脚本一键启动全部服务：

```bash
./scripts/start_all.sh
```

停止全部服务：

```bash
./scripts/stop_all.sh
```

从主机 Chrome 访问（WSL 场景）：

- 前端：`http://localhost:5173`
- API：`http://localhost:8010`
- vLLM：`http://localhost:8000`

## 参考链接

- vLLM Quickstart: https://docs.vllm.ai/en/latest/getting_started/quickstart/
- vLLM Quantization: https://docs.vllm.ai/en/stable/features/quantization/
- HY-MT1.5-7B-FP8 模型说明页: https://huggingface.co/tencent/HY-MT1.5-7B-FP8
- HY-MT1.5-7B-GPTQ-Int4: https://huggingface.co/tencent/HY-MT1.5-7B-GPTQ-Int4
