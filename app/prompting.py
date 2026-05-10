from __future__ import annotations

from dataclasses import dataclass


LANGUAGE_NAMES = {
    "ja": "日语",
    "en": "英语",
    "ko": "韩语",
    "zh": "中文",
}

PROTECTED_TOKENS = [
    "%s",
    "%d",
    "{name}",
    "{0}",
    "\\n",
    "\\r\\n",
    "<color=red>",
    "[...]",
    "【...】",
]


@dataclass(frozen=True)
class PromptTerm:
    source: str
    target: str


@dataclass(frozen=True)
class PromptInput:
    text: str
    source_lang: str
    target_lang: str
    context: str | None = None
    terms: tuple[PromptTerm, ...] = ()
    preserve_format: bool = True


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def build_translation_prompt(payload: PromptInput) -> str:
    parts: list[str] = []

    if payload.terms:
        parts.append("参考下面的术语映射进行翻译，严格优先使用指定译法：")
        parts.extend(f"{term.source} -> {term.target}" for term in payload.terms)
        parts.append("")

    if payload.context:
        parts.append("以下是本句的上文信息，仅用于帮助理解语义，不需要翻译：")
        parts.append(payload.context)
        parts.append("")

    instruction = (
        f"将以下{language_name(payload.source_lang)}文本翻译为"
        f"{language_name(payload.target_lang)}。"
    )
    constraints = ["只输出翻译结果，不要额外解释。"]

    if payload.preserve_format:
        constraints.append("保留所有变量、占位符、控制符、标签和换行结构。")
        constraints.append(
            "以下内容如在原文中出现必须原样保留：" + "、".join(PROTECTED_TOKENS)
        )

    parts.append(instruction)
    parts.extend(constraints)
    parts.append("")
    parts.append("待翻译文本：")
    parts.append(payload.text)
    return "\n".join(parts)
