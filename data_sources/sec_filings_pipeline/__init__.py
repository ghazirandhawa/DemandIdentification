"""Bronze pipeline: latest EDGAR run → keywords, cap facts, Gemini PDF/officers → CSV + Postgres."""

from .relevance_keywords import RELEVANCE_KEYWORDS

__all__ = ["RELEVANCE_KEYWORDS"]
