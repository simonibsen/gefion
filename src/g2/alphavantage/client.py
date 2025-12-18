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
    """Rate limiter with minimum spacing to prevent burst patterns.

    AlphaVantage requires requests to be spread evenly across the 1-minute window,
    not just under 5/sec. We enforce minimum spacing between consecutive requests.
    """

    def __init__(self, calls_per_minute: int, calls_per_second: int = 4):
        self.capacity = calls_per_minute
        self.tokens = calls_per_minute
        self.updated = time.monotonic()
        # Enforce minimum spacing: for 75/min, that's 0.8 sec/call
        # Add 25% buffer for safety: 0.8 * 1.25 = 1.0 second minimum spacing
        self.min_spacing = (60.0 / calls_per_minute) * 1.25
        self.last_request = 0.0  # Track last request time
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

            # Check minimum spacing since last request
            time_since_last = now - self.last_request

            # Can proceed if we have tokens AND enough time has passed
            if self.tokens >= 1 and time_since_last >= self.min_spacing:
                self.tokens -= 1
                self.last_request = now
                return

            # Calculate how long to wait
            if self.tokens < 1:
                # Wait for per-minute token bucket to refill
                wait_for_tokens = (1 - self.tokens) * (60.0 / self.capacity)
            else:
                wait_for_tokens = 0.0

            # Wait for minimum spacing
            wait_for_spacing = max(0.0, self.min_spacing - time_since_last)

            # Wait for the longer of the two constraints
            sleep_for = max(wait_for_tokens, wait_for_spacing)

        time.sleep(sleep_for)
        self.acquire()


class AlphaVantageClient:
    """HTTP client for AlphaVantage."""

    def __init__(self, api_key: Optional[str] = None, calls_per_minute: int = 75, session: Optional[requests.Session] = None):
        self.api_key = api_key or os.getenv("ALPHAVANTAGE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ALPHAVANTAGE_API_KEY is required.\n"
                "\n"
                "To fix this:\n"
                "  1. Get a free API key from: https://www.alphavantage.co/support/#api-key\n"
                "  2. Add to your .env file: ALPHAVANTAGE_API_KEY=your_key_here\n"
                "  3. Or set environment variable: export ALPHAVANTAGE_API_KEY=your_key_here\n"
                "\n"
                "See: docs/USER_GUIDE.md#api-keys"
            )
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
