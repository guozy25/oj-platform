from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import AdminUserDependency, CurrentUserDependency
from app.api.query import parse_pagination
from app.core.errors import APIError
from app.core.responses import api_response
from app.models.auth import CurrentUser
from app.models.judge import SubmissionCreate
from app.repositories.problems import ProblemRepository
from app.services.judge_tasks import schedule_judge
from app.services.submissions import SubmissionService

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _submission_service(request: Request) -> SubmissionService:
    repository = ProblemRepository(request.app.state.settings.problems_dir)
    return SubmissionService(request.app.state.database, repository)


def _list_filters(
    request: Request, current_user: CurrentUser
) -> tuple[str | None, str | None, str | None, int | None, int | None]:
    requested_user_id = request.query_params.get("user_id")
    problem_id = request.query_params.get("problem_id")

    if requested_user_id == "" or problem_id == "":
        raise APIError(400, "invalid submission filters")
    if (
        current_user.role != "admin"
        and requested_user_id is not None
        and requested_user_id != current_user.user_id
    ):
        raise APIError(403, "permission denied")
    if requested_user_id is None and problem_id is None:
        raise APIError(400, "user_id or problem_id is required")

    effective_user_id = requested_user_id
    if current_user.role != "admin":
        effective_user_id = current_user.user_id

    status = request.query_params.get("status")
    if status is not None and status not in {"pending", "success", "error"}:
        raise APIError(400, "invalid submission status")
    page, page_size = parse_pagination(request)
    return effective_user_id, problem_id, status, page, page_size


@router.post("/")
async def create_submission(request: Request, current_user: CurrentUserDependency):
    submission = await parse_json_body(request, SubmissionCreate)
    repository = ProblemRepository(request.app.state.settings.problems_dir)
    data = await SubmissionService(request.app.state.database, repository).create(
        submission, current_user.user_id
    )
    await schedule_judge(request.app, data["submission_id"], repository)
    return api_response(data=data)


@router.get("/")
async def list_submissions(request: Request, current_user: CurrentUserDependency):
    user_id, problem_id, status, page, page_size = _list_filters(request, current_user)
    data = await _submission_service(request).list_submissions(
        user_id=user_id,
        problem_id=problem_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return api_response(data=data)


@router.get("/{submission_id}")
async def get_submission(
    submission_id: str,
    request: Request,
    current_user: CurrentUserDependency,
):
    data = await _submission_service(request).get_submission(
        submission_id, current_user.user_id, current_user.role
    )
    return api_response(data=data)


@router.put("/{submission_id}/rejudge")
async def rejudge_submission(
    submission_id: str,
    request: Request,
    _admin: AdminUserDependency,
):
    data = await _submission_service(request).prepare_rejudge(submission_id)
    await schedule_judge(request.app, submission_id)
    return api_response(msg="rejudge started", data=data)
