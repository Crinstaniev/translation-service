from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


SupportedSourceLang = Literal["ja", "en", "ko", "zh"]
SupportedTargetLang = Literal["ja", "en", "ko", "zh"]


class TermMapping(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    source_lang: SupportedSourceLang
    target_lang: SupportedTargetLang = "zh"
    context: str | None = Field(default=None, max_length=12000)
    terms: list[TermMapping] | None = None
    preserve_format: bool = True

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned

    @field_validator("context")
    @classmethod
    def normalize_context(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TranslateResponse(BaseModel):
    translation: str
    model: str
    source_lang: SupportedSourceLang
    target_lang: SupportedTargetLang


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model: str
    vllm_reachable: bool
