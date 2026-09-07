from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import CurrentUserDependency
from app.core.responses import api_response
from app.models.users import Credentials
from app.services.auth import AuthenticationService

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login")
async def login(request: Request):
    credentials = await parse_json_body(request, Credentials)
    settings = request.app.state.settings
    service = AuthenticationService(request.app.state.database, settings)
    user = await service.login(credentials.username, credentials.password)

    response = api_response(
        msg="login success",
        data={"user_id": user.user_id, "username": user.username, "role": user.role},
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=user.session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request, current_user: CurrentUserDependency):
    settings = request.app.state.settings
    service = AuthenticationService(request.app.state.database, settings)
    await service.logout(current_user.session_id)

    response = api_response(msg="logout success")
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return response
