from fastapi import APIRouter, Request

from app.api.dependencies.auth import AdminUserDependency, CurrentUserDependency
from app.api.query import parse_pagination
from app.core.errors import APIError
from app.core.responses import api_response
from app.repositories.problems import ProblemRepository
from app.services.logs import LogService

router = APIRouter(tags=["logs"])


def _log_service(request: Request) -> LogService:
    repository = ProblemRepository(request.app.state.settings.problems_dir)
    return LogService(request.app.state.database, repository)


@router.get("/submissions/{submission_id}/log")
async def get_submission_log(
    submission_id: str,
    request: Request,
    current_user: CurrentUserDependency,
):
    data = await _log_service(request).get_submission_log(submission_id, current_user)
    return api_response(data=data)


@router.get("/logs/access/")
async def list_access_logs(request: Request, _admin: AdminUserDependency):
    user_id = request.query_params.get("user_id")
    problem_id = request.query_params.get("problem_id")
    if user_id == "" or problem_id == "":
        raise APIError(400, "invalid access log filters")
    page, page_size = parse_pagination(request)
    data = await _log_service(request).list_access_logs(
        user_id=user_id,
        problem_id=problem_id,
        page=page,
        page_size=page_size,
    )
    return api_response(data=data)
