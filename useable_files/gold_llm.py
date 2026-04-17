"""Shared Gemini helpers for gold insights (ingest_gold + API regenerate)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

# Best-effort noise suppression (gRPC / absl). Must be set before importing Gemini clients.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

try:
    import logging

    logging.getLogger("absl").setLevel(logging.ERROR)
    logging.getLogger("grpc").setLevel(logging.ERROR)
except Exception:
    pass


def safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t.strip()


def _parse_llm_json_blob(s: str) -> dict[str, Any] | None:
    """Parse model output as JSON object; tolerate literal control chars in strings."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        val = json.loads(s, strict=False)
    except TypeError:
        try:
            val = json.loads(s)
        except json.JSONDecodeError:
            return None
    except json.JSONDecodeError:
        return None
    return val if isinstance(val, dict) else None


def build_context_block(row: dict[str, Any]) -> str:
    lines = [
        f"Company: {row.get('company_name')}",
        f"Industry: {row.get('company_industry') or 'unknown'}",
        f"Geography: {row.get('geography') or 'unknown'}",
        f"Signal label: {row.get('signal_display')}",
        f"Signal source: {row.get('signal_source')}",
        f"Problem type (heuristic): {row.get('problem_type')}",
        f"ICP score (rule-based): {row.get('icp_score')}",
        f"Priority (rule-based): {row.get('priority')}",
        f"Signal title: {row.get('signal_title')}",
        f"Signal description excerpt: {(row.get('signal_description') or '')[:1500]}",
        f"URL: {row.get('source_url') or 'none'}",
        f"Signal date: {row.get('signal_date')}",
    ]
    return "\n".join(lines)


def _extract_text_from_response(resp: Any) -> str:
    # google.generativeai often exposes resp.text; google.genai may also provide .text.
    txt = getattr(resp, "text", None)
    if isinstance(txt, str) and txt.strip():
        return txt
    # Try common nested shapes.
    for attr in ("candidates", "responses"):
        cand = getattr(resp, attr, None)
        if isinstance(cand, list) and cand:
            c0 = cand[0]
            t2 = getattr(c0, "text", None)
            if isinstance(t2, str) and t2.strip():
                return t2
            content = getattr(c0, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if isinstance(parts, list) and parts:
                p0 = parts[0]
                t3 = getattr(p0, "text", None)
                if isinstance(t3, str) and t3.strip():
                    return t3
    return ""


def generate_insight_json(context: str, api_key: str) -> dict[str, Any] | None:
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = f"""You are an analyst for Data Pilot (data, analytics, AI/ML, automation, transformation services).

Use ONLY the facts in the CONTEXT block below. Do not invent company names, funding rounds, or products not stated.
If information is missing, say so briefly instead of guessing.

Return a single JSON object with these keys (all strings except severity_score which is integer 0-100):
- business_problem
- why_opportunity
- solution_summary
- outreach_draft (short email-style paragraph, professional)
- problem_category (short label)
- severity_score
- impact_summary (one or two sentences)

CONTEXT:
{context}
"""

    # Prefer the new SDK (google.genai). Fall back to deprecated google.generativeai.
    resp: Any | None = None
    try:
        from google import genai  # type: ignore

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={"temperature": 0.35, "max_output_tokens": 2048},
        )
    except Exception:
        try:
            import google.generativeai as genai_old  # type: ignore

            genai_old.configure(api_key=api_key)
            model = genai_old.GenerativeModel(model_name)
            resp = model.generate_content(
                prompt,
                generation_config={"temperature": 0.35, "max_output_tokens": 2048},
            )
        except Exception:
            return None

    raw = _extract_text_from_response(resp).strip()
    if not raw:
        return None
    raw = strip_json_fence(raw)
    parsed = _parse_llm_json_blob(raw)
    if parsed is not None:
        return parsed
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return _parse_llm_json_blob(m.group(0))
    return None
