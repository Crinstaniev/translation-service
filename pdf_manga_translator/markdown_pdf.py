from __future__ import annotations

import html
import importlib.util
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class RenderOptions:
    title: str = "Translated PDF"
    font_family: str = "Noto Sans CJK SC, Noto Sans CJK, Microsoft YaHei, sans-serif"
    font_size: str = "15px"
    page_size: str = "A4"
    margin: str = "16mm"


@dataclass
class PdfRecord:
    source: str
    html: str
    pdf: str | None
    status: str
    backend: str | None = None
    seconds: float | None = None
    error: str | None = None


def discover_input_markdown(input_root: Path, *, target_lang: str | None = "zh") -> list[Path]:
    suffix = f".{target_lang}.md" if target_lang else ".md"
    return sorted(
        [
            path
            for path in input_root.rglob(f"*{suffix}")
            if path.is_file() and not path.name.startswith(".")
        ],
        key=lambda path: str(path.relative_to(input_root)).casefold(),
    )


def output_pdf_path(source_path: Path, input_root: Path, output_root: Path) -> Path:
    relative = source_path.relative_to(input_root)
    return output_root / relative.with_suffix(".pdf")


def output_html_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".html")


def write_manifest(records: Iterable[PdfRecord], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def convert_markdown_to_pdf(
    source_path: Path,
    pdf_path: Path,
    *,
    input_root: Path,
    asset_root: Path,
    backend: str = "auto",
    html_only: bool = False,
    keep_html: bool = True,
    options: RenderOptions | None = None,
) -> PdfRecord:
    started = time.monotonic()
    options = options or RenderOptions(title=source_path.stem)
    html_path = output_html_path(pdf_path)
    markdown = source_path.read_text(encoding="utf-8")
    rendered_html = markdown_to_html(
        markdown,
        md_path=source_path,
        input_root=input_root,
        asset_root=asset_root,
        options=options,
    )

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(rendered_html, encoding="utf-8")
    if html_only:
        return PdfRecord(
            source=str(source_path),
            html=str(html_path),
            pdf=None,
            status="html",
            seconds=round(time.monotonic() - started, 2),
        )

    selected_backend = select_pdf_backend(backend)
    if selected_backend is None:
        return PdfRecord(
            source=str(source_path),
            html=str(html_path),
            pdf=None,
            status="html_only_no_backend",
            seconds=round(time.monotonic() - started, 2),
            error="No PDF backend found. Install weasyprint or chromium, or rerun with --html-only.",
        )

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    render_pdf_with_backend(html_path, pdf_path, selected_backend)
    if not keep_html:
        html_path.unlink(missing_ok=True)
    return PdfRecord(
        source=str(source_path),
        html=str(html_path) if keep_html else "",
        pdf=str(pdf_path),
        status="done",
        backend=selected_backend,
        seconds=round(time.monotonic() - started, 2),
    )


def markdown_to_html(
    markdown: str,
    *,
    md_path: Path,
    input_root: Path,
    asset_root: Path,
    options: RenderOptions,
) -> str:
    body = "\n".join(_render_blocks(markdown, md_path=md_path, input_root=input_root, asset_root=asset_root))
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(options.title)}</title>
<style>
@page {{
  size: {options.page_size};
  margin: {options.margin};
}}
* {{
  box-sizing: border-box;
}}
body {{
  font-family: {options.font_family};
  color: #111;
  background: #fff;
  font-size: {options.font_size};
  line-height: 1.65;
}}
h1, h2, h3, h4, h5, h6 {{
  break-after: avoid;
  margin: 0 0 0.7em;
  line-height: 1.3;
}}
p {{
  margin: 0 0 0.9em;
  orphans: 2;
  widows: 2;
}}
figure {{
  margin: 0 0 1em;
  break-inside: avoid;
  text-align: center;
}}
img {{
  max-width: 100%;
  height: auto;
}}
pre {{
  white-space: pre-wrap;
  border: 1px solid #ddd;
  padding: 0.8em;
  background: #f7f7f7;
}}
.page-break {{
  break-before: page;
  page-break-before: always;
  height: 0;
}}
.page-break:first-child {{
  break-before: auto;
  page-break-before: auto;
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def select_pdf_backend(requested: str) -> str | None:
    requested = requested.lower()
    if requested == "none":
        return None
    available = _available_backends()
    if requested == "auto":
        for candidate in ("weasyprint-python", "weasyprint", "chromium"):
            if candidate in available:
                return candidate
        return None
    if requested not in {"weasyprint-python", "weasyprint", "chromium"}:
        raise ValueError("--backend must be auto, none, weasyprint-python, weasyprint, or chromium")
    if requested in available:
        return requested
    raise RuntimeError(f"requested PDF backend is not available: {requested}")


def render_pdf_with_backend(html_path: Path, pdf_path: Path, backend: str) -> None:
    if backend == "weasyprint-python":
        from weasyprint import HTML  # type: ignore

        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return
    if backend == "weasyprint":
        subprocess.run(["weasyprint", str(html_path), str(pdf_path)], check=True)
        return
    if backend == "chromium":
        browser = _find_chromium()
        if browser is None:
            raise RuntimeError("chromium backend selected but no chromium executable was found")
        html_target = html_path.resolve().as_uri()
        pdf_target = str(pdf_path)
        if _is_windows_browser(browser):
            html_target = f"file://{_wslpath('-m', html_path)}"
            pdf_target = _wslpath("-w", pdf_path)
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                f"--print-to-pdf={pdf_target}",
                html_target,
            ],
            check=True,
        )
        return
    raise ValueError(f"unsupported PDF backend: {backend}")


