from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import AdminUserDependency, CurrentUserDependency
from app.core.errors import APIError
from app.core.responses import api_response
from app.models.users import Credentials, RoleUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


def _parse_pagination(request: Request) -> tuple[int | None, int | None]:
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


@router.post("/admin")
async def create_admin(request: Request, _admin: AdminUserDependency):
    credentials = await parse_json_body(request, Credentials)
    user = await UserService(request.app.state.database).create_user(
        credentials.username, credentials.password, role="admin"
    )
    return api_response(data={"user_id": user["user_id"], "username": user["username"]})


@router.post("/")
async def register_user(request: Request):
    credentials = await parse_json_body(request, Credentials)
    user = await UserService(request.app.state.database).create_user(
        credentials.username, credentials.password
    )
    return api_response(msg="register success", data=user)


@router.get("/")
async def list_users(request: Request, _admin: AdminUserDependency):
    page, page_size = _parse_pagination(request)
    data = await UserService(request.app.state.database).list_users(page, page_size)
    return api_response(data=data)


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    current_user: CurrentUserDependency,
):
    if current_user.role != "admin" and current_user.user_id != user_id:
        raise APIError(403, "permission denied")
    data = await UserService(request.app.state.database).get_user(user_id)
    return api_response(data=data)


@router.put("/{user_id}/role")
async def update_user_role(
    user_id: str,
    request: Request,
    _admin: AdminUserDependency,
):
    role_update = await parse_json_body(request, RoleUpdate)
    data = await UserService(request.app.state.database).update_role(
        user_id, role_update.role, _admin.user_id
    )
    return api_response(msg="role updated", data=data)
