"""
provisioning/adapters.py

Adapter boundary for provisioning primitive operations.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Protocol


logger = logging.getLogger(__name__)


class ProvisioningAdapter(Protocol):
    def build_challenge(self, input_data: dict) -> dict: ...

    def verify_request(self, request_json: dict) -> dict: ...

    def build_response(self, input_data: dict) -> dict: ...

    def base58check_encode(self, data_hex: str, version: int = 0) -> str: ...


class HttpProvisioningAdapter:
    """Scaffold adapter for calling an external provisioning HTTP service."""

    def __init__(self, base_url: str, timeout_seconds: int = 10, retry_count: int = 0, retry_delay_seconds: float = 0.25):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_count = max(0, int(retry_count))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))

    def _should_retry_http_error(self, status_code: int) -> bool:
        return status_code in {429, 500, 502, 503, 504}

    def _post_json(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw = resp.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
                if attempt < self.retry_count and self._should_retry_http_error(e.code):
                    logger.warning(
                        "http adapter retry attempt=%s path=%s status=%s",
                        attempt + 1,
                        path,
                        e.code,
                    )
                    time.sleep(self.retry_delay_seconds)
                    last_error = e
                    continue
                raise RuntimeError(f"HTTP adapter error ({e.code}) for {path}: {error_body}") from e
            except urllib.error.URLError as e:
                if attempt < self.retry_count:
                    logger.warning("http adapter retry attempt=%s path=%s reason=%s", attempt + 1, path, e)
                    time.sleep(self.retry_delay_seconds)
                    last_error = e
                    continue
                raise RuntimeError(f"HTTP adapter connection error for {path}: {e}") from e

        if last_error is not None and 'raw' not in locals():
            raise RuntimeError(f"HTTP adapter request failed for {path}: {last_error}")

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"HTTP adapter invalid JSON for {path}: {raw[:200]}") from e

    def build_challenge(self, input_data: dict) -> dict:
        return self._post_json("/v1/provisioning/challenge/build", input_data)

    def verify_request(self, request_json: dict) -> dict:
        return self._post_json("/v1/provisioning/request/verify", {"request_json": request_json})

    def build_response(self, input_data: dict) -> dict:
        return self._post_json("/v1/provisioning/response/build", input_data)

    def base58check_encode(self, data_hex: str, version: int = 0) -> str:
        result = self._post_json(
            "/v1/base58check/encode",
            {
                "data_hex": data_hex,
                "version": version,
            },
        )
        if "result" not in result:
            raise RuntimeError("HTTP adapter encode response missing 'result'")
        return result["result"]