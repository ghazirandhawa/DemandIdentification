from __future__ import annotations

import json
import os
from typing import Any, Callable

from google.genai import types

from .gemini_extract import _get_client, _strip_json_fence, configure_gemini

OPPORTUNITIES_PROMPT_VERSION = "sec_opp_v2"
INSIGHTS_PROMPT_VERSION = "sec_ins_v2"


def _use_json_response_mime() -> bool:
    v = (os.environ.get("GEMINI_RESPONSE_JSON") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _use_json_repair() -> bool:
    v = (os.environ.get("GEMINI_JSON_REPAIR") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


_JSON_REPAIR_MAX_BAD_CHARS = 28_000

_OPPORTUNITY_JSON_KEYS_LINE = (
    "company_industry, geography, signal_display, signal_source, problem_type, icp_score, "
    "priority, stage, opportunity_kind, signal_title, signal_description, source_url, "
    "signal_date, scoring_notes, evidence_quotes"
)

_INSIGHTS_JSON_KEYS_LINE = (
    "business_problem, problem_category, severity_score, impact_summary, why_opportunity, "
    "why_now_signals, solution_summary, outreach_draft, evidence_quotes"
)


def _build_json_repair_prompt(bad_text: str, *, required_keys_line: str) -> str:
    snippet = (bad_text or "").strip()
    if len(snippet) > _JSON_REPAIR_MAX_BAD_CHARS:
        snippet = snippet[: _JSON_REPAIR_MAX_BAD_CHARS] + "\n\n[... truncated for repair prompt ...]\n"
    return (
        "You are a strict JSON repair tool. The text below was meant to be ONE JSON object but "
        "is invalid JSON (bad escapes, unclosed strings, trailing commas, prose before/after, "
        "truncation, etc.).\n\n"
        "TASK: Return ONLY one valid JSON object (no markdown fences, no commentary). "
        "Preserve field values and meaning as much as possible. Inside strings, escape "
        'double quotes as \\" and newlines as \\n. Use null only where a value was completely '
        "lost.\n\n"
        f"Required top-level keys (same names and nesting as the original task): {required_keys_line}\n\n"
        "INVALID OUTPUT TO FIX:\n---\n"
        f"{snippet}\n"
        "---\n"
    )


def _repair_json_via_gemini(
    bad_text: str, *, required_keys_line: str, max_output_tokens: int
) -> tuple[str, str]:
    prompt = _build_json_repair_prompt(bad_text, required_keys_line=required_keys_line)
    return _generate_raw_json_text(prompt, temperature=0.0, max_output_tokens=max_output_tokens)


def _generate_raw_json_text(
    prompt: str, *, temperature: float, max_output_tokens: int
) -> tuple[str, str]:
    client = _get_client()
    model_name = configure_gemini()
    cfg_kw: dict[str, Any] = {
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
    }
    if _use_json_response_mime():
        cfg_kw["response_mime_type"] = "application/json"
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**cfg_kw),
        )
    except Exception as exc:
        return "", f"gemini_error:{exc!s}"
    raw = (getattr(resp, "text", None) or "").strip()
    if not raw:
        return "", "gemini_empty"
    return raw, "ok"


def _first_balanced_json_object(s: str) -> str | None:
    """Slice the first top-level ``{ ... }`` object, respecting string literals."""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"' and not esc:
            in_str = not in_str
            continue
        if not in_str:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
    return None


def _parse_json_obj(raw: str) -> tuple[dict[str, Any], str]:
    stripped = _strip_json_fence((raw or "").strip())
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    inner = _first_balanced_json_object(stripped)
    if inner and inner not in candidates:
        candidates.append(inner)

    last_err: str | None = None
    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError as exc:
            last_err = str(exc)
            continue
        if not isinstance(obj, dict):
            return {}, "not_object"
        return obj, "ok"
    if last_err:
        return {}, f"json_parse_error:{last_err[:240]}"
    return {}, "json_parse_error"


def _failure_payload(raw: str, *, parse_note: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"raw_response_excerpt": raw[:6000], "_parse_note": parse_note}
    if extra:
        out.update(extra)
    return out


