import inspect

import pytest
from fastapi import Query
from fastapi.routing import APIRoute, APIRouter

from app.api.router import api_router
from app.core.errors import APIError
from app.services.passwords import verify_password


@pytest.mark.asyncio
async def test_health_uses_required_response_envelope(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "msg": "success",
        "data": {"status": "ok", "database": True},
    }


@pytest.mark.asyncio
async def test_initial_admin_is_created_with_hashed_password(app, test_settings):
    row = await app.state.database.fetch_one(
        "SELECT username, password_hash, role FROM users WHERE username = ?",
        (test_settings.initial_admin_username,),
    )

    assert row is not None
    assert row["role"] == "admin"
    assert row["password_hash"] != test_settings.initial_admin_password
    assert verify_password(test_settings.initial_admin_password, row["password_hash"])


@pytest.mark.asyncio
async def test_initial_admin_creation_is_idempotent(app, test_settings):
    await app.state.database.ensure_initial_admin()
    rows = await app.state.database.fetch_all(
        "SELECT user_id FROM users WHERE username = ?",
        (test_settings.initial_admin_username,),
    )

    assert len(rows) == 1


@pytest.mark.asyncio
async def test_validation_errors_are_mapped_to_400(app, client):
    @app.get("/_validation-test")
    async def validation_test(value: int = Query(gt=0)):
        return {"value": value}

    response = await client.get("/_validation-test", params={"value": "invalid"})

    assert response.status_code == 400
    assert response.json() == {
        "code": 400,
        "msg": "invalid request parameters",
        "data": None,
    }


@pytest.mark.asyncio
async def test_api_errors_keep_http_and_json_codes_equal(app, client):
    @app.get("/_error-test")
    async def error_test():
        raise APIError(409, "resource already exists")

    response = await client.get("/_error-test")

    assert response.status_code == 409
    assert response.json()["code"] == response.status_code
    assert response.json()["msg"] == "resource already exists"


def _collect_api_routes(router: APIRouter) -> list[APIRoute]:
    """Traverse FastAPI's lazy included-router objects (introduced in 0.141)."""
    collected: list[APIRoute] = []
    for route in router.routes:
        if isinstance(route, APIRoute):
            collected.append(route)
            continue
        nested_router = getattr(route, "original_router", None)
        if isinstance(nested_router, APIRouter):
            collected.extend(_collect_api_routes(nested_router))
    return collected


def test_every_application_route_is_async():
    application_routes = _collect_api_routes(api_router)

    assert application_routes
    assert all(inspect.iscoroutinefunction(route.endpoint) for route in application_routes)
