from __future__ import annotations

import json
import re
from pathlib import Path


def strip_html_to_text(html: str, max_chars: int | None = None) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for HTML stripping") from exc
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    out = text.strip()
    if max_chars is not None:
        return out[:max_chars]
    return out


def load_tenk_htm_text(tenk_dir: Path) -> str:
    parts: list[str] = []
    if not tenk_dir.is_dir():
        return ""
    for p in sorted(tenk_dir.glob("*.htm")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts.append(strip_html_to_text(raw, max_chars=500_000))
    for p in sorted(tenk_dir.glob("*.html")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts.append(strip_html_to_text(raw, max_chars=500_000))
    return "\n\n".join(parts)


def load_filing_path_as_text(path: Path) -> str:
    """HTML primary filing on disk to plain text (no cap). PDF paths return empty."""
    suf = path.suffix.lower()
    if suf not in (".htm", ".html"):
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return strip_html_to_text(raw, max_chars=None)


def load_narrative_text(all_narrative_path: Path, max_chars: int | None = None) -> str:
    if not all_narrative_path.is_file():
        return ""
    try:
        data = json.loads(all_narrative_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    filings = data.get("filings") or []
    chunks: list[str] = []
    for f in filings:
        if not isinstance(f, dict):
            continue
        for c in f.get("concepts") or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                chunks.append(c["text"])
    blob = "\n\n".join(chunks)
    if max_chars is not None:
        return blob[:max_chars]
    return blob
