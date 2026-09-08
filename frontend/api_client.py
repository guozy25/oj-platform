from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True, slots=True)
class APIResponse:
    code: int
    msg: str
    data: Any


class APIClientError(RuntimeError):
    """A transport failure or an error returned by the OJ API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.data = data


def normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("后端地址必须是完整的 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("请勿在后端地址中携带账号或密码")
    return base_url


class OJAPIClient:
    """Synchronous API client whose cookie jar survives Streamlit reruns."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            trust_env=False,
        )

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> APIResponse:
        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx.RequestError as exc:
            raise APIClientError(
                f"无法连接后端：{exc}",
                status_code=None,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise APIClientError(
                "后端返回了无法解析的响应",
                status_code=response.status_code,
            ) from exc

        if not isinstance(payload, dict):
            raise APIClientError(
                "后端响应格式不正确",
                status_code=response.status_code,
            )

        code = payload.get("code")
        msg = payload.get("msg")
        if not isinstance(code, int) or not isinstance(msg, str) or "data" not in payload:
            raise APIClientError(
                "后端响应缺少 code、msg 或 data",
                status_code=response.status_code,
            )
        if code != response.status_code:
            raise APIClientError(
                "后端 HTTP 状态与响应 code 不一致",
                status_code=response.status_code,
                data=payload.get("data"),
            )

        result = APIResponse(code=code, msg=msg, data=payload["data"])
        if response.is_error:
            raise APIClientError(msg, status_code=code, data=result.data)
        return result

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> APIResponse:
        return self.request("GET", path, params=params)

    def post(self, path: str, *, json: Any = None) -> APIResponse:
        return self.request("POST", path, json=json)

    def put(self, path: str, *, json: Any = None) -> APIResponse:
        return self.request("PUT", path, json=json)

    def delete(self, path: str) -> APIResponse:
        return self.request("DELETE", path)
