from fastapi import APIRouter, Request

from app.api.body import parse_json_body
from app.api.dependencies.auth import AdminUserDependency
from app.core.responses import api_response
from app.models.ai import (
    HabitConfigCreate,
    HabitConfigUpdate,
    ModelConfigUpdate,
    ProblemTaskCreate,
    ProblemTaskRefinement,
)
from app.services.ai_tasks import AITaskService

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/model-config")
async def get_model_config(request: Request, current_user: AdminUserDependency):
    data = AITaskService(request.app).get_model_config(current_user.user_id)
    return api_response(data=data)


@router.put("/model-config")
async def update_model_config(request: Request, current_user: AdminUserDependency):
    config = await parse_json_body(request, ModelConfigUpdate)
    data = AITaskService(request.app).configure_model(current_user.user_id, config)
    return api_response(msg="model config updated", data=data)


@router.get("/habit-configs/")
async def list_habit_configs(request: Request, current_user: AdminUserDependency):
    data = AITaskService(request.app).list_habit_configs(current_user.user_id)
    return api_response(data=data)


@router.post("/habit-configs/")
async def create_habit_config(request: Request, current_user: AdminUserDependency):
    config = await parse_json_body(request, HabitConfigCreate)
    data = AITaskService(request.app).create_habit_config(
        current_user.user_id, config
    )
    return api_response(msg="habit config created", data=data)


@router.put("/habit-configs/{config_id}")
async def update_habit_config(
    config_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    config = await parse_json_body(request, HabitConfigUpdate)
    data = AITaskService(request.app).update_habit_config(
        current_user.user_id, config_id, config
    )
    return api_response(msg="habit config updated", data=data)


@router.put("/habit-configs/{config_id}/select")
async def select_habit_config(
    config_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    data = AITaskService(request.app).select_habit_config(
        current_user.user_id, config_id
    )
    return api_response(msg="habit config selected", data=data)


@router.delete("/habit-configs/{config_id}")
async def delete_habit_config(
    config_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    data = AITaskService(request.app).delete_habit_config(
        current_user.user_id, config_id
    )
    return api_response(msg="habit config deleted", data=data)


@router.post("/problem-tasks/")
async def create_problem_task(request: Request, current_user: AdminUserDependency):
    task = await parse_json_body(request, ProblemTaskCreate)
    data = await AITaskService(request.app).create_task(task, current_user)
    return api_response(msg="task created", data=data)


@router.get("/problem-tasks/")
async def list_problem_tasks(request: Request, current_user: AdminUserDependency):
    data = await AITaskService(request.app).list_tasks(current_user)
    return api_response(data=data)


@router.get("/problem-tasks/{task_id}/revisions/")
async def list_problem_task_revisions(
    task_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    data = await AITaskService(request.app).list_revisions(task_id, current_user)
    return api_response(data=data)


@router.get("/problem-tasks/{task_id}/revisions/{revision}")
async def get_problem_task_revision(
    task_id: str,
    revision: int,
    request: Request,
    current_user: AdminUserDependency,
):
    data = await AITaskService(request.app).get_revision(task_id, revision, current_user)
    return api_response(data=data)


@router.post("/problem-tasks/{task_id}/refinements/")
async def refine_problem_task(
    task_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    refinement = await parse_json_body(request, ProblemTaskRefinement)
    data = await AITaskService(request.app).refine_task(
        task_id, refinement, current_user
    )
    return api_response(msg="refinement started", data=data)


@router.get("/problem-tasks/{task_id}")
async def get_problem_task(
    task_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    data = await AITaskService(request.app).get_task(task_id, current_user)
    return api_response(data=data)


@router.put("/problem-tasks/{task_id}/cancel")
async def cancel_problem_task(
    task_id: str,
    request: Request,
    current_user: AdminUserDependency,
):
    data = await AITaskService(request.app).cancel_task(task_id, current_user)
    return api_response(msg="task cancelled", data=data)
