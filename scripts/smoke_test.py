from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


API_BASE_URL = os.getenv("TRANSLATION_API_BASE_URL", "http://127.0.0.1:8010")
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000")
MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "hy-mt15-7b")


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    try:
        models_payload = request_json(f"{VLLM_BASE_URL}/v1/models")
        model_ids = {item["id"] for item in models_payload.get("data", []) if "id" in item}
        assert_true(MODEL_NAME in model_ids, f"expected model {MODEL_NAME} in {model_ids}")

        health = request_json(f"{API_BASE_URL}/health")
        assert_true(health["status"] in {"ok", "degraded"}, f"unexpected health: {health}")

        samples = [
            {"text": "It's on the house.", "source_lang": "en", "target_lang": "zh"},
            {"text": "これは無料です。", "source_lang": "ja", "target_lang": "zh"},
            {
                "text": "HP +10\\n{name} joined the party.",
                "source_lang": "en",
                "target_lang": "zh",
                "preserve_format": True,
            },
        ]

        for sample in samples:
            response = request_json(f"{API_BASE_URL}/translate", method="POST", payload=sample)
            translation = response.get("translation", "").strip()
            assert_true(bool(translation), f"empty translation for sample: {sample}")

        print("Smoke test passed.")
        return 0
    except (AssertionError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
