from __future__ import annotations

from .relevance_keywords import RELEVANCE_KEYWORDS


def find_keyword_hits(text: str) -> list[str]:
    """Case-insensitive substring match for each phrase (order preserved, unique)."""
    if not text:
        return []
    lower = text.lower()
    seen: set[str] = set()
    hits: list[str] = []
    for phrase in RELEVANCE_KEYWORDS:
        p = phrase.lower()
        if p in lower and phrase not in seen:
            seen.add(phrase)
            hits.append(phrase)
    return hits
