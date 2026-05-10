from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import time

from fastapi import FastAPI, HTTPException
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn

from app.config import Settings, get_settings
from app.history import TranslationHistory
from app.metrics import MetricsCollector
from app.prompting import PromptInput, PromptTerm, build_translation_prompt
from app.schemas import HealthResponse, TranslateRequest, TranslateResponse
from app.vllm_client import TranslationParams, VLLMClient, VLLMError


def build_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    client = VLLMClient(
        base_url=runtime_settings.vllm_base_url,
        timeout_seconds=runtime_settings.request_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="Translation Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = runtime_settings
    app.state.vllm_client = client
    app.state.metrics = MetricsCollector()
    app.state.history = TranslationHistory(limit=runtime_settings.translation_history_limit)

    @app.middleware("http")
    async def record_metrics(request: Request, call_next):
        if request.url.path in {
            "/health",
            "/metrics/realtime",
            "/translations/history",
            "/translations/realtime",
        }:
            return await call_next(request)

        metrics: MetricsCollector = request.app.state.metrics
        metrics.track_request_start()
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            metrics.track_request_end(status_code=500, duration_ms=duration_ms)
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        metrics.track_request_end(status_code=response.status_code, duration_ms=duration_ms)
        return response

    def build_params(request: TranslateRequest) -> TranslationParams:
        prompt = build_translation_prompt(
            PromptInput(
                text=request.text,
                source_lang=request.source_lang,
                target_lang=request.target_lang,
                context=request.context,
                terms=tuple(PromptTerm(source=t.source, target=t.target) for t in request.terms or []),
                preserve_format=request.preserve_format,
            )
        )
        return TranslationParams(
            model=runtime_settings.vllm_model_name,
            prompt=prompt,
            temperature=runtime_settings.temperature,
            top_p=runtime_settings.top_p,
            top_k=runtime_settings.top_k,
            repetition_penalty=runtime_settings.repetition_penalty,
        )

    def sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        reachable = await client.is_reachable(runtime_settings.vllm_model_name)
        if runtime_settings.strict_healthcheck and not reachable:
            raise HTTPException(status_code=503, detail="vLLM is unreachable")
        return HealthResponse(
            status="ok" if reachable else "degraded",
            model=runtime_settings.vllm_model_name,
            vllm_reachable=reachable,
        )

    @app.get("/metrics/realtime")
    async def realtime_metrics(window: int = 10) -> dict:
        metrics: MetricsCollector = app.state.metrics
        return metrics.snapshot(window_seconds=window)

    @app.get("/translations/history")
    async def translation_history(limit: int = 100) -> dict:
        history: TranslationHistory = app.state.history
        return {"records": history.list_recent(limit=limit)}

    @app.get("/translations/realtime")
    async def translation_realtime(request: Request) -> StreamingResponse:
        history: TranslationHistory = app.state.history
        queue = history.subscribe()

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield sse_event("heartbeat", {"ok": True})
                        continue
                    yield sse_event(payload["event"], payload["record"])
            finally:
                history.unsubscribe(queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/translate", response_model=TranslateResponse)
    async def translate(request: TranslateRequest) -> TranslateResponse:
        metrics: MetricsCollector = app.state.metrics
        history: TranslationHistory = app.state.history
        record = history.create(request, is_streaming=False)
        translation_started_at = metrics.track_translation_start(input_chars=len(request.text))
        try:
            translation = await client.translate(build_params(request))
        except VLLMError as exc:
            history.fail(record["id"], str(exc))
            metrics.track_translation_end(
                translation_started_at,
                success=False,
                output_chars=0,
                is_streaming=False,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            history.fail(record["id"], str(exc))
            metrics.track_translation_end(
                translation_started_at,
                success=False,
                output_chars=0,
                is_streaming=False,
            )
            raise

        metrics.track_translation_end(
            translation_started_at,
            success=True,
            output_chars=len(translation),
            is_streaming=False,
        )
        history.complete(record["id"], translation=translation)

        return TranslateResponse(
            translation=translation,
            model=runtime_settings.vllm_model_name,
            source_lang=request.source_lang,
            target_lang=request.target_lang,
        )

    @app.post("/translate/stream")
    async def translate_stream(request: TranslateRequest) -> StreamingResponse:
        params = build_params(request)
        metrics: MetricsCollector = app.state.metrics
        history: TranslationHistory = app.state.history
        record = history.create(request, is_streaming=True)
        translation_started_at = metrics.track_translation_start(input_chars=len(request.text))

        async def event_stream():
            output_parts: list[str] = []
            yield sse_event(
                "start",
                {
                    "model": runtime_settings.vllm_model_name,
                    "source_lang": request.source_lang,
                    "target_lang": request.target_lang,
                },
            )
            try:
                async for chunk in client.stream_translate(params):
                    output_parts.append(chunk)
                    history.append_delta(record["id"], chunk)
                    yield sse_event("delta", {"chunk": chunk})
                metrics.track_translation_end(
                    translation_started_at,
                    success=True,
                    output_chars=len("".join(output_parts)),
                    is_streaming=True,
                )
                history.complete(record["id"])
                yield sse_event("end", {"done": True})
            except VLLMError as exc:
                history.fail(record["id"], str(exc))
                metrics.track_translation_end(
                    translation_started_at,
                    success=False,
                    output_chars=0,
                    is_streaming=True,
                )
                yield sse_event("error", {"message": str(exc)})
            except Exception as exc:
                history.fail(record["id"], str(exc))
                metrics.track_translation_end(
                    translation_started_at,
                    success=False,
                    output_chars=0,
                    is_streaming=True,
                )
                yield sse_event("error", {"message": str(exc)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = build_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.service_host,
        port=settings.service_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
