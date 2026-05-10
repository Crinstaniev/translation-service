import base64
import json
import tempfile
import unittest
from pathlib import Path

from pdf_pipeline.paddleocr_markdown import (
    build_layout_parsing_payload,
    combine_markdown_pages,
    save_layout_response,
)


class PaddleOCRMarkdownTests(unittest.TestCase):
    def test_build_layout_payload_marks_input_as_pdf(self):
        payload = build_layout_parsing_payload("http://example.test/book.pdf")

        self.assertEqual(payload["file"], "http://example.test/book.pdf")
        self.assertEqual(payload["fileType"], 0)
        self.assertFalse(payload["useDocOrientationClassify"])
        self.assertFalse(payload["useDocUnwarping"])
        self.assertIn("footer", payload["markdownIgnoreLabels"])

    def test_combine_markdown_pages_keeps_page_boundaries(self):
        response = {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"text": "# Title\n\nPage one."}},
                    {"markdown": {"text": "Page two."}},
                ]
            }
        }

        markdown = combine_markdown_pages(response)

        self.assertIn("<!-- page: 1 -->", markdown)
        self.assertIn("# Title", markdown)
        self.assertIn("<!-- page: 2 -->", markdown)
        self.assertTrue(markdown.endswith("\n"))

    def test_save_layout_response_writes_raw_json_markdown_and_images(self):
        png = base64.b64encode(b"fake-png").decode("ascii")
        response = {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "![figure](img_0.png)",
                            "images": {"img_0.png": png},
                        }
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            paths = save_layout_response(response, Path(tmp), "book")

            self.assertTrue(paths.raw_json.exists())
            self.assertTrue(paths.markdown.exists())
            self.assertTrue((Path(tmp) / "book_assets" / "img_0.png").exists())
            self.assertIn("book_assets/img_0.png", paths.markdown.read_text())
            self.assertEqual(json.loads(paths.raw_json.read_text()), response)


if __name__ == "__main__":
    unittest.main()
