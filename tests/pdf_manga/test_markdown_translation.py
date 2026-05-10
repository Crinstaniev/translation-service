import tempfile
import unittest
from pathlib import Path

from pdf_manga_translator.markdown_translation import (
    TranslationState,
    discover_markdown_files,
    load_context,
    output_markdown_path,
    translate_markdown_text,
)


class FakeTranslator:
    def __init__(self, prefix="ZH"):
        self.prefix = prefix
        self.calls = []

    def translate(self, *, text, source_lang, target_lang, context, terms, timeout_seconds):
        self.calls.append(
            {
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "context": context,
                "terms": terms,
                "timeout_seconds": timeout_seconds,
            }
        )
        return f"{self.prefix}({text})"


class DroppingTranslator:
    def translate(self, *, text, source_lang, target_lang, context, terms, timeout_seconds):
        return "translated without protected tokens"


class MarkdownTranslationTests(unittest.TestCase):
    def test_translates_text_blocks_and_preserves_markdown_structure(self):
        source = """<!-- page: 1 -->

![cover](Book_assets/cover.jpg)

# Title $x+y$

Paragraph with {name} and <b>tag</b>.

```json
{"keep": "English"}
```
"""
        translator = FakeTranslator()

        result = translate_markdown_text(
            source,
            doc_id="Book/Book.md",
            translator=translator,
            state=TranslationState(),
            source_lang="en",
            target_lang="zh",
            concurrency=2,
        )

        self.assertIn("<!-- page: 1 -->", result.markdown)
        self.assertIn("![cover](Book_assets/cover.jpg)", result.markdown)
        self.assertIn('{"keep": "English"}', result.markdown)
        self.assertIn("# ZH(Title $x+y$)", result.markdown)
        self.assertIn("ZH(Paragraph with {name} and <b>tag</b>.)", result.markdown)
        self.assertEqual(result.total_units, 2)
        self.assertEqual(result.translated_units, 2)
        self.assertEqual(result.failed_units, 0)
        self.assertEqual(len(translator.calls), 2)
        self.assertTrue(any("__FMT0__" in call["text"] for call in translator.calls))

    def test_reuses_state_for_unchanged_units(self):
        state = TranslationState()
        first_translator = FakeTranslator(prefix="FIRST")
        first = translate_markdown_text(
            "One paragraph.",
            doc_id="Book/Book.md",
            translator=first_translator,
            state=state,
            source_lang="en",
            target_lang="zh",
        )

        second_translator = FakeTranslator(prefix="SECOND")
        second = translate_markdown_text(
            "One paragraph.",
            doc_id="Book/Book.md",
            translator=second_translator,
            state=state,
            source_lang="en",
            target_lang="zh",
        )

        self.assertIn("FIRST(One paragraph.)", first.markdown)
        self.assertIn("FIRST(One paragraph.)", second.markdown)
        self.assertEqual(len(second_translator.calls), 0)
        self.assertEqual(second.reused_units, 1)

    def test_validation_keeps_source_when_translator_drops_protected_tokens(self):
        result = translate_markdown_text(
            "Paragraph with {name}.",
            doc_id="Book/Book.md",
            translator=DroppingTranslator(),
            state=TranslationState(),
            source_lang="en",
            target_lang="zh",
        )

        self.assertEqual(result.markdown, "Paragraph with {name}.")
        self.assertEqual(result.failed_units, 1)

    def test_translation_splits_structural_lines_without_blank_lines(self):
        source = """## Title
Subtitle
<!-- page: 2 -->
Body
<div style="text-align: center;"><img src="Book_assets/page.jpg" alt="Image" width="99%" /></div>
"""
        translator = FakeTranslator()

        result = translate_markdown_text(
            source,
            doc_id="Book/Book.md",
            translator=translator,
            state=TranslationState(),
            source_lang="en",
            target_lang="zh",
            concurrency=1,
        )

        self.assertIn("## ZH(Title)", result.markdown)
        self.assertIn("ZH(Subtitle)", result.markdown)
        self.assertIn("<!-- page: 2 -->", result.markdown)
        self.assertIn("<div style=", result.markdown)
        self.assertEqual([call["text"] for call in translator.calls], ["Title", "Subtitle", "Body"])

    def test_discovers_source_markdown_and_maps_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            out = Path(tmp) / "output"
            (root / "Book").mkdir(parents=True)
            source = root / "Book" / "Book.md"
            source.write_text("text", encoding="utf-8")
            (root / "Book" / "Book.zh.md").write_text("old", encoding="utf-8")
            (root / "Book" / "notes.txt").write_text("skip", encoding="utf-8")

            files = discover_markdown_files(root)
            mapped = output_markdown_path(source, root, out, target_lang="zh")

        self.assertEqual(files, [source])
        self.assertEqual(mapped, out / "Book" / "Book.zh.md")

    def test_load_context_uses_file_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.txt"
            path.write_text(" custom prompt \n", encoding="utf-8")

            self.assertEqual(load_context(path, default_context="fallback"), "custom prompt")
            self.assertEqual(load_context(None, default_context="fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
