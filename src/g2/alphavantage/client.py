"""
Minimal AlphaVantage HTTP client with simple rate limiting.

Note: Network calls are not used in tests; this is for CLI runtime.
"""

from __future__ import annotations

import os
import threading
import time
import time
from typing import Dict, Mapping, Optional

import requests
from requests import exceptions as req_exc
from requests.exceptions import JSONDecodeError

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"


class RateLimiter:
    """Token-bucket rate limiter with dual per-minute and per-second limits."""

    def __init__(self, calls_per_minute: int, calls_per_second: int = 4):
        self.capacity = calls_per_minute
        self.tokens = calls_per_minute
        self.updated = time.monotonic()
        self.calls_per_second = calls_per_second
        self.recent_calls = []  # Track timestamps of recent calls
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.updated

            # Per-minute token bucket: refill tokens
            refill = (self.capacity / 60.0) * elapsed
            if refill > 0:
                self.tokens = min(self.capacity, self.tokens + refill)
                self.updated = now

            # Per-second sliding window: remove calls older than 1 second
            cutoff = now - 1.0
            self.recent_calls = [t for t in self.recent_calls if t > cutoff]

            # Check if we can make a request
            if self.tokens >= 1 and len(self.recent_calls) < self.calls_per_second:
                self.tokens -= 1
                self.recent_calls.append(now)
                return

            # Calculate how long to wait
            if self.tokens < 1:
                # Wait for per-minute limit
                sleep_for = (1 - self.tokens) * (60.0 / self.capacity)
            else:
                # Wait for per-second limit (oldest call to age out)
                if self.recent_calls:
                    oldest = self.recent_calls[0]
                    sleep_for = max(0.0, 1.0 - (now - oldest)) + 0.01
                else:
                    sleep_for = 0.01
        time.sleep(sleep_for)
        self.acquire()


class AlphaVantageClient:
    """HTTP client for AlphaVantage."""

    def __init__(self, api_key: Optional[str] = None, calls_per_minute: int = 75, session: Optional[requests.Session] = None):
        self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY")
        if not self.api_key:
            raise ValueError("ALPHAVANTAGE_API_KEY is required")
        self.session = session or requests.Session()
        self.rate = RateLimiter(calls_per_minute)

    def get(self, function: str, **params: str) -> Mapping[str, object]:
        return self.get_with_retry(function, retries=5, backoff=3.0, **params)

    def get_with_retry(self, function: str, retries: int = 3, backoff: float = 2.0, **params: str) -> Mapping[str, object]:
        payload: Dict[str, str] = {"function": function, "apikey": self.api_key}
        payload.update({k: v for k, v in params.items() if v is not None})
        attempt = 0
        while True:
            try:
                self.rate.acquire()
                resp = self.session.get(ALPHAVANTAGE_URL, params=payload, timeout=30)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except JSONDecodeError:
                    data = {"text": resp.text}
                # Treat empty payload as retryable
                if not data:
                    raise req_exc.RequestException("empty payload")
                return data
            except (req_exc.ConnectionError, req_exc.Timeout, req_exc.ChunkedEncodingError, req_exc.RequestException):
                attempt += 1
                if attempt > retries:
                    raise
                time.sleep(backoff * attempt)

    def fetch_daily_adjusted(self, symbol: str, outputsize: str = "compact") -> Mapping[str, object]:
        return self.get("TIME_SERIES_DAILY_ADJUSTED", symbol=symbol, outputsize=outputsize)

    def fetch_listing_status(self) -> Mapping[str, object]:
        # Listing status is CSV; we request csv and fall back to text parse.
        return self.get("LISTING_STATUS", datatype="csv")