def _parse_or_repair_then_normalize(
    raw: str,
    *,
    first_call_note: str,
    max_output_tokens: int,
    required_keys_line: str,
    normalize: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    obj, pnote = _parse_json_obj(raw)
    if pnote == "ok":
        return normalize(obj), first_call_note
    if not _use_json_repair():
        return _failure_payload(raw, parse_note=pnote), pnote
    repair_raw, rnote = _repair_json_via_gemini(
        raw, required_keys_line=required_keys_line, max_output_tokens=max_output_tokens
    )
    if not repair_raw:
        return (
            _failure_payload(
                raw,
                parse_note=pnote,
                extra={"_repair_note": rnote},
            ),
            f"{pnote}|repair_empty:{rnote}",
        )
    obj2, pnote2 = _parse_json_obj(repair_raw)
    if pnote2 != "ok":
        return (
            _failure_payload(
                raw,
                parse_note=pnote,
                extra={
                    "_repair_note": rnote,
                    "_repair_parse_note": pnote2,
                    "repair_response_excerpt": repair_raw[:4000],
                },
            ),
            f"{pnote}|repair_parse:{pnote2}",
        )
    return normalize(obj2), "json_repaired"


def _allowed_problem_types() -> tuple[str, ...]:
    return ("Data Engineering", "BI / Analytics", "AI / ML", "GenAI / Automation")


def _allowed_signal_displays() -> tuple[str, ...]:
    return (
        "Hiring Signal",
        "Strategic Signal",
        "Startup / Growth",
        "Pain Signal",
        "Formal RFP",
    )


def _allowed_priorities() -> tuple[str, ...]:
    return ("Critical", "High", "Medium", "Low")


def _allowed_signal_sources() -> tuple[str, ...]:
    """Dashboard ``Signals by Source`` bands (short labels)."""
    return ("Hiring", "Startup", "Rfp", "Strategic", "Pain")


BRONZE_FIELD_ALIASES = (
    "description_business_operations",
    "description_products_services",
    "description_strategy_outlook",
    "description_risk_summary",
    "tags_business",
    "tags_products",
    "tags_strategy",
    "tags_risk",
    "signal_hits_pain",
    "signal_hits_strategic",
    "signal_hits_vertical",
    "relevance_keywords",
    "pdf_gemini_notes",
    "officers_contacts",
)


def _coerce_int(v: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _coerce_str(v: Any, *, default: str = "") -> str:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip() or default
    return str(v).strip() or default


def build_opportunity_prompt(
    *,
    datapilot_context: str,
    bronze_block: str,
    context_meta_note: str,
) -> str:
    prob = ", ".join(_allowed_problem_types())
    sig = ", ".join(_allowed_signal_displays())
    src = ", ".join(_allowed_signal_sources())
    pri = ", ".join(_allowed_priorities())
    bfields = ", ".join(BRONZE_FIELD_ALIASES)
    schema = (
        "Return ONLY valid JSON (optionally wrapped in ```json fences) with EXACTLY these keys:\n"
        "{\n"
        '  "company_industry": string,\n'
        '  "geography": string,\n'
        f'  "signal_display": string (one of: {sig}),\n'
        f'  "signal_source": string (one of: {src}; best single band for the dashboard),\n'
        f'  "problem_type": string (one of: {prob}),\n'
        '  "icp_score": integer 0-100,\n'
        f'  "priority": string (one of: {pri}),\n'
        '  "stage": string (default Signals Identified; may use Outreach Draft Created, '
        'Scored & Prioritized, Solution Mapped, Awaiting Approval if justified),\n'
        '  "opportunity_kind": string (e.g. SEC_Filing),\n'
        '  "signal_title": string,\n'
        '  "signal_description": string (2-6 sentences),\n'
        '  "source_url": string|null,\n'
        '  "signal_date": string|null (ISO-8601 date or datetime if known from input; else null),\n'
        '  "scoring_notes": string (markdown bullets; MUST tie each major score/classification '
        "to verbatim quotes from the BRONZE INPUT using the format: "
        '`Quote: \"...\"` (Field: <which bronze field>) — <why this supports the determination>),\n'
        '  "evidence_quotes": [\n'
        '    {"determination": "icp_score|priority|problem_type|signal_display|signal_source|...", '
        f'"bronze_field": string (one of: {bfields}), '
        '"verbatim_quote": string, "rationale": string}\n'
        "  ]\n"
        "}\n"
    )
    rules = (
        "Rules:\n"
        "- Provenance is SEC filings in bronze, but ``signal_source`` must still be exactly one "
        f"dashboard band in {{{src}}} inferred from language in tags/signals/descriptions.\n"
        "- You MUST ground every classification (ICP score band, priority, problem_type, "
        "signal_display, signal_source band) in explicit evidence from the BRONZE INPUT below.\n"
        "- evidence_quotes MUST contain at least 4 items when the bronze text has enough content; "
        "each verbatim_quote MUST be copied exactly from the bronze text (substring), not invented.\n"
        "- bronze_field must use the canonical names above (the same labels as the BRONZE INPUT keys).\n"
        "- For every quoted span, the quote MUST appear verbatim under that bronze_field section "
        "in the BRONZE INPUT; if you cannot find a literal substring, do not quote it.\n"
        "- Output must be **valid JSON**: inside string values, escape double quotes as \\\" and "
        "line breaks as \\n so parsers never break.\n"
        "- If the bronze text is thin, say so in scoring_notes and still return best-effort fields; "
        "use shorter quotes only if they truly appear.\n"
        "- Map SEC narratives to Data Pilot services (data platforms, analytics/BI, AI/ML, "
        "GenAI/automation, transformation) using BOTH the datapilot context bundle AND the bronze text.\n"
        "- signal_display should reflect the dominant demand signal implied by the bronze tags/signals "
        "(hiring vs pain vs strategy vs growth vs RFP-like procurement language).\n"
        "- Do not invent ticker-specific URLs; prefer null for source_url unless the input contains a URL.\n"
        f"- Context bundle status: {context_meta_note}\n"
    )
    return (
        "You are a senior GTM analyst for Data Pilot. You score SEC-derived bronze rows into "
        "silver opportunity records for a CRM-style UI.\n\n"
        "=== DATAPILOT CONTEXT (ICP / scope / deck text; may be partial if files are missing) ===\n"
        f"{datapilot_context}\n\n"
        "=== BRONZE INPUT (SEC filing derived fields for one company) ===\n"
        f"{bronze_block}\n\n"
        + schema + rules
    )


def score_opportunity_with_gemini(
    *,
    datapilot_context: str,
    bronze_block: str,
    context_meta_note: str,
) -> tuple[dict[str, Any], str]:
    prompt = build_opportunity_prompt(
        datapilot_context=datapilot_context,
        bronze_block=bronze_block,
        context_meta_note=context_meta_note,
    )
    raw, note = _generate_raw_json_text(prompt, temperature=0.2, max_output_tokens=16384)
    if not raw:
        return {}, note
    return _parse_or_repair_then_normalize(
        raw,
        first_call_note=note,
        max_output_tokens=16384,
        required_keys_line=_OPPORTUNITY_JSON_KEYS_LINE,
        normalize=_normalize_opportunity_obj,
    )


def _normalize_opportunity_obj(obj: dict[str, Any]) -> dict[str, Any]:
    prob = _allowed_problem_types()
    sigd = _allowed_signal_displays()
    srcb = _allowed_signal_sources()
    pri = _allowed_priorities()
    pt = _coerce_str(obj.get("problem_type"))
    if pt not in prob:
        pt = "Data Engineering" if "data" in pt.lower() else prob[0]
    sd = _coerce_str(obj.get("signal_display"))
    if sd not in sigd:
        sd = "Strategic Signal"
    ss = _coerce_str(obj.get("signal_source"))
    if ss not in srcb:
        legacy = ss.upper()
        if "RFP" in legacy or "PROCUREMENT" in legacy or "TENDER" in legacy:
            ss = "Rfp"
        elif "HIRING" in legacy or "JOB" in legacy or "RECRUIT" in legacy:
            ss = "Hiring"
        elif "STARTUP" in legacy or "SERIES" in legacy or "VC" in legacy:
            ss = "Startup"
        elif "RISK" in legacy or "PAIN" in legacy or "CYBER" in legacy or "BREACH" in legacy:
            ss = "Pain"
        else:
            ss = "Strategic"
    pr = _coerce_str(obj.get("priority"))
    if pr not in pri:
        pr = "Medium"
    out = {
        "company_industry": _coerce_str(obj.get("company_industry")) or "Unknown",
        "geography": _coerce_str(obj.get("geography")) or "Unknown",
        "signal_display": sd,
        "signal_source": ss,
        "problem_type": pt,
        "icp_score": _coerce_int(obj.get("icp_score"), default=50, lo=0, hi=100),
        "priority": pr,
        "stage": _coerce_str(obj.get("stage")) or "Signals Identified",
        "opportunity_kind": _coerce_str(obj.get("opportunity_kind")) or "SEC_Filing",
        "signal_title": _coerce_str(obj.get("signal_title")) or "SEC filing signal",
        "signal_description": _coerce_str(obj.get("signal_description")) or "",
        "source_url": obj.get("source_url"),
        "signal_date": obj.get("signal_date"),
        "scoring_notes": _coerce_str(obj.get("scoring_notes")),
        "evidence_quotes": obj.get("evidence_quotes") if isinstance(obj.get("evidence_quotes"), list) else [],
    }
    if not out["signal_description"]:
        out["signal_description"] = out["scoring_notes"][:2000] if out["scoring_notes"] else ""
    return out


def build_insights_prompt(
    *,
    datapilot_context: str,
    bronze_block: str,
    opportunity_block: str,
    context_meta_note: str,
) -> str:
    bfields = ", ".join(BRONZE_FIELD_ALIASES)
    schema = (
        "Return ONLY valid JSON (optionally wrapped in ```json fences) with EXACTLY these keys:\n"
        "{\n"
        '  "business_problem": string,\n'
        '  "problem_category": string (short tag),\n'
        '  "severity_score": integer 0-100,\n'
        '  "impact_summary": string,\n'
        '  "why_opportunity": string (ICP fit; include at least two short verbatim quotes copied '
        "from the BRONZE INPUT and label which bronze_field each quote came from),\n"
        '  "why_now_signals": string (urgency / timing triggers; must cite verbatim bronze quotes '
        "with bronze_field labels),\n"
        '  "solution_summary": string (include Suggested Approach, Entry Point, Business Outcome, '
        "and Solution Journey as numbered markdown subsections),\n"
        '  "outreach_draft": string (include Persona, Channel, Angle as markdown subheadings, '
        "then a concise draft message),\n"
        '  "evidence_quotes": [\n'
        '    {"section": "problem|why_now|solution|outreach|icp", '
        f'"bronze_field": string (one of: {bfields}), '
        '"verbatim_quote": string, "rationale": string}\n'
        "  ]\n"
        "}\n"
    )
    rules = (
        "Rules:\n"
        "- Ground claims in the BRONZE INPUT; every substantive paragraph should reference "
        "at least one verbatim_quote entry.\n"
        "- evidence_quotes MUST have at least 5 items when bronze text is non-trivial.\n"
        "- bronze_field must be one of the canonical names listed above.\n"
        "- verbatim_quote must be an exact substring from the BRONZE INPUT under that field; "
        "do not invent quotes.\n"
        "- Output must be **valid JSON**: inside string values, escape double quotes as \\\" and "
        "line breaks as \\n.\n"
        "- Align recommendations with Data Pilot positioning in the datapilot context bundle.\n"
        f"- Context bundle status: {context_meta_note}\n"
    )
    return (
        "You are a senior solutions consultant for Data Pilot. Expand a scored SEC opportunity "
        "into Problem / Solution / Outreach style insights for sales review.\n\n"
        "=== DATAPILOT CONTEXT ===\n"
        f"{datapilot_context}\n\n"
        "=== SCORED OPPORTUNITY (current silver fields) ===\n"
        f"{opportunity_block}\n\n"
        "=== BRONZE INPUT (verbatim source) ===\n"
        f"{bronze_block}\n\n"
        + schema + rules
    )


def generate_insights_with_gemini(
    *,
    datapilot_context: str,
    bronze_block: str,
    opportunity_block: str,
    context_meta_note: str,
) -> tuple[dict[str, Any], str]:
    prompt = build_insights_prompt(
        datapilot_context=datapilot_context,
        bronze_block=bronze_block,
        opportunity_block=opportunity_block,
        context_meta_note=context_meta_note,
    )
    raw, note = _generate_raw_json_text(prompt, temperature=0.25, max_output_tokens=16384)
    if not raw:
        return {}, note
    return _parse_or_repair_then_normalize(
        raw,
        first_call_note=note,
        max_output_tokens=16384,
        required_keys_line=_INSIGHTS_JSON_KEYS_LINE,
        normalize=_normalize_insights_obj,
    )


def _normalize_insights_obj(obj: dict[str, Any]) -> dict[str, Any]:
    eq = obj.get("evidence_quotes")
    if not isinstance(eq, list):
        eq = []
    sev = obj.get("severity_score")
    if sev is None or (isinstance(sev, str) and not sev.strip()):
        sev_i: int | None = None
    else:
        sev_i = _coerce_int(sev, default=50, lo=0, hi=100)
    return {
        "business_problem": _coerce_str(obj.get("business_problem")),
        "problem_category": _coerce_str(obj.get("problem_category")),
        "severity_score": sev_i,
        "impact_summary": _coerce_str(obj.get("impact_summary")),
        "why_opportunity": _coerce_str(obj.get("why_opportunity")),
        "why_now_signals": _coerce_str(obj.get("why_now_signals")),
        "solution_summary": _coerce_str(obj.get("solution_summary")),
        "outreach_draft": _coerce_str(obj.get("outreach_draft")),
        "evidence_quotes": eq,
    }


def format_bronze_block(row: dict[str, Any]) -> str:
    def g(key: str) -> str:
        v = row.get(key)
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            try:
                return json.dumps(v, ensure_ascii=False)[:20000]
            except (TypeError, ValueError):
                return str(v)[:20000]
        return str(v)

    return (
        f"run_folder: {g('run_folder')}\n"
        f"ticker: {g('ticker')}\n"
        f"cik10: {g('cik10')}\n"
        f"company_name: {g('company_name')}\n"
        f"relevance_keywords: {g('relevance_keywords')}\n"
        f"signal_hits_pain: {g('signal_hits_pain')}\n"
        f"signal_hits_strategic: {g('signal_hits_strategic')}\n"
        f"signal_hits_vertical: {g('signal_hits_vertical')}\n"
        f"tags_business: {g('tags_business')}\n"
        f"tags_products: {g('tags_products')}\n"
        f"tags_strategy: {g('tags_strategy')}\n"
        f"tags_risk: {g('tags_risk')}\n"
        f"description_business_operations: {g('description_business_operations')}\n"
        f"description_products_services: {g('description_products_services')}\n"
        f"description_strategy_outlook: {g('description_strategy_outlook')}\n"
        f"description_risk_summary: {g('description_risk_summary')}\n"
        f"officers_contacts: {g('officers_contacts')}\n"
        f"pdf_gemini_notes: {g('pdf_gemini_notes')}\n"
    )


def truncate_block(text: str, max_chars: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 120] + "\n\n[... truncated ...]\n"


def format_silver_opportunity_block(o: dict[str, Any]) -> str:
    keys = (
        "opportunity_id",
        "silver_signal_id",
        "silver_company_id",
        "bronze_run_folder",
        "bronze_ticker",
        "cik10",
        "company_name",
        "company_industry",
        "geography",
        "signal_display",
        "signal_source",
        "problem_type",
        "icp_score",
        "priority",
        "stage",
        "opportunity_kind",
        "signal_title",
        "signal_description",
        "source_url",
        "signal_date",
        "country",
        "mailing_address",
        "website",
        "business_address",
        "contacts_summary",
        "latest_revenue_usd",
        "scoring_notes",
    )
    lines: list[str] = []
    for k in keys:
        v = o.get(k)
        if v is None:
            continue
        lines.append(f"{k}: {v}")
    eq = o.get("evidence_quotes")
    if eq is not None:
        try:
            ej = json.dumps(eq, ensure_ascii=False)
        except (TypeError, ValueError):
            ej = str(eq)
        if len(ej) > 12_000:
            ej = ej[:12_000] + "…"
        lines.append(f"evidence_quotes (from opportunity scoring): {ej}")
    return "\n".join(lines)
