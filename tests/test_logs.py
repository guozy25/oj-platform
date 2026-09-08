import json

import pytest

from tests.test_judge import problem_payload, submit, wait_for_result
from tests.test_problems import login_as_admin
from tests.test_users import login, register


@pytest.mark.asyncio
async def test_log_endpoints_require_authentication_before_validation(client):
    assert (await client.get("/api/submissions/missing/log")).status_code == 401
    assert (
        await client.put("/api/problems/missing/log_visibility", json={"public_cases": "bad"})
    ).status_code == 401
    assert (await client.get("/api/logs/access/", params={"page": "bad"})).status_code == 401


@pytest.mark.asyncio
async def test_only_visibility_endpoint_can_change_public_cases(client, test_settings):
    await register(client, "visibility-user")
    await login(client, "visibility-user", "secret123")
    payload = problem_payload("visibility-problem")
    assert (await client.post("/api/problems/", json=payload)).status_code == 200

    forbidden = await client.put(
        "/api/problems/visibility-problem/log_visibility",
        json={"public_cases": True},
    )
    assert forbidden.status_code == 403

    injected_create = problem_payload("injected-visibility")
    injected_create["public_cases"] = True
    assert (await client.post("/api/problems/", json=injected_create)).status_code == 400
    injected_update = {**payload, "public_cases": True}
    assert (
        await client.put("/api/problems/visibility-problem", json=injected_update)
    ).status_code == 400

    await client.post("/api/auth/logout")
    await login_as_admin(client, test_settings)
    assert (
        await client.put(
            "/api/problems/visibility-problem/log_visibility",
            json={"public_cases": "true"},
        )
    ).status_code == 400
    assert (
        await client.put(
            "/api/problems/visibility-problem/log_visibility",
            json={"public_cases": True, "unexpected": True},
        )
    ).status_code == 400
    assert (
        await client.put(
            "/api/problems/missing-problem/log_visibility",
            json={"public_cases": True},
        )
    ).status_code == 404

    visible = await client.put(
        "/api/problems/visibility-problem/log_visibility",
        json={"public_cases": True},
    )
    assert visible.json() == {
        "code": 200,
        "msg": "log visibility updated",
        "data": {"problem_id": "visibility-problem", "public_cases": True},
    }

    await client.post("/api/auth/logout")
    await login(client, "visibility-user", "secret123")
    updated_payload = {**payload, "title": "Updated without changing visibility"}
    assert (
        await client.put("/api/problems/visibility-problem", json=updated_payload)
    ).status_code == 200
    stored_path = test_settings.problems_dir / "visibility-problem.json"
    assert json.loads(stored_path.read_text(encoding="utf-8"))["public_cases"] is True
    assert (
        "public_cases" not in (await client.get("/api/problems/visibility-problem")).json()["data"]
    )

    await client.post("/api/auth/logout")
    await login_as_admin(client, test_settings)
    hidden = await client.put("/api/problems/visibility-problem/log_visibility", json={})
    assert hidden.json()["data"] == {
        "problem_id": "visibility-problem",
        "public_cases": False,
    }


@pytest.mark.asyncio
async def test_log_visibility_permissions_and_access_audit(client, app, test_settings):
    alice = (await register(client, "log-alice")).json()["data"]
    await login(client, "log-alice", "secret123")
    created_problem = await client.post(
        "/api/problems/",
        json=problem_payload("logged-problem"),
    )
    assert created_problem.status_code == 200
    created_submission = await submit(
        client,
        "logged-problem",
        "a, b = map(int, input().split())\nprint(a + b)",
    )
    submission_id = created_submission.json()["data"]["submission_id"]
    await wait_for_result(app, submission_id)

    private_owner_log = await client.get(f"/api/submissions/{submission_id}/log")
    assert private_owner_log.status_code == 200
    assert private_owner_log.json()["data"] == {"score": 20, "counts": 20}

    bob = (await register(client, "log-bob")).json()["data"]
    await client.post("/api/auth/logout")
    await login(client, "log-bob", "secret123")
    assert (await client.get(f"/api/submissions/{submission_id}/log")).status_code == 403
    assert (await client.get("/api/submissions/missing/log")).status_code == 404

    await client.post("/api/auth/logout")
    await login_as_admin(client, test_settings)
    admin_log = await client.get(f"/api/submissions/{submission_id}/log")
    assert admin_log.status_code == 200
    admin_data = admin_log.json()["data"]
    assert admin_data["score"] == admin_data["counts"] == 20
    assert [detail["result"] for detail in admin_data["details"]] == ["AC", "AC"]
    assert all(
        set(detail) == {"id", "result", "time", "memory"} for detail in admin_data["details"]
    )

    made_public = await client.put(
        "/api/problems/logged-problem/log_visibility",
        json={"public_cases": True},
    )
    assert made_public.status_code == 200

    await client.post("/api/auth/logout")
    await login(client, "log-bob", "secret123")
    public_log = await client.get(f"/api/submissions/{submission_id}/log")
    assert public_log.status_code == 200
    assert public_log.json()["data"]["details"] == admin_data["details"]
    # Public testcase logs do not make the Step 3 submission summary public.
    assert (await client.get(f"/api/submissions/{submission_id}")).status_code == 403
    assert (await client.get("/api/logs/access/", params={"page": "bad"})).status_code == 403

    await client.post("/api/auth/logout")
    await login_as_admin(client, test_settings)
    all_accesses = await client.get("/api/logs/access/")
    assert all_accesses.status_code == 200
    logs = all_accesses.json()["data"]
    assert len(logs) == 4
    assert all(set(item) == {"user_id", "problem_id", "action", "time", "status"} for item in logs)
    assert all(item["problem_id"] == "logged-problem" for item in logs)
    assert all(item["action"] == "view_logs" for item in logs)

    bob_accesses = await client.get("/api/logs/access/", params={"user_id": bob["user_id"]})
    assert sorted(item["status"] for item in bob_accesses.json()["data"]) == ["200", "403"]
    alice_accesses = await client.get(
        "/api/logs/access/",
        params={"user_id": alice["user_id"], "problem_id": "logged-problem"},
    )
    assert len(alice_accesses.json()["data"]) == 1
    assert alice_accesses.json()["data"][0]["status"] == "200"

    first_page = await client.get("/api/logs/access/", params={"page_size": 1})
    assert len(first_page.json()["data"]) == 1
    second_page = await client.get("/api/logs/access/", params={"page": 2, "page_size": 1})
    assert len(second_page.json()["data"]) == 1
    assert (await client.get("/api/logs/access/", params={"page": 2})).status_code == 400
    assert (await client.get("/api/logs/access/", params={"user_id": ""})).status_code == 400
    assert (await client.get("/api/logs/access/", params={"user_id": "not-a-user"})).json()[
        "data"
    ] == []

    stored_logs = await app.state.database.fetch_all(
        "SELECT submission_id, status FROM access_logs ORDER BY access_id"
    )
    assert len(stored_logs) == 4
    assert all(row["submission_id"] == submission_id for row in stored_logs)

    await client.post("/api/auth/logout")
    assert (await client.get("/api/logs/access/")).status_code == 401
