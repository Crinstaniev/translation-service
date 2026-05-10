#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_pipeline.batch_markdown import (  # noqa: E402
    BatchRecord,
    convert_pdf_to_markdown,
    discover_pdfs,
    output_stem_for_pdf,
    should_skip_pdf,
    write_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch extract Markdown from every PDF in a directory."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        nargs="?",
        default=Path("input/pdfs"),
        help="Directory containing PDFs.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("out/markdown"),
        help="Directory for Markdown, raw JSON, assets, and manifest.",
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
        help="Per-PDF HTTP timeout for PaddleOCR layout parsing.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many PDFs. Useful for smoke tests.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess PDFs even if Markdown and raw JSON already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned work without calling PaddleOCR.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Manifest path. Defaults to OUTPUT_DIR/batch-manifest.json.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed PDF.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of PDFs to process in parallel. Keep 1 for safest GPU memory use; try 2 after smoke tests.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    manifest_path = args.manifest or output_dir / "batch-manifest.json"

    pdfs = discover_pdfs(input_dir)
    if args.limit is not None:
        pdfs = pdfs[: args.limit]

    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")

    print(f"PDF count: {len(pdfs)}")
    print(f"Concurrency: {args.concurrency}")

    records: list[BatchRecord] = []
    records_lock = threading.Lock()

    def append_record(record: BatchRecord) -> None:
        with records_lock:
            records.append(record)
            write_manifest(records, manifest_path)

    def process_one(index: int, pdf: Path) -> BatchRecord:
        stem = output_stem_for_pdf(pdf)
        if args.dry_run:
            status = "would_skip" if should_skip_pdf(output_dir, stem, force=args.force) else "would_process"
            return BatchRecord(source=str(pdf), stem=stem, status=status)

        return convert_pdf_to_markdown(
            pdf,
            output_dir,
            api_base_url=args.api_base_url,
            container_host=args.container_host,
            timeout_seconds=args.timeout_seconds,
            force=args.force,
        )

    if args.dry_run:
        for index, pdf in enumerate(pdfs, start=1):
            record = process_one(index, pdf)
            status = record.status
            print(f"[{index}/{len(pdfs)}] {status}: {pdf.name}")
            records.append(record)
        write_manifest(records, manifest_path)
        print(f"Manifest: {manifest_path}")
        return 0

    if args.concurrency == 1:
        for index, pdf in enumerate(pdfs, start=1):
            stem = output_stem_for_pdf(pdf)
            print(f"[{index}/{len(pdfs)}] processing: {pdf.name}", flush=True)
            try:
                record = process_one(index, pdf)
                print(f"[{index}/{len(pdfs)}] {record.status}: {pdf.name} ({record.seconds}s)", flush=True)
            except Exception as exc:
                record = BatchRecord(
                    source=str(pdf),
                    stem=stem,
                    status="failed",
                    error=str(exc),
                )
                print(f"[{index}/{len(pdfs)}] failed: {pdf.name}: {exc}", flush=True)
                append_record(record)
                if args.stop_on_error:
                    return 1
                continue

            append_record(record)
        print(f"Manifest: {manifest_path}")
        failures = sum(1 for record in records if record.status == "failed")
        return 1 if failures else 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {}
        for index, pdf in enumerate(pdfs, start=1):
            print(f"[{index}/{len(pdfs)}] queued: {pdf.name}", flush=True)
            futures[executor.submit(process_one, index, pdf)] = (index, pdf)

        for future in as_completed(futures):
            index, pdf = futures[future]
            stem = output_stem_for_pdf(pdf)
            try:
                record = future.result()
                print(f"[{index}/{len(pdfs)}] {record.status}: {pdf.name} ({record.seconds}s)", flush=True)
            except Exception as exc:
                record = BatchRecord(
                    source=str(pdf),
                    stem=stem,
                    status="failed",
                    error=str(exc),
                )
                print(f"[{index}/{len(pdfs)}] failed: {pdf.name}: {exc}", flush=True)
                append_record(record)
                if args.stop_on_error:
                    for pending in futures:
                        pending.cancel()
                    return 1
                continue

            append_record(record)

    write_manifest(records, manifest_path)
    print(f"Manifest: {manifest_path}")
    failures = sum(1 for record in records if record.status == "failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
