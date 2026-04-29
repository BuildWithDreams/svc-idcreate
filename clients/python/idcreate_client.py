import json
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


class IdCreateApiError(Exception):
    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body


@dataclass
class IdCreateClient:
    base_url: str
    api_key: str | None = None
    timeout_seconds: int = 10

    def _headers(self, include_json: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_json:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = urllib_request.Request(url=url, method=method, data=body)
        for k, v in self._headers().items():
            req.add_header(k, v)

        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib_error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8") if exc.fp else ""
            parsed = None
            if raw_error:
                try:
                    parsed = json.loads(raw_error)
                except json.JSONDecodeError:
                    parsed = raw_error
            message = parsed.get("detail") if isinstance(parsed, dict) else str(parsed or exc)
            raise IdCreateApiError(exc.code, str(message), parsed) from exc
        except urllib_error.URLError as exc:
            raise IdCreateApiError(0, f"Transport error: {exc}") from exc

    def health(self, native_coin: str | None = None) -> dict[str, Any]:
        path = "/health"
        if native_coin:
            query = urllib_parse.urlencode({"native_coin": native_coin})
            path = f"{path}?{query}"
        return self._request("GET", path)

    def create_identity(
        self,
        name: str,
        parent: str,
        native_coin: str,
        primary_raddress: str,
        webhook_url: str | None = None,
        webhook_secret: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "parent": parent,
            "native_coin": native_coin,
            "primary_raddress": primary_raddress,
        }
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if webhook_secret is not None:
            payload["webhook_secret"] = webhook_secret
        return self._request("POST", "/api/register", payload)

    def get_identity_request_status(self, request_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/status/{request_id}")

    def list_recent_identity_failures(self, limit: int = 20) -> dict[str, Any]:
        query = urllib_parse.urlencode({"limit": limit})
        return self._request("GET", f"/api/registrations/failures?{query}")

    def requeue_identity_webhook(self, request_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/webhook/requeue/{request_id}", {})

    def create_storage_upload(
        self,
        name: str,
        parent: str,
        native_coin: str,
        primary_raddress: str,
        file_path: str,
        mime_type: str | None = "application/octet-stream",
        chunk_size_bytes: int = 999000,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "parent": parent,
            "native_coin": native_coin,
            "primary_raddress": primary_raddress,
            "file_path": file_path,
            "chunk_size_bytes": chunk_size_bytes,
        }
        if mime_type is not None:
            payload["mime_type"] = mime_type
        return self._request("POST", "/api/storage/upload", payload)

    def get_storage_upload_status(self, upload_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/storage/upload/{upload_id}")

    def start_storage_upload(self, upload_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/storage/upload/{upload_id}/start", {})

    def retry_storage_upload(self, upload_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/storage/upload/{upload_id}/retry", {})

    def retrieve_storage_upload(self, upload_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/storage/retrieve/{upload_id}")
