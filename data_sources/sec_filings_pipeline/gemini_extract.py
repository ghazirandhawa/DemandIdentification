from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

_client: genai.Client | None = None
_client_lock = threading.Lock()
_configured = False
_model_name = ""


def _get_client() -> genai.Client:
    global _client
    with _client_lock:
        if _client is None:
            key = (os.environ.get("GEMINI_API_KEY") or "").strip()
            if not key:
                raise RuntimeError("GEMINI_API_KEY is not set in the environment or .env file.")
            _client = genai.Client(api_key=key)
        return _client


def configure_gemini() -> str:
    """Validate API key and client; returns model id for ``generate_content``."""
    global _configured, _model_name
    if _configured:
        return _model_name
    _get_client()
    # gemini-2.0-flash is not available to new API keys; 2.5 Flash is current default.
    _model_name = (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash").strip()
    _configured = True
    return _model_name


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        if "```" in s:
            s = s.rsplit("```", 1)[0]
    return s.strip()


def extract_text_from_pdf(pdf_path: Path, *, max_bytes: int = 40 * 1024 * 1024) -> tuple[str, str]:
    """
    Use Gemini multimodal input to transcribe readable text from a PDF.

    Returns ``(text, note)`` where ``note`` describes skips or errors.
    """
    client = _get_client()
    model_name = configure_gemini()
    data = pdf_path.read_bytes()
    if len(data) > max_bytes:
        return "", f"skipped_large_pdf>{max_bytes}b:{pdf_path.name}"
    prompt = (
        "You are extracting text from an SEC filing PDF. "
        "Return plain UTF-8 text only. Preserve headings and paragraph breaks where possible. "
        "Do not add commentary."
    )
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=[
                prompt,
                types.Part.from_bytes(data=data, mime_type="application/pdf"),
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=65536,
                temperature=0.2,
            ),
        )
    except Exception as exc:
        return "", f"gemini_pdf_error:{pdf_path.name}:{exc!s}"
    text = (getattr(resp, "text", None) or "").strip()
    return text, "ok" if text else f"gemini_empty_response:{pdf_path.name}"


def _empty_bundle() -> dict[str, Any]:
    return {
        "company_name": None,
        "description_business_operations": "",
        "description_products_services": "",
        "description_strategy_outlook": "",
        "description_risk_summary": "",
        "officers": [],
        "other_contacts": [],
    }


def extract_bronze_bundle_gemini(excerpt: str) -> tuple[dict[str, Any], str]:
    """
    One structured Gemini pass: legal name, 10-K-style narrative buckets, officers.

    Narrative buckets mirror common 10-K sections (Business / offerings / MD&A-style
    strategy / risk themes) even when the excerpt mixes HTML, iXBRL snippets, and PDF text.
    """
    client = _get_client()
    model_name = configure_gemini()
    prompt = (
        "You are analyzing excerpts from SEC Form 10-K materials (business description, "
        "risk discussion, MD&A-style narrative, tables, or PDF-derived text). "
        "Return ONLY valid JSON (no markdown fences) with EXACTLY these keys:\n"
        "{\n"
        '  "company_name": string|null,\n'
        '  "description_business_operations": string,\n'
        '  "description_products_services": string,\n'
        '  "description_strategy_outlook": string,\n'
        '  "description_risk_summary": string,\n'
        '  "officers": [{"name":"string","title":"string","email":null,"phone":null}],\n'
        '  "other_contacts": [{"name_or_role":"string","detail":"string"}]\n'
        "}\n"
        "Rules:\n"
        "- company_name: registrant legal name if clearly stated; else null.\n"
        "- description_business_operations: Item-1-style company, markets, competition, "
        "organizational overview (prose paragraphs).\n"
        "- description_products_services: products, services, segments, contracts, "
        "technology offerings, revenue streams.\n"
        "- description_strategy_outlook: management strategy, growth initiatives, capex, "
        "M&A themes, forward-looking plans (MD&A / Item-7 flavor).\n"
        "- description_risk_summary: condensed principal risks (Item-1A flavor); if absent, \"\".\n"
        "- officers: named executive officers (CEO, CFO, COO, CTO, President, Chair, etc.) "
        "with email/phone only if present in the excerpt.\n"
        "- other_contacts: IR, media, or notable contacts with detail text.\n"
        "Use \"\" for unknown text sections.\n\n"
        f"EXCERPT:\n{excerpt}"
    )
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=32768,
                temperature=0.15,
            ),
        )
    except Exception as exc:
        out = _empty_bundle()
        out["gemini_error"] = repr(exc)
        return out, f"gemini_bundle_error:{exc!s}"
    raw = (getattr(resp, "text", None) or "").strip()
    if not raw:
        return _empty_bundle(), "gemini_bundle_empty"
    try:
        obj = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        out = _empty_bundle()
        out["raw_response_excerpt"] = raw[:4000]
        return out, "gemini_bundle_json_parse_error"
    if not isinstance(obj, dict):
        return _empty_bundle(), "gemini_bundle_bad_type"
    base = _empty_bundle()
    for k in base:
        if k in obj:
            base[k] = obj[k]
    if not isinstance(base.get("officers"), list):
        base["officers"] = []
    if not isinstance(base.get("other_contacts"), list):
        base["other_contacts"] = []
    for text_key in (
        "description_business_operations",
        "description_products_services",
        "description_strategy_outlook",
        "description_risk_summary",
    ):
        v = base.get(text_key)
        if v is None:
            base[text_key] = ""
        elif not isinstance(v, str):
            base[text_key] = str(v)
    cn = base.get("company_name")
    if cn is not None and not isinstance(cn, str):
        base["company_name"] = str(cn) if cn else None
    return base, "ok"