def _render_blocks(markdown: str, *, md_path: Path, input_root: Path, asset_root: Path) -> list[str]:
    blocks: list[str] = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False
    fence = ""

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = "\n".join(line.strip() for line in paragraph).strip()
        paragraph.clear()
        if not text:
            return
        rendered = _render_block(text, md_path=md_path, input_root=input_root, asset_root=asset_root)
        if rendered:
            blocks.append(rendered)

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if in_code:
            if stripped.startswith(fence):
                blocks.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
                code = []
                in_code = False
                fence = ""
            else:
                code.append(line)
            continue
        if fence_match:
            flush_paragraph()
            in_code = True
            fence = fence_match.group(1)
            continue
        if not stripped:
            flush_paragraph()
            continue
        if _is_structural_line(stripped):
            flush_paragraph()
            rendered = _render_block(
                stripped,
                md_path=md_path,
                input_root=input_root,
                asset_root=asset_root,
            )
            if rendered:
                blocks.append(rendered)
            continue
        paragraph.append(line)

    if in_code:
        blocks.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
    flush_paragraph()
    return blocks


def _render_block(text: str, *, md_path: Path, input_root: Path, asset_root: Path) -> str:
    page_match = re.fullmatch(r"<!--\s*page:\s*([0-9]+)\s*-->", text, flags=re.IGNORECASE)
    if page_match:
        return f'<div class="page-break" data-page="{html.escape(page_match.group(1))}"></div>'

    image_match = re.fullmatch(r"!\[([^\]]*)]\(([^)]+)\)", text)
    if image_match:
        alt = html.escape(image_match.group(1))
        src = html.escape(resolve_asset_uri(image_match.group(2), md_path, input_root, asset_root))
        return f'<figure><img src="{src}" alt="{alt}"></figure>'

    heading_match = re.fullmatch(r"(#{1,6})\s+(.+)", text)
    if heading_match:
        level = len(heading_match.group(1))
        content = _render_inline(heading_match.group(2))
        return f"<h{level}>{content}</h{level}>"

    if text.lower().startswith("<div") and "<img" in text.lower():
        src_match = re.search(r'src=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
        if src_match:
            src = html.escape(resolve_asset_uri(src_match.group(1), md_path, input_root, asset_root))
            return f'<figure><img src="{src}" alt=""></figure>'

    return f"<p>{_render_inline(text)}</p>"


def _is_structural_line(stripped: str) -> bool:
    if re.fullmatch(r"<!--\s*page:\s*[0-9]+\s*-->", stripped, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"!\[[^\]]*]\([^)]+\)", stripped):
        return True
    if stripped.lower().startswith("<div") and "<img" in stripped.lower():
        return True
    if re.fullmatch(r"#{1,6}\s+.+", stripped):
        return True
    return False


def _render_inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped.replace("\n", "<br>\n")


def resolve_asset_uri(image_ref: str, md_path: Path, input_root: Path, asset_root: Path) -> str:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", image_ref):
        return image_ref
    relative_to_doc = md_path.parent / image_ref
    if relative_to_doc.exists():
        return relative_to_doc.resolve().as_uri()

    try:
        doc_relative_dir = md_path.parent.relative_to(input_root)
        candidate = asset_root / doc_relative_dir / image_ref
        if candidate.exists():
            return candidate.resolve().as_uri()
    except ValueError:
        pass

    return relative_to_doc.resolve().as_uri()


def _available_backends() -> set[str]:
    backends: set[str] = set()
    if importlib.util.find_spec("weasyprint") is not None:
        backends.add("weasyprint-python")
    if shutil.which("weasyprint"):
        backends.add("weasyprint")
    if _find_chromium():
        backends.add("chromium")
    return backends


def _find_chromium() -> str | None:
    for command in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "microsoft-edge",
    ):
        found = shutil.which(command)
        if found:
            return found
    for path in (
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe",
        "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ):
        if Path(path).is_file():
            return path
    return None


def _is_windows_browser(browser: str) -> bool:
    return browser.lower().startswith("/mnt/") and browser.lower().endswith(".exe")


def _wslpath(mode: str, path: Path) -> str:
    return subprocess.check_output(
        ["wslpath", mode, str(path.resolve())],
        text=True,
    ).strip()
