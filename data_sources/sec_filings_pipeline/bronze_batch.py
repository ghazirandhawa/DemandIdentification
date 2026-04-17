from __future__ import annotations

import threading


def llm_transport_failure_in_notes(notes: str, *, provider: str) -> bool:
    """True when bundle/PDF LLM calls failed in a way that often indicates API / capacity issues."""
    n = (notes or "").lower()
    if "openai_bundle_error:" in n:
        return True
    if "gemini_bundle_error:" in n:
        return True
    if "openai_bundle_json_parse_error" in n or "gemini_bundle_json_parse_error" in n:
        return True
    if provider == "gemini" and "gemini_pdf_error:" in n:
        return True
    return False


class LlmApiAbortController:
    """
    After ``max_consecutive_failures`` consecutive LLM transport failures (default 10), the next
    failure sets ``abort`` so the pipeline can flush partial CSV/DB and exit.
    """

    def __init__(self, *, max_consecutive_failures: int = 10) -> None:
        self._max = max(1, int(max_consecutive_failures))
        self._lock = threading.Lock()
        self._consecutive = 0
        self.abort = False
        self.last_error_excerpt = ""

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0

    def record_llm_transport_failure(self, notes_excerpt: str) -> bool:
        """Return True if this failure tripped the abort threshold."""
        with self._lock:
            if self.abort:
                return False
            self._consecutive += 1
            self.last_error_excerpt = (notes_excerpt or "")[:600]
            if self._consecutive > self._max:
                self.abort = True
                return True
            return False

    def streak(self) -> int:
        with self._lock:
            return self._consecutive

    def should_abort(self) -> bool:
        with self._lock:
            return self.abort
