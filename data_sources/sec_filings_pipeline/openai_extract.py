from __future__ import annotations

import json
import os
import re
import threading
from io import BytesIO
from pathlib import Path
from typing import Any

from openai import OpenAI

_client: OpenAI | None = None
_client_lock = threading.Lock()
_configured = False
_model_name = ""


def _get_client() -> OpenAI:
    global _client
    with _client_lock:
        if _client is None:
            key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not key:
                raise RuntimeError("OPENAI_API_KEY is not set in the environment or .env file.")
            _client = OpenAI(api_key=key)
        return _client


def configure_openai() -> str:
    """Validate client; returns model id for chat completions (``OPENAI_MODEL`` or ``gpt-4.1-mini``)."""
    global _configured, _model_name
    if _configured:
        return _model_name
    _get_client()
    _model_name = (os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    _configured = True
    return _model_name


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        if "```" in s:
            s = s.rsplit("```", 1)[0]
    return s.strip()


def extract_pdf_text_with_pypdf(pdf_path: Path, *, max_bytes: int = 40 * 1024 * 1024) -> tuple[str, str]:
    data = pdf_path.read_bytes()
    if len(data) > max_bytes:
        return "", f"skipped_large_pdf>{max_bytes}b:{pdf_path.name}"
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        return "", f"pypdf_missing:{pdf_path.name}:{exc!s}"
    try:
        reader = PdfReader(BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        txt = "\n".join(parts).strip()
        return txt, "ok_pypdf" if txt else f"pypdf_empty:{pdf_path.name}"
    except Exception as exc:
        return "", f"pypdf_error:{pdf_path.name}:{exc!s}"


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


def extract_bronze_bundle_openai(excerpt: str) -> tuple[dict[str, Any], str]:
    """
    Structured OpenAI pass: legal name, 10-K-style narrative buckets, officers.
    No excerpt length cap (caller still bounded by RAM / provider limits).
    """
    client = _get_client()
    model_name = configure_openai()
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
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.15,
            max_tokens=32768,
        )
    except Exception as exc:
        return _empty_bundle(), f"openai_bundle_error:{exc!s}"
    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return _empty_bundle(), "openai_bundle_empty"
    try:
        obj = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        out = _empty_bundle()
        out["raw_response_excerpt"] = raw[:4000]
        return out, "openai_bundle_json_parse_error"
    if not isinstance(obj, dict):
        return _empty_bundle(), "openai_bundle_bad_type"
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
