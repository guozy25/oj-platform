import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


def problem_payload(problem_id: str = "P1001", title: str = "A+B Problem") -> dict:
    return {
        "id": problem_id,
        "title": title,
        "description": "Read two integers and print their sum.",
        "input_description": "Two integers a and b.",
        "output_description": "The value of a + b.",
        "samples": [{"input": "1 2", "output": "3"}],
        "constraints": "|a|, |b| <= 10^9",
        "testcases": [
            {"input": "1 2", "output": "3"},
            {"input": "-5 8", "output": "3"},
        ],
    }


async def register_and_login(client, username: str = "problem-author") -> None:
    registered = await client.post(
        "/api/users/", json={"username": username, "password": "secret123"}
    )
    assert registered.status_code == 200
    logged_in = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert logged_in.status_code == 200


async def login_as_admin(client, test_settings) -> None:
    logged_in = await client.post(
        "/api/auth/login",
        json={
            "username": test_settings.initial_admin_username,
            "password": test_settings.initial_admin_password,
        },
    )
    assert logged_in.status_code == 200


@pytest.mark.asyncio
async def test_problem_endpoints_require_login_before_validation(client):
    assert (await client.get("/api/problems/")).status_code == 401
    assert (await client.get("/api/problems/P1001")).status_code == 401

    invalid_create = await client.post("/api/problems/", json={})
    assert invalid_create.status_code == 401

    invalid_update = await client.put("/api/problems/P1001", json={})
    assert invalid_update.status_code == 401

    missing_delete = await client.delete("/api/problems/does-not-exist")
    assert missing_delete.status_code == 401


@pytest.mark.asyncio
async def test_create_list_get_and_update_problem(client, test_settings):
    await register_and_login(client)
    original = problem_payload()

    created = await client.post("/api/problems/", json=original)
    assert created.status_code == 200
    assert created.json() == {
        "code": 200,
        "msg": "add success",
        "data": {"id": "P1001"},
    }

    stored_path = test_settings.problems_dir / "P1001.json"
    assert stored_path.is_file()
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    assert stored["id"] == "P1001"
    assert stored["public_cases"] is False

    listing = await client.get("/api/problems/")
    assert listing.status_code == 200
    assert listing.json()["data"] == [{"id": "P1001", "title": "A+B Problem"}]

    detail = await client.get("/api/problems/P1001")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["hint"] == ""
    assert data["source"] == ""
    assert data["tags"] == []
    assert data["time_limit"] == 3.0
    assert data["memory_limit"] == 128
    assert data["author"] == ""
    assert data["difficulty"] == ""
    assert "public_cases" not in data

    updated_payload = problem_payload(title="Updated A+B")
    updated_payload.update({"tags": ["basic", "math"], "time_limit": 1.5, "memory_limit": 64})
    updated = await client.put("/api/problems/P1001", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["msg"] == "update success"

    updated_detail = await client.get("/api/problems/P1001")
    assert updated_detail.json()["data"]["title"] == "Updated A+B"
    assert updated_detail.json()["data"]["tags"] == ["basic", "math"]


@pytest.mark.asyncio
async def test_problem_validation_conflicts_and_error_order(client):
    await register_and_login(client)
    assert (await client.post("/api/problems/", json=problem_payload())).status_code == 200

    duplicate = await client.post("/api/problems/", json=problem_payload())
    assert duplicate.status_code == 409

    invalid_duplicate = problem_payload()
    invalid_duplicate.pop("title")
    invalid_before_conflict = await client.post("/api/problems/", json=invalid_duplicate)
    assert invalid_before_conflict.status_code == 400

    mismatched = await client.put(
        "/api/problems/missing-problem", json=problem_payload("different-id")
    )
    assert mismatched.status_code == 400

    missing = await client.put(
        "/api/problems/missing-problem", json=problem_payload("missing-problem")
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_rejects_invalid_problem_configurations(client):
    await register_and_login(client)
    invalid_payloads = []

    missing_required = problem_payload("missing-title")
    missing_required.pop("title")
    invalid_payloads.append(missing_required)

    path_traversal = problem_payload("safe-id")
    path_traversal["id"] = "../outside"
    invalid_payloads.append(path_traversal)

    empty_testcases = problem_payload("empty-tests")
    empty_testcases["testcases"] = []
    invalid_payloads.append(empty_testcases)

    malformed_sample = problem_payload("bad-sample")
    malformed_sample["samples"] = [{"input": "1 2"}]
    invalid_payloads.append(malformed_sample)

    invalid_limits = problem_payload("bad-limits")
    invalid_limits["time_limit"] = 0
    invalid_limits["memory_limit"] = -1
    invalid_payloads.append(invalid_limits)

    infinite_limit = problem_payload("infinite-limit")
    infinite_limit["time_limit"] = "Infinity"
    invalid_payloads.append(infinite_limit)

    unexpected_field = problem_payload("extra-field")
    unexpected_field["unexpected"] = True
    invalid_payloads.append(unexpected_field)

    for payload in invalid_payloads:
        response = await client.post("/api/problems/", json=payload)
        assert response.status_code == 400
        assert response.json()["code"] == 400


@pytest.mark.asyncio
async def test_only_admin_can_delete_problems(client, test_settings):
    await register_and_login(client)
    await client.post("/api/problems/", json=problem_payload())

    forbidden_existing = await client.delete("/api/problems/P1001")
    assert forbidden_existing.status_code == 403
    forbidden_missing = await client.delete("/api/problems/does-not-exist")
    assert forbidden_missing.status_code == 403

    await client.post("/api/auth/logout")
    await login_as_admin(client, test_settings)

    deleted = await client.delete("/api/problems/P1001")
    assert deleted.status_code == 200
    assert deleted.json() == {
        "code": 200,
        "msg": "delete success",
        "data": {"id": "P1001"},
    }
    assert (await client.get("/api/problems/P1001")).status_code == 404
    assert (await client.delete("/api/problems/P1001")).status_code == 404


@pytest.mark.asyncio
async def test_problem_files_persist_across_application_restarts(client, test_settings):
    await register_and_login(client)
    await client.post("/api/problems/", json=problem_payload("persistent-problem"))

    restarted_app = create_app(test_settings)
    async with restarted_app.router.lifespan_context(restarted_app):
        transport = ASGITransport(app=restarted_app)
        async with AsyncClient(transport=transport, base_url="http://restarted") as new_client:
            await login_as_admin(new_client, test_settings)
            response = await new_client.get("/api/problems/persistent-problem")

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "persistent-problem"


@pytest.mark.asyncio
async def test_problem_list_is_stable_and_stored_corruption_is_safe(client, test_settings):
    await register_and_login(client)
    await client.post("/api/problems/", json=problem_payload("problem-z", "Z problem"))
    await client.post("/api/problems/", json=problem_payload("problem-a", "A problem"))

    listing = await client.get("/api/problems/")
    assert [item["id"] for item in listing.json()["data"]] == ["problem-a", "problem-z"]

    corrupted_path = test_settings.problems_dir / "corrupted.json"
    corrupted_path.write_text("not valid JSON", encoding="utf-8")
    corrupted = await client.get("/api/problems/")
    assert corrupted.status_code == 500
    assert corrupted.json() == {
        "code": 500,
        "msg": "stored problem configuration is invalid",
        "data": None,
    }
