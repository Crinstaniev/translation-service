#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_pipeline.paddleocr_markdown import (
    default_stem,
    request_layout_parsing,
    save_layout_response,
    serve_local_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Markdown from a PDF using the local PaddleOCR-VL service."
    )
    parser.add_argument("pdf", type=Path, help="Path to the input PDF.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("out/markdown"),
        help="Directory for Markdown, raw JSON, and extracted assets.",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:8080",
        help="PaddleOCR-VL API base URL.",
    )
    parser.add_argument(
        "--container-host",
        default="172.18.0.1",
        help="Host/IP that PaddleOCR containers can use to reach this WSL host.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=1800,
        help="HTTP timeout for PaddleOCR layout parsing.",
    )
    parser.add_argument(
        "--stem",
        help="Output filename stem. Defaults to a sanitized input filename.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf = args.pdf.resolve()
    stem = args.stem or default_stem(pdf)

    print(f"Serving PDF for PaddleOCR: {pdf}")
    with serve_local_file(pdf, container_host=args.container_host) as file_url:
        print(f"Calling PaddleOCR-VL: {args.api_base_url}/layout-parsing")
        print(f"Container file URL: {file_url}")
        response = request_layout_parsing(
            args.api_base_url,
            file_url,
            timeout_seconds=args.timeout_seconds,
        )

    paths = save_layout_response(response, args.output_dir, stem)
    print(f"Markdown: {paths.markdown}")
    print(f"Raw JSON: {paths.raw_json}")
    print(f"Assets: {paths.assets_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
