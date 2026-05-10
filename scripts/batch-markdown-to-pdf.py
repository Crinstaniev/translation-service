#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_manga_translator.markdown_pdf import (  # noqa: E402
    RenderOptions,
    PdfRecord,
    convert_markdown_to_pdf,
    discover_input_markdown,
    output_pdf_path,
    select_pdf_backend,
    write_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch render translated Markdown files back to printable HTML/PDF."
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        nargs="?",
        default=Path("out/translated"),
        help="Directory containing translated Markdown files.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("out/pdf"),
        help="Directory for rendered HTML/PDF files.",
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=Path("out/markdown"),
        help="Root directory containing OCR assets. Usually the original Markdown output root.",
    )
    parser.add_argument(
        "--target-lang",
        default="zh",
        help="Only process *.{target_lang}.md files. Use --all-markdown to disable.",
    )
    parser.add_argument(
        "--all-markdown",
        action="store_true",
        help="Process every *.md file instead of only translated *.TARGET.md files.",
    )
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "none", "weasyprint-python", "weasyprint", "chromium"],
        help="PDF renderer backend.",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Only write HTML files; do not try to render PDF.",
    )
    parser.add_argument(
        "--no-keep-html",
        action="store_true",
        help="Delete intermediate HTML after successful PDF rendering.",
    )
    parser.add_argument(
        "--page-size",
        default="A4",
        help="CSS @page size, for example A4 or Letter.",
    )
    parser.add_argument(
        "--margin",
        default="16mm",
        help="CSS @page margin.",
    )
    parser.add_argument(
        "--font-size",
        default="15px",
        help="Body font size for rendered HTML/PDF, for example 17px or 12pt.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many Markdown files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Render even if the target PDF already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned work without writing HTML/PDF.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Manifest path. Defaults to OUTPUT_DIR/pdf-manifest.json.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed Markdown file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    asset_root = args.asset_root.resolve()
    manifest_path = (args.manifest or output_dir / "pdf-manifest.json").resolve()

    files = discover_input_markdown(
        input_dir,
        target_lang=None if args.all_markdown else args.target_lang,
    )
    if args.limit is not None:
        files = files[: args.limit]

    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Asset root: {asset_root}")
    print(f"Markdown count: {len(files)}")
    print(f"Backend: {args.backend}")

    records: list[PdfRecord] = []
    if args.dry_run:
        for index, source_path in enumerate(files, start=1):
            pdf_path = output_pdf_path(source_path, input_dir, output_dir)
            status = "would_skip" if pdf_path.exists() and not args.force else "would_render"
            print(f"[{index}/{len(files)}] {status}: {source_path.relative_to(input_dir)}")
            records.append(
                PdfRecord(
                    source=str(source_path),
                    html=str(pdf_path.with_suffix(".html")),
                    pdf=str(pdf_path),
                    status=status,
                    backend=None,
                )
            )
        write_manifest(records, manifest_path)
        print(f"Manifest: {manifest_path}")
        return 0

    if not args.html_only and args.backend == "auto":
        backend = select_pdf_backend("auto")
        if backend is None:
            print(
                "No PDF backend found. HTML will still be written. "
                "Install weasyprint or chromium for PDF output, or use --html-only.",
                flush=True,
            )

    for index, source_path in enumerate(files, start=1):
        pdf_path = output_pdf_path(source_path, input_dir, output_dir)
        relative = source_path.relative_to(input_dir)
        if pdf_path.exists() and not args.force and not args.html_only:
            record = PdfRecord(
                source=str(source_path),
                html=str(pdf_path.with_suffix(".html")),
                pdf=str(pdf_path),
                status="skipped",
                seconds=0.0,
            )
            print(f"[{index}/{len(files)}] skipped: {relative}", flush=True)
            records.append(record)
            write_manifest(records, manifest_path)
            continue

        print(f"[{index}/{len(files)}] rendering: {relative}", flush=True)
        try:
            record = convert_markdown_to_pdf(
                source_path,
                pdf_path,
                input_root=input_dir,
                asset_root=asset_root,
                backend=args.backend,
                html_only=args.html_only,
                keep_html=not args.no_keep_html,
                options=RenderOptions(
                    title=source_path.stem,
                    font_size=args.font_size,
                    page_size=args.page_size,
                    margin=args.margin,
                ),
            )
            print(
                f"[{index}/{len(files)}] {record.status}: {relative} "
                f"backend={record.backend or '-'} ({record.seconds}s)",
                flush=True,
            )
        except Exception as exc:
            record = PdfRecord(
                source=str(source_path),
                html=str(pdf_path.with_suffix(".html")),
                pdf=str(pdf_path),
                status="failed",
                error=str(exc),
            )
            print(f"[{index}/{len(files)}] failed: {relative}: {exc}", flush=True)
            records.append(record)
            write_manifest(records, manifest_path)
            if args.stop_on_error:
                return 1
            continue

        records.append(record)
        write_manifest(records, manifest_path)
        if args.stop_on_error and record.status not in {"done", "html", "skipped"}:
            return 1

    write_manifest(records, manifest_path)
    print(f"Manifest: {manifest_path}")
    failures = sum(
        1
        for record in records
        if record.status not in {"done", "html", "skipped", "would_render", "would_skip"}
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
