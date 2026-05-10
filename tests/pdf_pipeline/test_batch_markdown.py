import tempfile
import unittest
from pathlib import Path

from pdf_pipeline.batch_markdown import (
    discover_pdfs,
    output_stem_for_pdf,
    per_pdf_output_dir,
    should_skip_pdf,
)


class BatchMarkdownTests(unittest.TestCase):
    def test_discover_pdfs_sorts_case_insensitively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.PDF").write_text("pdf")
            (root / "A.pdf").write_text("pdf")
            (root / "note.txt").write_text("text")

            names = [path.name for path in discover_pdfs(root)]

            self.assertEqual(names, ["A.pdf", "b.PDF"])

    def test_output_stem_sanitizes_filename(self):
        stem = output_stem_for_pdf(Path("Jackie Nowell, Loan Shark.pdf"))

        self.assertEqual(stem, "Jackie_Nowell_Loan_Shark")

    def test_should_skip_when_markdown_and_json_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            book_dir = out / "Book"
            book_dir.mkdir()
            (book_dir / "Book.md").write_text("markdown")
            (book_dir / "Book.paddleocr.json").write_text("{}")

            self.assertTrue(should_skip_pdf(out, "Book", force=False))
            self.assertFalse(should_skip_pdf(out, "Book", force=True))
            self.assertFalse(should_skip_pdf(out, "Missing", force=False))

    def test_per_pdf_output_dir_uses_stem(self):
        self.assertEqual(
            per_pdf_output_dir(Path("/tmp/out"), "Book"),
            Path("/tmp/out/Book"),
        )


if __name__ == "__main__":
    unittest.main()
