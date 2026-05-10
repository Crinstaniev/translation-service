from __future__ import annotations

import unittest

from app.prompting import PromptInput, PromptTerm, build_translation_prompt


class PromptingTests(unittest.TestCase):
    def test_prompt_includes_terms_and_context(self) -> None:
        prompt = build_translation_prompt(
            PromptInput(
                text="それは無料です。",
                source_lang="ja",
                target_lang="zh",
                context="店主正在请客。",
                terms=(PromptTerm(source="無料", target="免费"),),
                preserve_format=True,
            )
        )
        self.assertIn("無料 -> 免费", prompt)
        self.assertIn("店主正在请客。", prompt)
        self.assertIn("保留所有变量、占位符、控制符、标签和换行结构。", prompt)

    def test_prompt_without_format_preservation_skips_protected_tokens(self) -> None:
        prompt = build_translation_prompt(
            PromptInput(
                text="It's on the house.",
                source_lang="en",
                target_lang="zh",
                preserve_format=False,
            )
        )
        self.assertNotIn("原样保留", prompt)
        self.assertIn("只输出翻译结果，不要额外解释。", prompt)


if __name__ == "__main__":
    unittest.main()
