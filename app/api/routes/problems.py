from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import AdminUserDependency, CurrentUserDependency
from app.core.responses import api_response
from app.models.problems import LogVisibilityUpdate, ProblemInput
from app.repositories.problems import ProblemRepository
from app.services.problems import ProblemService

router = APIRouter(prefix="/problems", tags=["problems"])


def _problem_service(request: Request) -> ProblemService:
    repository = ProblemRepository(request.app.state.settings.problems_dir)
    return ProblemService(repository)


@router.get("/")
async def list_problems(request: Request, _current_user: CurrentUserDependency):
    data = await _problem_service(request).list_problems()
    return api_response(data=data)


@router.post("/")
async def create_problem(request: Request, _current_user: CurrentUserDependency):
    problem = await parse_json_body(request, ProblemInput)
    data = await _problem_service(request).create_problem(problem)
    return api_response(msg="add success", data=data)


@router.get("/{problem_id}")
async def get_problem(
    problem_id: str,
    request: Request,
    _current_user: CurrentUserDependency,
):
    data = await _problem_service(request).get_problem(problem_id)
    return api_response(data=data)


@router.put("/{problem_id}")
async def update_problem(
    problem_id: str,
    request: Request,
    _current_user: CurrentUserDependency,
):
    problem = await parse_json_body(request, ProblemInput)
    data = await _problem_service(request).update_problem(problem_id, problem)
    return api_response(msg="update success", data=data)


@router.put("/{problem_id}/log_visibility")
async def update_log_visibility(
    problem_id: str,
    request: Request,
    _admin: AdminUserDependency,
):
    visibility = await parse_json_body(request, LogVisibilityUpdate)
    data = await _problem_service(request).update_log_visibility(
        problem_id, visibility.public_cases
    )
    return api_response(msg="log visibility updated", data=data)


@router.get("/{problem_id}/log_visibility")
async def get_log_visibility(
    problem_id: str,
    request: Request,
    _admin: AdminUserDependency,
):
    data = await _problem_service(request).get_log_visibility(problem_id)
    return api_response(data=data)


@router.delete("/{problem_id}")
async def delete_problem(
    problem_id: str,
    request: Request,
    _admin: AdminUserDependency,
):
    data = await _problem_service(request).delete_problem(problem_id)
    return api_response(msg="delete success", data=data)
