from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

import requests


MAX_TRANSLATION_CHARS = 20_000
DEFAULT_CONTEXT = (
    "Markdown prose extracted from an illustrated PDF. Translate naturally from "
    "English to Chinese while preserving markdown formatting, image references, "
    "page boundaries, placeholders, inline math, and HTML-like tags."
)
DEFAULT_TERMS = [
    {"source": "chapter", "target": "章节"},
    {"source": "figure", "target": "插图"},
    {"source": "caption", "target": "图注"},
]


class Translator(Protocol):
    def translate(
        self,
        *,
        text: str,
        source_lang: str,
        target_lang: str,
        context: str,
        terms: list[dict[str, str]],
        timeout_seconds: int,
    ) -> str:
        ...


@dataclass
class MarkdownBlock:
    text: str
    translatable: bool
    kind: str
    prefix: str = ""
    suffix: str = ""
    unit_index: int | None = None
    translated_text: str | None = None
    error: str | None = None
    reused: bool = False


@dataclass
class TranslateResult:
    markdown: str
    total_units: int
    translated_units: int
    reused_units: int
    failed_units: int


@dataclass
class BatchTranslateRecord:
    source: str
    output: str
    status: str
    total_units: int = 0
    translated_units: int = 0
    reused_units: int = 0
    failed_units: int = 0
    seconds: float | None = None
    error: str | None = None


