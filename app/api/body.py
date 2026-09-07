from typing import TypeVar

from fastapi import Request
from pydantic import BaseModel, ValidationError

from app.core.errors import APIError

ModelT = TypeVar("ModelT", bound=BaseModel)


async def parse_json_body(request: Request, model_type: type[ModelT]) -> ModelT:
    """Validate JSON after authorization dependencies have already run."""
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(400, "invalid request body") from exc

    if not isinstance(payload, dict):
        raise APIError(400, "invalid request body")

    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise APIError(400, "invalid request parameters") from exc
