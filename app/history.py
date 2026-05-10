from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Literal
from uuid import uuid4

from app.schemas import TranslateRequest


TranslationStatus = Literal["running", "completed", "failed"]
HistoryEventType = Literal["created", "updated"]


@dataclass(slots=True)
class TranslationRecord:
    id: str
    created_at: str
    updated_at: str
    status: TranslationStatus
    source_lang: str
    target_lang: str
    text: str
    context: str | None
    terms: list[dict[str, str]]
    preserve_format: bool
    translation: str = ""
    error: str | None = None
    duration_ms: float | None = None
    is_streaming: bool = False
    _started_at: float = field(default_factory=time.perf_counter, repr=False)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "text": self.text,
            "context": self.context,
            "terms": self.terms,
            "preserve_format": self.preserve_format,
            "translation": self.translation,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "is_streaming": self.is_streaming,
        }


class TranslationHistory:
    def __init__(self, limit: int = 500) -> None:
        self._limit = max(1, limit)
        self._records: deque[TranslationRecord] = deque(maxlen=self._limit)
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._lock = threading.Lock()

    def create(self, request: TranslateRequest, *, is_streaming: bool) -> dict:
        now = _utc_now()
        record = TranslationRecord(
            id=uuid4().hex,
            created_at=now,
            updated_at=now,
            status="running",
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            text=request.text,
            context=request.context,
            terms=[term.model_dump() for term in request.terms or []],
            preserve_format=request.preserve_format,
            is_streaming=is_streaming,
        )
        with self._lock:
            self._records.append(record)
            payload = record.as_dict()
        self._publish("created", payload)
        return payload

    def append_delta(self, record_id: str, chunk: str) -> dict | None:
        if not chunk:
            return self.get(record_id)
        return self._update(record_id, translation_delta=chunk)

    def complete(self, record_id: str, *, translation: str | None = None) -> dict | None:
        return self._update(record_id, status="completed", translation=translation)

    def fail(self, record_id: str, error: str) -> dict | None:
        return self._update(record_id, status="failed", error=error)

    def list_recent(self, *, limit: int = 100) -> list[dict]:
        clipped_limit = max(1, min(limit, self._limit))
        with self._lock:
            records = list(self._records)[-clipped_limit:]
            return [record.as_dict() for record in reversed(records)]

    def get(self, record_id: str) -> dict | None:
        with self._lock:
            record = self._find_locked(record_id)
            return record.as_dict() if record else None

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def _update(
        self,
        record_id: str,
        *,
        status: TranslationStatus | None = None,
        translation: str | None = None,
        translation_delta: str | None = None,
        error: str | None = None,
    ) -> dict | None:
        with self._lock:
            record = self._find_locked(record_id)
            if record is None:
                return None
            if translation_delta is not None:
                record.translation += translation_delta
            if translation is not None:
                record.translation = translation
            if error is not None:
                record.error = error
            if status is not None:
                record.status = status
                record.duration_ms = round((time.perf_counter() - record._started_at) * 1000, 3)
            record.updated_at = _utc_now()
            payload = record.as_dict()
        self._publish("updated", payload)
        return payload

    def _find_locked(self, record_id: str) -> TranslationRecord | None:
        for record in self._records:
            if record.id == record_id:
                return record
        return None

    def _publish(self, event_type: HistoryEventType, record: dict) -> None:
        event = {"event": event_type, "record": record}
        with self._lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
