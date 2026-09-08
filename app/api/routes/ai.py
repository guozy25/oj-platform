from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import CurrentUserDependency
from app.core.responses import api_response
from app.models.ai import ModelConfigUpdate, ProblemTaskCreate
from app.services.ai_tasks import AITaskService

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/model-config")
async def get_model_config(request: Request, current_user: CurrentUserDependency):
    data = AITaskService(request.app).get_model_config(current_user.user_id)
    return api_response(data=data)


@router.put("/model-config")
async def update_model_config(request: Request, current_user: CurrentUserDependency):
    config = await parse_json_body(request, ModelConfigUpdate)
    data = AITaskService(request.app).configure_model(current_user.user_id, config)
    return api_response(msg="model config updated", data=data)
@router.post("/problem-tasks/")
async def create_problem_task(request: Request, current_user: CurrentUserDependency):
    task = await parse_json_body(request, ProblemTaskCreate)
    data = await AITaskService(request.app).create_task(task, current_user)
    return api_response(msg="task created", data=data)


@router.get("/problem-tasks/")
async def list_problem_tasks(request: Request, current_user: CurrentUserDependency):
    data = await AITaskService(request.app).list_tasks(current_user)
    return api_response(data=data)


@router.get("/problem-tasks/{task_id}")
async def get_problem_task(
    task_id: str,
    request: Request,
    current_user: CurrentUserDependency,
):
    data = await AITaskService(request.app).get_task(task_id, current_user)
    return api_response(data=data)


@router.put("/problem-tasks/{task_id}/cancel")
async def cancel_problem_task(
    task_id: str,
    request: Request,
    current_user: CurrentUserDependency,
):
    data = await AITaskService(request.app).cancel_task(task_id, current_user)
    return api_response(msg="task cancelled", data=data)
