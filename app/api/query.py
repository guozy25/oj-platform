from fastapi import Request

from app.core.errors import APIError


def parse_pagination(request: Request) -> tuple[int | None, int | None]:
    raw_page = request.query_params.get("page")
    raw_page_size = request.query_params.get("page_size")
    if raw_page is not None and raw_page_size is None:
        raise APIError(400, "page_size is required when page is provided")

    try:
        page = int(raw_page) if raw_page is not None else None
        page_size = int(raw_page_size) if raw_page_size is not None else None
    except ValueError as exc:
        raise APIError(400, "invalid pagination parameters") from exc

    if page is not None and page < 1:
        raise APIError(400, "page must be positive")
    if page_size is not None and page_size < 1:
        raise APIError(400, "page_size must be positive")
    return page, page_size
