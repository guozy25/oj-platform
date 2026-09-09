from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import CurrentUserDependency
from app.core.responses import api_response
from app.models.judge import LanguageCreate
from app.services.languages import LanguageService

router = APIRouter(prefix="/languages", tags=["languages"])


@router.post("/")
async def register_language(request: Request, current_user: CurrentUserDependency):
    language = await parse_json_body(request, LanguageCreate)
    settings = request.app.state.settings
    data = await LanguageService(
        request.app.state.database, settings.allowed_language_executables
    ).register(language, current_user.user_id)
    return api_response(msg="language registered", data=data)


@router.get("/")
async def list_languages(request: Request, _current_user: CurrentUserDependency):
    data = await LanguageService(request.app.state.database).list_names()
    return api_response(data=data)
