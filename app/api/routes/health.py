from fastapi import APIRouter, Request

from app.api.dependencies.auth import AdminUserDependency
from app.core.responses import api_response
from app.services.system import SystemResetService

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request):
    """Confirm both the application and its database are available."""
    database = request.app.state.database
    row = await database.fetch_one("SELECT 1 AS healthy")
    return api_response(data={"status": "ok", "database": row["healthy"] == 1})


@router.post("/reset/")
async def reset_system(request: Request, _admin: AdminUserDependency):
    await SystemResetService(request.app).reset()
    settings = request.app.state.settings
    response = api_response(msg="system reset successfully")
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return response
