from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_app


class _StubVLLMHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self.send_error(404)
            return
        body = json.dumps({"data": [{"id": "hy-mt15-7b"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        prompt = payload["messages"][0]["content"]

        if "It's on the house." in prompt:
            translation = "这次免单。"
        elif "これは無料です。" in prompt:
            translation = "这是免费的。"
        else:
            translation = "翻译结果"

        if payload.get("stream") is True:
            body_chunks = [
                f'data: {json.dumps({"choices": [{"delta": {"content": translation[:2]}}]})}\n\n',
                f'data: {json.dumps({"choices": [{"delta": {"content": translation[2:]}}]})}\n\n',
                "data: [DONE]\n\n",
            ]
            body = "".join(body_chunks).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = json.dumps(
            {"choices": [{"message": {"content": translation}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _StubVLLMHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address

        settings = Settings(
            vllm_base_url=f"http://{host}:{port}",
            vllm_model_name="hy-mt15-7b",
            strict_healthcheck=True,
        )
        cls.client = TestClient(build_app(settings))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=1)

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_translate_returns_translation(self) -> None:
        response = self.client.post(
            "/translate",
            json={
                "text": "It's on the house.",
                "source_lang": "en",
                "target_lang": "zh",
                "preserve_format": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["translation"], "这次免单。")
        self.assertEqual(payload["model"], "hy-mt15-7b")

    def test_translate_rejects_invalid_target_lang(self) -> None:
        response = self.client.post(
            "/translate",
            json={
                "text": "これは無料です。",
                "source_lang": "ja",
                "target_lang": "fr",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_translate_stream_returns_sse_events(self) -> None:
        with self.client.stream(
            "POST",
            "/translate/stream",
            json={
                "text": "It's on the house.",
                "source_lang": "en",
                "target_lang": "zh",
                "preserve_format": True,
            },
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())

        self.assertIn("event: start", body)
        self.assertGreaterEqual(body.count("event: delta"), 2)
        self.assertIn('"chunk": "这次"', body)
        self.assertIn('"chunk": "免单。"', body)
        self.assertIn("event: end", body)

    def test_realtime_metrics_returns_throughput_snapshot(self) -> None:
        self.client.post(
            "/translate",
            json={
                "text": "It's on the house.",
                "source_lang": "en",
                "target_lang": "zh",
                "preserve_format": True,
            },
        )

        response = self.client.get("/metrics/realtime?window=60")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertIn("rates", payload)
        self.assertIn("latency_ms", payload)
        self.assertGreaterEqual(payload["window_totals"]["translations_completed"], 1)
        self.assertGreaterEqual(payload["lifetime_totals"]["requests"], 1)

    def test_translation_history_records_sync_translation(self) -> None:
        response = self.client.post(
            "/translate",
            json={
                "text": "It's on the house.",
                "source_lang": "en",
                "target_lang": "zh",
                "context": "The speaker owns the restaurant.",
                "terms": [{"source": "house", "target": "本店"}],
                "preserve_format": True,
            },
        )
        self.assertEqual(response.status_code, 200)

        history_response = self.client.get("/translations/history?limit=1")
        self.assertEqual(history_response.status_code, 200)
        records = history_response.json()["records"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["text"], "It's on the house.")
        self.assertEqual(record["translation"], "这次免单。")
        self.assertEqual(record["context"], "The speaker owns the restaurant.")
        self.assertEqual(record["terms"], [{"source": "house", "target": "本店"}])
        self.assertFalse(record["is_streaming"])
        self.assertIsNotNone(record["duration_ms"])

    def test_translation_history_records_streaming_translation(self) -> None:
        with self.client.stream(
            "POST",
            "/translate/stream",
            json={
                "text": "これは無料です。",
                "source_lang": "ja",
                "target_lang": "zh",
                "preserve_format": True,
            },
        ) as response:
            self.assertEqual(response.status_code, 200)
            _ = "".join(response.iter_text())

        history_response = self.client.get("/translations/history?limit=1")
        self.assertEqual(history_response.status_code, 200)
        record = history_response.json()["records"][0]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["text"], "これは無料です。")
        self.assertEqual(record["translation"], "这是免费的。")
        self.assertTrue(record["is_streaming"])
        self.assertIsNone(record["error"])


if __name__ == "__main__":
    unittest.main()
