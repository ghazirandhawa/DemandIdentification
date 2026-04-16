from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping

import requests
from dotenv import load_dotenv

from .rate_limit import SlidingWindowRateLimiter, build_default_limiter


def _load_dotenv_files() -> None:
    for anc in Path(__file__).resolve().parents:
        candidate = anc / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            break
    load_dotenv()


_load_dotenv_files()

_GLOBAL_RATE_LIMITER = build_default_limiter()


class EdgarClient:
    """
    HTTP client for data.sec.gov and www.sec.gov/Archives.

    Per SEC guidance, every request must include a User-Agent that identifies
    your application and includes contact information (email or URL).
    Set SEC_USER_AGENT in a repository-root .env file, in the process
    environment, or pass user_agent explicitly.

    All instances share a process-wide sliding-window rate limiter by default
    (``SEC_MAX_RPS``, default 9 requests per second, max 10). Pass a custom
    ``rate_limiter`` for isolated tests.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        session: requests.Session | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        ua = user_agent or os.environ.get("SEC_USER_AGENT", "").strip()
        if not ua:
            raise ValueError(
                "A User-Agent is required for SEC EDGAR access. "
                "Set SEC_USER_AGENT in a .env file (see .env.example) or the "
                "environment, e.g. 'YourOrgName your.email@domain.com' (see "
                "https://www.sec.gov/os/webmaster-faq#developers)."
            )
        self._user_agent = ua
        self._rate_limiter = rate_limiter if rate_limiter is not None else _GLOBAL_RATE_LIMITER
        self._session = session or requests.Session()

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def _before_request(self) -> None:
        self._rate_limiter.acquire()

    def _default_headers(self) -> Mapping[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

    def get_json(
        self,
        url: str,
        *,
        extra_headers: MutableMapping[str, str] | None = None,
    ) -> Any:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc or "data.sec.gov"
        headers: dict[str, str] = dict(self._default_headers())
        headers["Host"] = host
        if extra_headers:
            headers.update(extra_headers)
        self._before_request()
        resp = self._session.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def get_text(
        self,
        url: str,
        *,
        extra_headers: MutableMapping[str, str] | None = None,
    ) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc or "www.sec.gov"
        headers: dict[str, str] = dict(self._default_headers())
        headers["Host"] = host
        if extra_headers:
            headers.update(extra_headers)
        self._before_request()
        resp = self._session.get(url, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.content.decode("utf-8", errors="replace")

    def get_bytes(
        self,
        url: str,
        *,
        extra_headers: MutableMapping[str, str] | None = None,
    ) -> bytes:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc or "www.sec.gov"
        headers: dict[str, str] = dict(self._default_headers())
        headers["Host"] = host
        if extra_headers:
            headers.update(extra_headers)
        self._before_request()
        resp = self._session.get(url, headers=headers, timeout=180)
        resp.raise_for_status()
        return bytes(resp.content)
