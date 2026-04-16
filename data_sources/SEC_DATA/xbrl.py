from __future__ import annotations

from typing import Any

from .client import EdgarClient
from .submissions import format_cik10


def fetch_company_facts(client: EdgarClient, cik: str | int) -> dict[str, Any]:
    """
    All aggregated XBRL facts for a filer (10-K, 10-Q, 8-K, etc.).

    Endpoint documented at:
    https://www.sec.gov/edgar/sec-api-documentation
    """
    cik10 = format_cik10(cik)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    return client.get_json(url)
