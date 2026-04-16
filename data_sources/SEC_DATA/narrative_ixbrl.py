from __future__ import annotations

import html as html_module
import re
from typing import Any

_IX_NONNUMERIC = re.compile(
    r"<ix:nonNumeric\s+([^>]+)>([\s\S]*?)</ix:nonNumeric>",
    re.IGNORECASE,
)
_NAME_ATTR = re.compile(r'\bname\s*=\s*"([^"]+)"', re.IGNORECASE)


def _strip_html_to_text(fragment: str, max_chars: int = 400_000) -> str:
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", fragment)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?is)<head[^>]*>.*?</head>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_module.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_chars:
        return t[:max_chars] + "\n...[truncated]"
    return t


def extract_ix_nonnumeric_narratives(html: str) -> list[dict[str, Any]]:
    """
    Pull inline XBRL ``ix:nonNumeric`` fragments from a 10-K HTML document.

    SEC ``data.sec.gov`` JSON does not include Item 1 / 1A / 7 narrative; filers
    embed that prose in the primary HTML as iXBRL facts (often long text blocks).
    """
    out: list[dict[str, Any]] = []
    for m in _IX_NONNUMERIC.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        nm = _NAME_ATTR.search(attrs)
        if not nm:
            continue
        concept = nm.group(1).strip()
        text = _strip_html_to_text(inner)
        out.append({"concept": concept, "text": text, "char_count": len(text)})
    return out


def filter_narrative_blocks(
    blocks: list[dict[str, Any]],
    *,
    concept_filter,
) -> list[dict[str, Any]]:
    return [b for b in blocks if concept_filter(b["concept"], b["text"])]
