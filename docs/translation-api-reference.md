# Translation API Reference

## Overview

This service exposes HTTP APIs for multilingual translation backed by `vLLM`.

- Base URL (default): `http://127.0.0.1:8010`
- Content type: `application/json`
- Auth: none (add your own gateway/auth for internet-facing deployments)
- Supported language codes: `zh`, `en`, `ja`, `ko`

Main endpoints:

- `GET /health`
- `GET /metrics/realtime`
- `GET /translations/history`
- `GET /translations/realtime` (SSE monitoring)
- `POST /translate`
- `POST /translate/stream` (SSE streaming)

---

## 1. Health Check

### Request

```bash
curl http://127.0.0.1:8010/health
```

### Response

```json
{
  "status": "ok",
  "model": "hy-mt15-7b",
  "vllm_reachable": true
}
```

Fields:

- `status`: `ok` or `degraded`
- `model`: active model name
- `vllm_reachable`: whether backend vLLM is reachable

---

## 2. Synchronous Translation

### Endpoint

`POST /translate`

### Request Body

```json
{
  "text": "It's on the house.",
  "source_lang": "en",
  "target_lang": "zh",
  "context": "The speaker is a restaurant owner.",
  "terms": [
    { "source": "house", "target": "本店" }
  ],
  "preserve_format": true
}
```

Field constraints:

- `text`: required, non-empty, max `20000` chars
- `source_lang`: required, one of `zh/en/ja/ko`
- `target_lang`: required, one of `zh/en/ja/ko`
- `context`: optional, max `12000` chars
- `terms`: optional array of `{source, target}` pairs
- `preserve_format`: optional boolean, defaults to `true`

### Response

```json
{
  "translation": "这次免单。",
  "model": "hy-mt15-7b",
  "source_lang": "en",
  "target_lang": "zh"
}
```

### cURL Example

```bash
curl http://127.0.0.1:8010/translate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello, welcome.",
    "source_lang": "en",
    "target_lang": "zh",
    "preserve_format": true
  }'
```

---

## 3. Streaming Translation (SSE)

### Endpoint

`POST /translate/stream`

### Protocol

- HTTP response `Content-Type: text/event-stream`
- Event stream contains 4 event types:
  - `start`
  - `delta`
  - `end`
  - `error`

### Request Body

Same schema as `POST /translate`.

### Event Format

#### start

```text
event: start
data: {"model":"hy-mt15-7b","source_lang":"en","target_lang":"zh"}
```

#### delta

```text
event: delta
data: {"chunk":"这次"}
```

```text
event: delta
data: {"chunk":"免单。"}
```

#### end

```text
event: end
data: {"done":true}
```

#### error

```text
event: error
data: {"message":"..."}
```

## 4. Realtime Monitoring

### History

`GET /translations/history?limit=100`

Returns recent in-memory translation records, newest first. The history limit defaults to `500` records and can be changed with `TRANSLATION_HISTORY_LIMIT`.

```json
{
  "records": [
    {
      "id": "7c7d9d9c0f3849a1b59c8c97d79d0c7f",
      "created_at": "2026-04-28T10:20:30.123+00:00",
      "updated_at": "2026-04-28T10:20:31.234+00:00",
      "status": "completed",
      "source_lang": "en",
      "target_lang": "zh",
      "text": "It's on the house.",
      "context": "The speaker is a restaurant owner.",
      "terms": [{ "source": "house", "target": "本店" }],
      "preserve_format": true,
      "translation": "这次免单。",
      "error": null,
      "duration_ms": 1111.2,
      "is_streaming": false
    }
  ]
}
```

### Realtime Events

`GET /translations/realtime`

Server-Sent Events stream for monitoring all translation requests handled by this API. Event types:

- `created`: a translation record was created with `running` status
- `updated`: translation text, status, error, or duration changed
- `heartbeat`: keepalive event

```text
event: updated
data: {"id":"...","status":"completed","translation":"这次免单。"}
```

---

## 5. Python Client Examples

### 5.1 Sync

```python
import requests

base_url = "http://127.0.0.1:8010"
payload = {
    "text": "これは無料です。",
    "source_lang": "ja",
    "target_lang": "zh",
    "preserve_format": True,
}

r = requests.post(f"{base_url}/translate", json=payload, timeout=120)
r.raise_for_status()
print(r.json()["translation"])
```

### 5.2 Streaming (SSE over chunked text)

```python
import json
import requests

base_url = "http://127.0.0.1:8010"
payload = {
    "text": "It's on the house.",
    "source_lang": "en",
    "target_lang": "zh",
    "preserve_format": True,
}

with requests.post(f"{base_url}/translate/stream", json=payload, stream=True, timeout=300) as r:
    r.raise_for_status()
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            if event == "delta":
                print(data.get("chunk", ""), end="", flush=True)
            elif event == "error":
                raise RuntimeError(data.get("message", "streaming failed"))
print()
```

---

## 6. JavaScript Examples

### 6.1 Sync

```javascript
const res = await fetch("http://127.0.0.1:8010/translate", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    text: "Hello world",
    source_lang: "en",
    target_lang: "zh",
    preserve_format: true,
  }),
});

if (!res.ok) throw new Error(`HTTP ${res.status}`);
const data = await res.json();
console.log(data.translation);
```

### 6.2 Streaming

```javascript
const res = await fetch("http://127.0.0.1:8010/translate/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    text: "It's on the house.",
    source_lang: "en",
    target_lang: "zh",
    preserve_format: true,
  }),
});

if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const frames = buffer.split("\n\n");
  buffer = frames.pop() ?? "";

  for (const frame of frames) {
    const lines = frame.split("\n");
    let event = "";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data = line.slice(5).trim();
    }
    if (!data) continue;
    const payload = JSON.parse(data);
    if (event === "delta") process.stdout.write(payload.chunk ?? "");
    if (event === "error") throw new Error(payload.message ?? "streaming error");
  }
}
```

---

## 7. Error Handling

Typical status codes:

- `200`: success
- `422`: request validation error (invalid language code, empty text, etc.)
- `502`: backend vLLM request failed
- `503`: healthcheck fails when strict mode is enabled

Validation error example (`422`):

```json
{
  "detail": [
    {
      "loc": ["body", "target_lang"],
      "msg": "Input should be 'ja', 'en', 'ko' or 'zh'",
      "type": "literal_error"
    }
  ]
}
```

---

## 8. Integration Notes

- For browser integration, ensure frontend origin is included in `CORS_ALLOWED_ORIGINS`.
- For long text, split requests into chunks to reduce timeout risk.
- If exposing publicly, place this service behind:
  - authentication
  - rate limiting
  - request-size limits
