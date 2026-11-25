import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import httpx

load_dotenv()

API_BASE_URL = os.getenv("LIJNET_API_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN_STORE_PATH = Path.home() / ".lijnet_mobile_tokens.json"


class ApiError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class LijnetApiClient:
    """Synchronous HTTPX client for /api/v1/* with automatic JWT access-token refresh."""

    def __init__(self, base_url: str = API_BASE_URL) -> None:
        self.base_url = base_url
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._load_tokens()

    def _load_tokens(self) -> None:
        if TOKEN_STORE_PATH.exists():
            try:
                data = json.loads(TOKEN_STORE_PATH.read_text(encoding="utf-8"))
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
            except Exception:
                self.clear_tokens()

    def save_tokens(self, access_token: str, refresh_token: str) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        TOKEN_STORE_PATH.write_text(
            json.dumps({"access_token": access_token, "refresh_token": refresh_token}),
            encoding="utf-8",
        )

    def clear_tokens(self) -> None:
        self.access_token = None
        self.refresh_token = None
        if TOKEN_STORE_PATH.exists():
            TOKEN_STORE_PATH.unlink(missing_ok=True)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    @staticmethod
    def _extract_error(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            detail = body.get("detail", resp.text)
            if isinstance(detail, list):
                return "; ".join(str(item.get("msg", item)) for item in detail)
            return str(detail)
        except Exception:
            return resp.text or f"HTTP {resp.status_code}"

    def _refresh_access_token(self) -> bool:
        if not self.refresh_token:
            return False
        try:
            with httpx.Client(timeout=12.0) as client:
                resp = client.post(
                    f"{self.base_url}/api/v1/auth/refresh",
                    json={"refresh_token": self.refresh_token},
                )
            if resp.status_code == 200:
                data = resp.json()
                self.save_tokens(data["access_token"], data["refresh_token"])
                return True
        except Exception:
            pass
        self.clear_tokens()
        return False

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._headers(),
                )
        except httpx.RequestError as exc:
            raise ApiError(f"Cannot reach Lijnet server at {self.base_url}: {exc}", 503) from exc

        if resp.status_code == 401 and retry_auth and not path.startswith("/api/v1/auth/"):
            if self._refresh_access_token():
                return self.request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    retry_auth=False,
                )
            raise ApiError("Session expired. Please sign in again.", 401)

        if resp.status_code >= 400:
            raise ApiError(self._extract_error(resp), resp.status_code)

        return resp.json()

    # --- Auth Endpoints ---
    def login(self, identifier: str, password: str) -> dict[str, Any]:
        data = self.request(
            "POST",
            "/api/v1/auth/login",
            json_body={"identifier": identifier, "password": password},
            retry_auth=False,
        )
        self.save_tokens(data["access_token"], data["refresh_token"])
        return data

    def register(
        self, full_name: str, email: str, phone_number: str, password: str
    ) -> dict[str, Any]:
        data = self.request(
            "POST",
            "/api/v1/auth/register",
            json_body={
                "full_name": full_name,
                "email": email,
                "phone_number": phone_number,
                "password": password,
            },
            retry_auth=False,
        )
        self.save_tokens(data["access_token"], data["refresh_token"])
        return data

    def get_me(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/auth/me")

    # --- Catalog & Delivery Config ---
    def get_products(
        self,
        lang: str = "en",
        age_range: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"lang": lang}
        if age_range:
            params["age_range"] = age_range
        if q and q.strip():
            params["q"] = q.strip()
        return self.request("GET", "/api/v1/products", params=params)

    def get_delivery_config(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/products/delivery-config")

    # --- Orders & Statutory 7-Day Returns ---
    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/v1/orders", json_body=payload)

    def get_orders(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/orders")

    def request_return(
        self, order_id: int, reason: str, hygiene_seal_broken: bool = False
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/v1/orders/{order_id}/return",
            json_body={
                "reason": reason,
                "hygiene_seal_broken": hygiene_seal_broken,
            },
        )