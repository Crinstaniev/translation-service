from __future__ import annotations

from dataclasses import dataclass
import os


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    items = [item.strip() for item in value.split(",")]
    return tuple(item for item in items if item)


@dataclass(frozen=True)
class Settings:
    service_host: str = os.getenv("TRANSLATION_SERVICE_HOST", "0.0.0.0")
    service_port: int = int(os.getenv("TRANSLATION_SERVICE_PORT", "8010"))
    vllm_base_url: str = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000")
    vllm_model_name: str = os.getenv("VLLM_MODEL_NAME", "hy-mt15-7b")
    request_timeout_seconds: float = float(os.getenv("VLLM_REQUEST_TIMEOUT_SECONDS", "60"))
    temperature: float = float(os.getenv("TRANSLATION_TEMPERATURE", "0.3"))
    top_p: float = float(os.getenv("TRANSLATION_TOP_P", "0.6"))
    top_k: int = int(os.getenv("TRANSLATION_TOP_K", "20"))
    repetition_penalty: float = float(os.getenv("TRANSLATION_REPETITION_PENALTY", "1.05"))
    translation_history_limit: int = int(os.getenv("TRANSLATION_HISTORY_LIMIT", "500"))
    strict_healthcheck: bool = _env_flag("STRICT_HEALTHCHECK", False)
    cors_allowed_origins: tuple[str, ...] = _env_list(
        "CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )


def get_settings() -> Settings:
    return Settings()
