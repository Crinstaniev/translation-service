import tempfile
import unittest
from pathlib import Path

from pdf_manga_translator.markdown_pdf import (
    RenderOptions,
    discover_input_markdown,
    markdown_to_html,
    output_pdf_path,
)


class MarkdownPdfTests(unittest.TestCase):
    def test_markdown_to_html_preserves_pages_and_resolves_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = root / "zh" / "Book" / "Book.zh.md"
            asset_root = root / "markdown"
            image_path = asset_root / "Book" / "Book_assets" / "cover.jpg"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"jpg")
            md_path.parent.mkdir(parents=True)

            html = markdown_to_html(
                """<!-- page: 1 -->

# 标题

![cover](Book_assets/cover.jpg)

正文 <keep>.
""",
                md_path=md_path,
                input_root=root / "zh",
                asset_root=asset_root,
                options=RenderOptions(title="Book"),
            )

        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn('class="page-break"', html)
        self.assertIn("<h1>标题</h1>", html)
        self.assertIn(image_path.resolve().as_uri(), html)
        self.assertIn("<p>正文 &lt;keep&gt;.</p>", html)

    def test_markdown_to_html_splits_structural_lines_without_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = root / "zh" / "Book" / "Book.zh.md"
            asset_root = root / "markdown"
            image_path = asset_root / "Book" / "Book_assets" / "page.jpg"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"jpg")
            md_path.parent.mkdir(parents=True)

            html = markdown_to_html(
                """## 标题
副标题
<!-- page: 2 -->
正文
<div style="text-align: center;"><img src="Book_assets/page.jpg" alt="Image" width="99%" /></div>
<!-- page: 3 -->
""",
                md_path=md_path,
                input_root=root / "zh",
                asset_root=asset_root,
                options=RenderOptions(title="Book"),
            )

        self.assertIn("<h2>标题</h2>", html)
        self.assertIn("<p>副标题</p>", html)
        self.assertIn('class="page-break" data-page="2"', html)
        self.assertIn(image_path.resolve().as_uri(), html)
        self.assertNotIn("&lt;!-- page:", html)
        self.assertNotIn("&lt;div", html)

    def test_discovers_translated_markdown_and_skips_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Book").mkdir()
            translated = root / "Book" / "Book.zh.md"
            translated.write_text("text", encoding="utf-8")
            (root / "Book" / "Book.md").write_text("source", encoding="utf-8")
            (root / "translation-manifest.json").write_text("{}", encoding="utf-8")

            files = discover_input_markdown(root, target_lang="zh")

        self.assertEqual(files, [translated])

    def test_output_pdf_path_preserves_layout(self):
        source = Path("/tmp/in/Book/Book.zh.md")

        self.assertEqual(
            output_pdf_path(source, Path("/tmp/in"), Path("/tmp/out")),
            Path("/tmp/out/Book/Book.zh.pdf"),
        )


if __name__ == "__main__":
    unittest.main()
