from fastapi import APIRouter, Request

from app.core.responses import api_response

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request):
    """Confirm both the application and its database are available."""
    database = request.app.state.database
    row = await database.fetch_one("SELECT 1 AS healthy")
    return api_response(data={"status": "ok", "database": row["healthy"] == 1})

