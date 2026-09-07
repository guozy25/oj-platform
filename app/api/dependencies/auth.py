from typing import Annotated

from fastapi import Depends, Request

from app.core.errors import APIError
from app.models.auth import CurrentUser
from app.services.auth import AuthenticationService


async def get_current_user(request: Request) -> CurrentUser:
    settings = request.app.state.settings
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise APIError(401, "not logged in")

    service = AuthenticationService(request.app.state.database, settings)
    return await service.get_current_user(session_id)


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]


async def require_admin(current_user: CurrentUserDependency) -> CurrentUser:
    if current_user.role != "admin":
        raise APIError(403, "permission denied")
    return current_user


AdminUserDependency = Annotated[CurrentUser, Depends(require_admin)]
