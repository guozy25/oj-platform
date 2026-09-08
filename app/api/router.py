from fastapi import APIRouter

from app.api.routes.ai import router as ai_router
from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.languages import router as languages_router
from app.api.routes.logs import router as logs_router
from app.api.routes.problems import router as problems_router
from app.api.routes.submissions import router as submissions_router
from app.api.routes.users import router as users_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(ai_router)
api_router.include_router(users_router)
api_router.include_router(problems_router)
api_router.include_router(languages_router)
api_router.include_router(submissions_router)
api_router.include_router(logs_router)
