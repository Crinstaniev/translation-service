#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_pipeline.markdown_translation import (  # noqa: E402
    DEFAULT_CONTEXT,
    DEFAULT_TERMS,
    BatchTranslateRecord,
    TranslationClient,
    TranslationState,
    discover_markdown_files,
    load_context,
    output_markdown_path,
    translate_markdown_file,
    write_batch_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch translate extracted Markdown files through the local translation API."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        nargs="?",
        default=Path("out/markdown"),
        help="Directory containing extracted Markdown files.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("out/translated"),
        help="Directory for translated Markdown and translation state.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8010",
        help="Translation API base URL.",
    )
    parser.add_argument("--source-lang", default="en", help="Source language code.")
    parser.add_argument("--target-lang", default="zh", help="Target language code.")
    parser.add_argument(
        "--context",
        default=DEFAULT_CONTEXT,
        help="Context sent with every translation request.",
    )
    parser.add_argument(
        "--context-file",
        type=Path,
        help="Read translation context from a UTF-8 text file. Overrides --context.",
    )
    parser.add_argument(
        "--terms",
        type=Path,
        help="Optional JSON file containing a list of {source,target} glossary terms.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Concurrent translation requests within each Markdown file.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Per-request HTTP timeout for the translation API.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        help="State checkpoint path. Defaults to OUTPUT_DIR/translation-state.json.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Manifest path. Defaults to OUTPUT_DIR/translation-manifest.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many Markdown files. Useful for smoke tests.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retranslate units even if matching state entries already exist.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip files whose translated Markdown output already exists.",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Do not call GET /health before translating.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned work without calling the translation API.",
    )
    parser.add_argument(
        "--quiet-preview",
        action="store_true",
        help="Disable live source => translation preview lines.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed Markdown file.",
    )
    return parser.parse_args()


def load_terms(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return DEFAULT_TERMS
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("--terms must point to a JSON array")
    terms: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str) or not isinstance(item.get("target"), str):
            raise ValueError("each term must be an object with string source and target")
        terms.append({"source": item["source"], "target": item["target"]})
    return terms


def main() -> int:
    args = parse_args()
    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    state_path = (args.state or output_dir / "translation-state.json").resolve()
    manifest_path = (args.manifest or output_dir / "translation-manifest.json").resolve()
    terms = load_terms(args.terms)
    context = load_context(args.context_file, default_context=args.context)

    files = discover_markdown_files(input_dir)
    if args.limit is not None:
        files = files[: args.limit]

    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Markdown count: {len(files)}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Translation API: {args.base_url}")

    records: list[BatchTranslateRecord] = []
    if args.dry_run:
        for index, source_path in enumerate(files, start=1):
            output_path = output_markdown_path(
                source_path,
                input_dir,
                output_dir,
                target_lang=args.target_lang,
            )
            status = (
                "would_skip"
                if args.skip_existing and output_path.exists() and not args.force
                else "would_process"
            )
            print(f"[{index}/{len(files)}] {status}: {source_path.relative_to(input_dir)}")
            records.append(
                BatchTranslateRecord(
                    source=str(source_path),
                    output=str(output_path),
                    status=status,
                )
            )
        write_batch_manifest(records, manifest_path)
        print(f"Manifest: {manifest_path}")
        return 0

    client = TranslationClient(args.base_url)
    if not args.skip_health_check:
        health = client.health_check()
        print(f"Health: {json.dumps(health, ensure_ascii=False)}")

    state = TranslationState.load(state_path)
    state_lock = threading.Lock()

    for index, source_path in enumerate(files, start=1):
        output_path = output_markdown_path(
            source_path,
            input_dir,
            output_dir,
            target_lang=args.target_lang,
        )
        relative = source_path.relative_to(input_dir)
        if args.skip_existing and output_path.exists() and not args.force:
            record = BatchTranslateRecord(
                source=str(source_path),
                output=str(output_path),
                status="skipped",
                seconds=0.0,
            )
            print(f"[{index}/{len(files)}] skipped: {relative}", flush=True)
            records.append(record)
            write_batch_manifest(records, manifest_path)
            continue

        print(f"[{index}/{len(files)}] translating: {relative}", flush=True)
        try:
            record = translate_markdown_file(
                source_path,
                output_path,
                translator=client,
                state=state,
                state_lock=state_lock,
                state_path=state_path,
                source_lang=args.source_lang,
                target_lang=args.target_lang,
                context=context,
                terms=terms,
                concurrency=args.concurrency,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
                preview=not args.quiet_preview,
            )
            print(
                f"[{index}/{len(files)}] {record.status}: {relative} "
                f"units={record.total_units} translated={record.translated_units} "
                f"reused={record.reused_units} failed={record.failed_units} "
                f"({record.seconds}s)",
                flush=True,
            )
        except Exception as exc:
            record = BatchTranslateRecord(
                source=str(source_path),
                output=str(output_path),
                status="failed",
                error=str(exc),
            )
            print(f"[{index}/{len(files)}] failed: {relative}: {exc}", flush=True)
            records.append(record)
            write_batch_manifest(records, manifest_path)
            if args.stop_on_error:
                return 1
            continue

        records.append(record)
        write_batch_manifest(records, manifest_path)
        if args.stop_on_error and record.status == "failed":
            return 1

    state.save(state_path)
    write_batch_manifest(records, manifest_path)
    print(f"State: {state_path}")
    print(f"Manifest: {manifest_path}")
    failures = sum(1 for record in records if record.status == "failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
