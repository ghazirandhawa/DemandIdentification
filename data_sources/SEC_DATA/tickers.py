from __future__ import annotations

from typing import Any, Iterator

from .client import EdgarClient

_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def load_company_tickers(client: EdgarClient) -> dict[str, Any]:
    """Fetch ticker / CIK / name mapping used by SEC.gov search."""
    return client.get_json(_COMPANY_TICKERS_URL)


def iter_ticker_rows(client: EdgarClient) -> Iterator[dict[str, Any]]:
    data = load_company_tickers(client)
    for row in data.values():
        if isinstance(row, dict) and "ticker" in row and "cik_str" in row:
            yield row


def ticker_to_cik_str(client: EdgarClient, ticker: str) -> str:
    """
    Resolve a stock ticker to a 10-digit CIK string (zero-padded).

    Raises KeyError if the ticker is not present in company_tickers.json.
    """
    t = ticker.strip().upper()
    for row in iter_ticker_rows(client):
        if str(row.get("ticker", "")).strip().upper() != t:
            continue
        cik_raw = row.get("cik_str")
        if cik_raw is None:
            break
        s = str(int(cik_raw))
        return s.zfill(10)
    raise KeyError(f"Unknown ticker: {ticker!r}")
