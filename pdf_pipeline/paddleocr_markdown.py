from __future__ import annotations

import base64
import contextlib
import functools
import http.server
import json
import mimetypes
import re
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import requests


DEFAULT_IGNORE_LABELS = [
    "number",
    "footnote",
    "header",
    "header_image",
    "footer",
    "footer_image",
    "aside_text",
]


@dataclass(frozen=True)
class SavedLayoutPaths:
    raw_json: Path
    markdown: Path
    assets_dir: Path


@dataclass(frozen=True)
class LocalFileServer:
    url: str
    shutdown: Any


def build_layout_parsing_payload(file_url: str) -> dict[str, Any]:
    return {
        "file": file_url,
        "fileType": 0,
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useLayoutDetection": True,
        "useChartRecognition": False,
        "useSealRecognition": False,
        "useOcrForImageBlock": False,
        "formatBlockContent": False,
        "mergeLayoutBlocks": True,
        "markdownIgnoreLabels": DEFAULT_IGNORE_LABELS,
        "prettifyMarkdown": True,
        "restructurePages": False,
        "mergeTables": True,
        "relevelTitles": True,
    }


def request_layout_parsing(
    api_base_url: str,
    file_url: str,
    *,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    endpoint = api_base_url.rstrip("/") + "/layout-parsing"
    response = requests.post(
        endpoint,
        json=build_layout_parsing_payload(file_url),
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errorCode", 0) != 0:
        raise RuntimeError(
            f"PaddleOCR layout parsing failed: {payload.get('errorMsg', payload)}"
        )
    return payload


def combine_markdown_pages(response: dict[str, Any]) -> str:
    pages = response["result"]["layoutParsingResults"]
    chunks: list[str] = []
    for index, page in enumerate(pages, start=1):
        text = page.get("markdown", {}).get("text", "").strip()
        chunks.append(f"<!-- page: {index} -->\n\n{text}".rstrip())
    return "\n\n".join(chunks).rstrip() + "\n"


def save_layout_response(
    response: dict[str, Any],
    output_dir: Path,
    stem: str,
) -> SavedLayoutPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / f"{stem}_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    raw_json = output_dir / f"{stem}.paddleocr.json"
    markdown_path = output_dir / f"{stem}.md"

    raw_json.write_text(
        json.dumps(response, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    markdown = combine_markdown_pages(response)
    image_rewrites: dict[str, str] = {}
    for page in response["result"]["layoutParsingResults"]:
        images = page.get("markdown", {}).get("images") or {}
        for name, content in images.items():
            safe_name = _safe_asset_name(name)
            asset_path = assets_dir / safe_name
            asset_path.write_bytes(_decode_image_content(content))
            image_rewrites[name] = f"{assets_dir.name}/{safe_name}"

    for original, replacement in image_rewrites.items():
        markdown = markdown.replace(original, replacement)

    markdown_path.write_text(markdown, encoding="utf-8")
    return SavedLayoutPaths(raw_json=raw_json, markdown=markdown_path, assets_dir=assets_dir)


def _decode_image_content(content: str) -> bytes:
    if content.startswith("data:"):
        _, encoded = content.split(",", 1)
        return base64.b64decode(encoded)
    return base64.b64decode(content)


def _safe_asset_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "image.bin"


@contextlib.contextmanager
def serve_local_file(
    path: Path,
    *,
    container_host: str = "172.18.0.1",
) -> Iterator[str]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    handler = functools.partial(
        QuietSimpleHTTPRequestHandler,
        directory=str(path.parent),
    )
    server = http.server.ThreadingHTTPServer(("0.0.0.0", _free_port()), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{container_host}:{server.server_port}/{quote(path.name)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        return int(sock.getsockname()[1])


class QuietSimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def default_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "document"
