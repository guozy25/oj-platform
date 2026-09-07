from typing import Any

from fastapi.responses import JSONResponse


def api_response(
    *,
    data: Any = None,
    msg: str = "success",
    status_code: int = 200,
) -> JSONResponse:
    """Build the response envelope required by the assignment API contract."""
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "msg": msg, "data": data},
    )