@dataclass
class TranslationState:
    version: int = 1
    units: dict[str, dict[str, object]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "TranslationState":
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(version=int(data.get("version", 1)), units=dict(data.get("units", {})))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": self.version, "units": self.units},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def get_done(self, unit_id: str, source_hash: str, *, force: bool = False) -> str | None:
        if force:
            return None
        record = self.units.get(unit_id)
        if (
            record
            and record.get("status") == "done"
            and record.get("source_hash") == source_hash
            and isinstance(record.get("translation"), str)
        ):
            return str(record["translation"])
        return None

    def mark_done(
        self,
        unit_id: str,
        *,
        source_hash: str,
        translation: str,
        kind: str,
    ) -> None:
        self.units[unit_id] = {
            "source_hash": source_hash,
            "status": "done",
            "translation": translation,
            "kind": kind,
            "updated_at": _now_iso(),
        }

    def mark_failed(
        self,
        unit_id: str,
        *,
        source_hash: str,
        error: str,
        kind: str,
    ) -> None:
        self.units[unit_id] = {
            "source_hash": source_hash,
            "status": "failed",
            "error": error,
            "kind": kind,
            "updated_at": _now_iso(),
        }


class TranslationClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8010"):
        self.base_url = base_url.rstrip("/")

    def health_check(self, timeout_seconds: int = 10) -> dict[str, object]:
        response = requests.get(f"{self.base_url}/health", timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if data.get("status") not in {"ok", "degraded"}:
            raise RuntimeError(f"translation service health is not ok: {data}")
        return data

    def translate(
        self,
        *,
        text: str,
        source_lang: str,
        target_lang: str,
        context: str,
        terms: list[dict[str, str]],
        timeout_seconds: int,
    ) -> str:
        response = requests.post(
            f"{self.base_url}/translate",
            json={
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "context": context,
                "terms": terms,
                "preserve_format": True,
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        translation = data.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise RuntimeError(f"translation response missing non-empty translation: {data}")
        return translation


def discover_markdown_files(input_root: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in input_root.rglob("*.md")
            if path.is_file() and not path.name.endswith(".zh.md")
        ],
        key=lambda path: str(path.relative_to(input_root)).casefold(),
    )


def load_context(path: Path | None, *, default_context: str = DEFAULT_CONTEXT) -> str:
    if path is None:
        return default_context
    context = path.read_text(encoding="utf-8").strip()
    if not context:
        raise ValueError(f"context file is empty: {path}")
    return context


def output_markdown_path(
    source_path: Path,
    input_root: Path,
    output_root: Path,
    *,
    target_lang: str,
) -> Path:
    relative = source_path.relative_to(input_root)
    return output_root / relative.with_name(f"{source_path.stem}.{target_lang}.md")


def write_batch_manifest(records: Iterable[BatchTranslateRecord], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def translate_markdown_file(
    source_path: Path,
    output_path: Path,
    *,
    translator: Translator,
    state: TranslationState,
    state_lock: threading.Lock | None,
    state_path: Path | None,
    source_lang: str,
    target_lang: str,
    context: str = DEFAULT_CONTEXT,
    terms: list[dict[str, str]] | None = None,
    concurrency: int = 8,
    timeout_seconds: int = 120,
    force: bool = False,
    preview: bool = True,
) -> BatchTranslateRecord:
    started = time.monotonic()
    source_text = source_path.read_text(encoding="utf-8")
    result = translate_markdown_text(
        source_text,
        doc_id=str(source_path),
        translator=translator,
        state=state,
        state_lock=state_lock,
        state_path=state_path,
        source_lang=source_lang,
        target_lang=target_lang,
        context=context,
        terms=terms,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        force=force,
        preview=preview,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.markdown, encoding="utf-8")
    status = "failed" if result.failed_units else "done"
    return BatchTranslateRecord(
        source=str(source_path),
        output=str(output_path),
        status=status,
        total_units=result.total_units,
        translated_units=result.translated_units,
        reused_units=result.reused_units,
        failed_units=result.failed_units,
        seconds=round(time.monotonic() - started, 2),
    )


def translate_markdown_text(
    markdown: str,
    *,
    doc_id: str,
    translator: Translator,
    state: TranslationState,
    source_lang: str,
    target_lang: str,
    state_lock: threading.Lock | None = None,
    state_path: Path | None = None,
    context: str = DEFAULT_CONTEXT,
    terms: list[dict[str, str]] | None = None,
    concurrency: int = 8,
    timeout_seconds: int = 120,
    force: bool = False,
    preview: bool = False,
) -> TranslateResult:
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")

    blocks = split_markdown_blocks(markdown)
    units = [block for block in blocks if block.translatable]
    for index, block in enumerate(units, start=1):
        block.unit_index = index

    translated_count = 0
    reused_count = 0
    failed_count = 0
    terms = terms if terms is not None else DEFAULT_TERMS
    lock = state_lock or threading.Lock()

    def translate_block(block: MarkdownBlock) -> MarkdownBlock:
        assert block.unit_index is not None
        source_text = block.text
        if len(source_text) > MAX_TRANSLATION_CHARS:
            raise ValueError(
                f"unit exceeds translation API limit: {len(source_text)} > {MAX_TRANSLATION_CHARS}"
            )

        unit_id = f"{doc_id}#u{block.unit_index:05d}"
        source_hash = stable_hash(source_text)
        with lock:
            cached = state.get_done(unit_id, source_hash, force=force)
        if cached is not None:
            block.translated_text = cached
            block.reused = True
            return block

        protected_text, restore, protected_tokens = protect_fragile_tokens(source_text)
        translated = translator.translate(
            text=protected_text,
            source_lang=source_lang,
            target_lang=target_lang,
            context=context_for_kind(block.kind, source_context=context),
            terms=terms,
            timeout_seconds=timeout_seconds,
        )
        validate_translation(protected_text, translated, protected_tokens)
        block.translated_text = restore(translated)
        with lock:
            state.mark_done(
                unit_id,
                source_hash=source_hash,
                translation=block.translated_text,
                kind=block.kind,
            )
            if state_path is not None:
                state.save(state_path)
        if preview:
            print(
                f"[done] {unit_id} {_preview(source_text)} => {_preview(block.translated_text)}",
                flush=True,
            )
        return block

    if concurrency == 1:
        for block in units:
            try:
                translate_block(block)
            except Exception as exc:
                _mark_block_failed(block, doc_id, state, lock, state_path, force, str(exc))
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(translate_block, block): block for block in units}
            for future in as_completed(futures):
                block = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    _mark_block_failed(block, doc_id, state, lock, state_path, force, str(exc))

    for block in units:
        if block.translated_text is not None:
            if block.reused:
                reused_count += 1
            else:
                translated_count += 1
        else:
            failed_count += 1

    return TranslateResult(
        markdown=render_markdown_blocks(blocks),
        total_units=len(units),
        translated_units=translated_count,
        reused_units=reused_count,
        failed_units=failed_count,
    )


def split_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    lines = markdown.splitlines(keepends=True)
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    in_fence = False
    fence_marker = ""
    fence_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = "".join(paragraph)
        stripped = text.strip()
        paragraph.clear()
        if not stripped:
            blocks.append(MarkdownBlock(text=text, translatable=False, kind="blank"))
            return
        if _is_preserved_block(stripped):
            blocks.append(MarkdownBlock(text=text, translatable=False, kind="preserved"))
            return
        heading = re.match(r"^(#{1,6}\s+)(.*?)(\s*)$", stripped, flags=re.DOTALL)
        if heading and "\n" not in stripped:
            blocks.append(
                MarkdownBlock(
                    text=heading.group(2),
                    translatable=bool(heading.group(2).strip()),
                    kind="heading",
                    prefix=heading.group(1),
                    suffix=heading.group(3),
                )
            )
            return
        blocks.append(MarkdownBlock(text=text.strip("\n"), translatable=True, kind="paragraph"))

    for line in lines:
        stripped_line = line.strip()
        fence_match = re.match(r"^(```+|~~~+)", stripped_line)
        if in_fence:
            fence_lines.append(line)
            if stripped_line.startswith(fence_marker):
                blocks.append(
                    MarkdownBlock(
                        text="".join(fence_lines),
                        translatable=False,
                        kind="code_fence",
                    )
                )
                fence_lines = []
                in_fence = False
                fence_marker = ""
            continue

        if fence_match:
            flush_paragraph()
            in_fence = True
            fence_marker = fence_match.group(1)
            fence_lines = [line]
            continue

        if not stripped_line:
            flush_paragraph()
            blocks.append(MarkdownBlock(text=line, translatable=False, kind="blank"))
            continue

        if _is_translation_structural_line(stripped_line):
            flush_paragraph()
            if _is_preserved_block(stripped_line):
                blocks.append(MarkdownBlock(text=line, translatable=False, kind="preserved"))
                continue
            heading = re.match(r"^(#{1,6}\s+)(.*?)(\s*)$", stripped_line)
            if heading:
                blocks.append(
                    MarkdownBlock(
                        text=heading.group(2),
                        translatable=bool(heading.group(2).strip()),
                        kind="heading",
                        prefix=heading.group(1),
                        suffix=heading.group(3),
                    )
                )
                continue

        paragraph.append(line)

    if in_fence:
        blocks.append(MarkdownBlock(text="".join(fence_lines), translatable=False, kind="code_fence"))
    flush_paragraph()
    return blocks


def render_markdown_blocks(blocks: list[MarkdownBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.translatable:
            translated = block.translated_text if block.translated_text is not None else block.text
            parts.append(f"{block.prefix}{translated}{block.suffix}")
        else:
            parts.append(block.text)
    return "".join(parts)


def protect_fragile_tokens(text: str) -> tuple[str, callable, list[str]]:
    token_map: dict[str, str] = {}
    patterns = [
        r"<[^>\n]+>",
        r"\$[^$\n]+\$",
        r"\{[A-Za-z0-9_.:-]+\}",
        r"%\([A-Za-z0-9_]+\)[sd]",
        r"%[sd]",
        r"\\n",
    ]
    combined = re.compile("|".join(f"({pattern})" for pattern in patterns))

    def replace(match: re.Match[str]) -> str:
        token = f"__FMT{len(token_map)}__"
        token_map[token] = match.group(0)
        return token

    protected = combined.sub(replace, text)

    def restore(translated: str) -> str:
        restored = translated
        for token, original in token_map.items():
            restored = restored.replace(token, original)
        return restored

    return protected, restore, list(token_map)


def validate_translation(source_text: str, translated_text: str, protected_tokens: list[str]) -> None:
    if not translated_text.strip():
        raise ValueError("translation is empty")
    missing = [token for token in protected_tokens if token not in translated_text]
    if missing:
        raise ValueError(f"translation dropped protected tokens: {', '.join(missing)}")
    if source_text.count("\n") != translated_text.count("\n"):
        raise ValueError("translation changed newline count")


def context_for_kind(kind: str, *, source_context: str) -> str:
    if kind == "heading":
        return f"{source_context} This unit is a markdown heading; keep it concise."
    return source_context


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_preserved_block(stripped: str) -> bool:
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return True
    if re.fullmatch(r"!\[[^\]]*]\([^)]+\)", stripped):
        return True
    lowered = stripped.lower()
    if "<img" in lowered:
        return True
    if lowered.startswith("<div") and lowered.endswith("</div>") and "<img" in lowered:
        return True
    return False


def _is_translation_structural_line(stripped: str) -> bool:
    if _is_preserved_block(stripped):
        return True
    return bool(re.fullmatch(r"#{1,6}\s+.+", stripped))


def _mark_block_failed(
    block: MarkdownBlock,
    doc_id: str,
    state: TranslationState,
    lock: threading.Lock,
    state_path: Path | None,
    force: bool,
    error: str,
) -> None:
    assert block.unit_index is not None
    source_hash = stable_hash(block.text)
    unit_id = f"{doc_id}#u{block.unit_index:05d}"
    block.error = error
    if force:
        return
    with lock:
        state.mark_failed(unit_id, source_hash=source_hash, error=error, kind=block.kind)
        if state_path is not None:
            state.save(state_path)


def _preview(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
