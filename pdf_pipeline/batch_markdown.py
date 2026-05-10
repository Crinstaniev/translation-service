from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from pdf_pipeline.paddleocr_markdown import (
    default_stem,
    request_layout_parsing,
    save_layout_response,
    serve_local_file,
)


@dataclass
class BatchRecord:
    source: str
    stem: str
    status: str
    markdown: str | None = None
    raw_json: str | None = None
    seconds: float | None = None
    error: str | None = None


def discover_pdfs(input_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        ],
        key=lambda path: path.name.casefold(),
    )


def output_stem_for_pdf(pdf: Path) -> str:
    return default_stem(pdf)


def per_pdf_output_dir(output_root: Path, stem: str) -> Path:
    return output_root / stem


def should_skip_pdf(output_dir: Path, stem: str, *, force: bool) -> bool:
    if force:
        return False
    doc_dir = per_pdf_output_dir(output_dir, stem)
    return (
        (doc_dir / f"{stem}.md").is_file()
        and (doc_dir / f"{stem}.paddleocr.json").is_file()
    )


def write_manifest(records: Iterable[BatchRecord], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def convert_pdf_to_markdown(
    pdf: Path,
    output_dir: Path,
    *,
    api_base_url: str,
    container_host: str,
    timeout_seconds: int,
    force: bool,
) -> BatchRecord:
    stem = output_stem_for_pdf(pdf)
    if should_skip_pdf(output_dir, stem, force=force):
        doc_dir = per_pdf_output_dir(output_dir, stem)
        return BatchRecord(
            source=str(pdf),
            stem=stem,
            status="skipped",
            markdown=str(doc_dir / f"{stem}.md"),
            raw_json=str(doc_dir / f"{stem}.paddleocr.json"),
            seconds=0.0,
        )

    started = time.monotonic()
    with serve_local_file(pdf, container_host=container_host) as file_url:
        response = request_layout_parsing(
            api_base_url,
            file_url,
            timeout_seconds=timeout_seconds,
        )
    paths = save_layout_response(response, per_pdf_output_dir(output_dir, stem), stem)
    return BatchRecord(
        source=str(pdf),
        stem=stem,
        status="done",
        markdown=str(paths.markdown),
        raw_json=str(paths.raw_json),
        seconds=round(time.monotonic() - started, 2),
    )
