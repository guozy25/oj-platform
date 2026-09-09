import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.test_problems import login_as_admin, problem_payload
from tests.test_users import login, register


@pytest.mark.asyncio
async def test_system_reset_requires_an_admin(client):
    assert (await client.post("/api/reset/")).status_code == 401

    await register(client, "regular-user")
    await login(client, "regular-user", "secret123")
    forbidden = await client.post("/api/reset/")
    assert forbidden.status_code == 403
    assert forbidden.json() == {"code": 403, "msg": "permission denied", "data": None}


@pytest.mark.asyncio
async def test_system_reset_restores_initial_state(client, app, test_settings):
    regular_user = (await register(client, "reset-user")).json()["data"]
    await login_as_admin(client, test_settings)
    old_admin_id = (await client.get("/api/users/")).json()["data"]["users"][0]["user_id"]

    created_problem = await client.post(
        "/api/problems/", json=problem_payload("reset-problem")
    )
    assert created_problem.status_code == 200
    assert (
        await client.post(
            "/api/languages/",
            json={
                "name": "pypy",
                "file_ext": ".py",
                "run_cmd": "pypy3 {src}",
            },
        )
    ).status_code == 200

    now = datetime.now(timezone.utc).isoformat()
    submission_id = str(uuid4())
    async with app.state.database.connection() as connection:
        await connection.execute(
            """
            INSERT INTO submissions (
                submission_id, user_id, problem_id, language, code, status,
                score, counts, compile_info, run_info, error_info, created_at, updated_at
            ) VALUES (?, ?, 'reset-problem', 'pypy', 'print(3)', 'success',
                      20, 20, NULL, NULL, NULL, ?, ?)
            """,
            (submission_id, regular_user["user_id"], now, now),
        )
        await connection.execute(
            """
            INSERT INTO testcase_results (
                submission_id, testcase_id, result, time_seconds, memory_mb
            ) VALUES (?, 1, 'AC', 0.01, 1.0)
            """,
            (submission_id,),
        )
        await connection.execute(
            """
            INSERT INTO access_logs (
                user_id, problem_id, submission_id, accessed_at, status
            ) VALUES (?, 'reset-problem', ?, ?, 'allowed')
            """,
            (old_admin_id, submission_id, now),
        )
        await connection.execute(
            """
            INSERT INTO role_change_logs (
                actor_user_id, target_user_id, old_role, new_role, changed_at
            ) VALUES (?, ?, 'user', 'banned', ?)
            """,
            (old_admin_id, regular_user["user_id"], now),
        )
        await connection.execute(
            """
            INSERT INTO ai_tasks (
                task_id, user_id, status, requirement, problem_id, progress,
                result, usage, error_info, created_at, updated_at
            ) VALUES ('reset-ai-task', ?, 'completed', 'test', 'reset-problem',
                      'done', '{}', '{}', NULL, ?, ?)
            """,
            (regular_user["user_id"], now, now),
        )
        await connection.commit()

    waiting_task = asyncio.create_task(asyncio.Event().wait())
    app.state.background_tasks.add(waiting_task)
    app.state.judge_tasks[submission_id] = waiting_task
    app.state.ai_task_handles["reset-ai-task"] = waiting_task
    app.state.ai_model_configs[regular_user["user_id"]] = object()

    reset = await client.post("/api/reset/")

    assert reset.status_code == 200
    assert reset.json() == {
        "code": 200,
        "msg": "system reset successfully",
        "data": None,
    }
    assert test_settings.session_cookie_name not in client.cookies
    assert waiting_task.cancelled()
    assert app.state.background_tasks == set()
    assert app.state.judge_tasks == {}
    assert app.state.ai_task_handles == {}
    assert app.state.ai_model_configs == {}
    assert list(test_settings.problems_dir.glob("*.json")) == []

    for table in (
        "sessions",
        "role_change_logs",
        "submissions",
        "testcase_results",
        "access_logs",
        "ai_tasks",
    ):
        row = await app.state.database.fetch_one(f"SELECT COUNT(*) AS total FROM {table}")
        assert row["total"] == 0

    users = await app.state.database.fetch_all(
        "SELECT user_id, username, role FROM users ORDER BY username"
    )
    assert len(users) == 1
    assert users[0]["username"] == test_settings.initial_admin_username
    assert users[0]["role"] == "admin"
    assert users[0]["user_id"] != old_admin_id

    languages = await app.state.database.fetch_all("SELECT name FROM languages ORDER BY name")
    assert [row["name"] for row in languages] == ["cpp", "python"]

    assert (await login(client, "reset-user", "secret123")).status_code == 401
    assert (
        await login(
            client,
            test_settings.initial_admin_username,
            test_settings.initial_admin_password,
        )
    ).status_code == 200
