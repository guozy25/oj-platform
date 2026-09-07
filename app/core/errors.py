from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.responses import api_response

logger = logging.getLogger(__name__)


class APIError(Exception):
    """An expected API failure with an explicit HTTP status and safe payload."""

    def __init__(self, status_code: int, msg: str, data: Any = None) -> None:
        super().__init__(msg)
        self.status_code = status_code
        self.msg = msg
        self.data = data


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError):
        return api_response(data=exc.data, msg=exc.msg, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        # FastAPI normally returns 422. The course contract explicitly requires 400.
        return api_response(msg="invalid request parameters", status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        msg = detail if isinstance(detail, str) else "request failed"
        data = detail if isinstance(detail, dict) else None
        return api_response(data=data, msg=msg, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
        return api_response(msg="internal server error", status_code=500)
