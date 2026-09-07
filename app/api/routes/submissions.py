import asyncio

from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import CurrentUserDependency
from app.core.responses import api_response
from app.models.judge import SubmissionCreate
from app.repositories.problems import ProblemRepository
from app.services.judge import JudgeService
from app.services.submissions import SubmissionService

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _track_task(request: Request, task: asyncio.Task) -> None:
    tasks = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.post("/")
async def create_submission(request: Request, current_user: CurrentUserDependency):
    submission = await parse_json_body(request, SubmissionCreate)
    repository = ProblemRepository(request.app.state.settings.problems_dir)
    data = await SubmissionService(request.app.state.database, repository).create(
        submission, current_user.user_id
    )

    judge = JudgeService(
        request.app.state.database,
        repository,
        request.app.state.settings,
    )
    task = asyncio.create_task(judge.judge_submission(data["submission_id"]))
    _track_task(request, task)
    return api_response(data=data)
