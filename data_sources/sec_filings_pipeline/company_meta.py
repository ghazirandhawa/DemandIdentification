from __future__ import annotations

import json
from pathlib import Path


def entity_name_from_companyfacts(companyfacts_path: Path) -> str:
    if not companyfacts_path.is_file():
        return ""
    try:
        data = json.loads(companyfacts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    name = data.get("entityName")
    return str(name).strip() if name else ""
