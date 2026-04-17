from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def llm_provider() -> str:
    """
    ``BRONZE_LLM_PROVIDER`` may be ``openai`` or ``gemini``.

    If unset, pick **Gemini** when only ``GEMINI_API_KEY`` is set, **OpenAI** when only
    ``OPENAI_API_KEY`` is set; if both are set, default to **openai** (set ``BRONZE_LLM_PROVIDER``
    to override).
    """
    raw = (os.environ.get("BRONZE_LLM_PROVIDER") or "").strip().lower()
    if raw in ("openai", "gemini"):
        return raw
    oa = (os.environ.get("OPENAI_API_KEY") or "").strip()
    gm = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gm and not oa:
        return "gemini"
    if oa and not gm:
        return "openai"
    if oa and gm:
        return "openai"
    return "openai"


def configure_llm() -> str:
    if llm_provider() == "openai":
        from .openai_extract import configure_openai

        return configure_openai()
    from .gemini_extract import configure_gemini

    return configure_gemini()


def extract_pdf_text(pdf_path: Path) -> tuple[str, str]:
    if llm_provider() == "openai":
        from .openai_extract import extract_pdf_text_with_pypdf

        return extract_pdf_text_with_pypdf(pdf_path)
    from .gemini_extract import extract_text_from_pdf

    return extract_text_from_pdf(pdf_path)


def extract_bronze_bundle(excerpt: str) -> tuple[dict[str, Any], str]:
    if llm_provider() == "openai":
        from .openai_extract import extract_bronze_bundle_openai

        return extract_bronze_bundle_openai(excerpt)
    from .gemini_extract import extract_bronze_bundle_gemini

    return extract_bronze_bundle_gemini(excerpt)
