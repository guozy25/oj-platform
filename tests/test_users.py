from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


async def register(client, username: str, password: str = "secret123"):
    return await client.post("/api/users/", json={"username": username, "password": password})


async def login(client, username: str, password: str):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.mark.asyncio
async def test_registration_login_self_query_and_logout(client, test_settings):
    response = await register(client, "alice", "alice-password")
    assert response.status_code == 200
    user = response.json()["data"]
    assert user["username"] == "alice"
    assert user["role"] == "user"
    assert user["submit_count"] == 0
    assert user["resolve_count"] == 0
    assert "password" not in response.text

    duplicate = await register(client, "ALICE", "different-password")
    assert duplicate.status_code == 400
    assert duplicate.json()["code"] == 400

    wrong_password = await login(client, "alice", "wrong-password")
    assert wrong_password.status_code == 401
    unknown_user = await login(client, "unknown-user", "wrong-password")
    assert unknown_user.status_code == 401
    assert unknown_user.json() == wrong_password.json()

    logged_in = await login(client, "alice", "alice-password")
    assert logged_in.status_code == 200
    assert logged_in.json()["data"]["user_id"] == user["user_id"]
    assert test_settings.session_cookie_name in client.cookies
    assert "HttpOnly" in logged_in.headers["set-cookie"]

    profile = await client.get(f"/api/users/{user['user_id']}")
    assert profile.status_code == 200
    assert profile.json()["data"] == user

    logged_out = await client.post("/api/auth/logout")
    assert logged_out.status_code == 200
    assert logged_out.json() == {"code": 200, "msg": "logout success", "data": None}
    assert test_settings.session_cookie_name not in client.cookies

    after_logout = await client.get(f"/api/users/{user['user_id']}")
    assert after_logout.status_code == 401


@pytest.mark.asyncio
async def test_registration_validation(client):
    invalid_payloads = [
        {"username": "ab", "password": "secret123"},
        {"username": "   ", "password": "secret123"},
        {"username": "valid-name", "password": "short"},
        {"username": "valid-name"},
        {"username": "valid-name", "password": "secret123", "role": "admin"},
    ]

    for payload in invalid_payloads:
        response = await client.post("/api/users/", json=payload)
        assert response.status_code == 400
        assert response.json()["code"] == 400

    long_password = "密" * 512
    registered = await register(client, "long-password-user", long_password)
    assert registered.status_code == 200
    logged_in = await login(client, "long-password-user", long_password)
    assert logged_in.status_code == 200


@pytest.mark.asyncio
async def test_user_counts_follow_submission_semantics(client, app):
    user = (await register(client, "counted-user")).json()["data"]
    now = datetime.now(timezone.utc).isoformat()
    submissions = [
        (str(uuid4()), "problem-a", "success", 10, 10),
        (str(uuid4()), "problem-a", "success", 0, 10),
        (str(uuid4()), "problem-b", "success", 20, 20),
        (str(uuid4()), "problem-c", "error", None, None),
    ]
    async with app.state.database.connection() as connection:
        await connection.executemany(
            """
            INSERT INTO submissions (
                submission_id, user_id, problem_id, language, code, status,
                score, counts, compile_info, run_info, error_info, created_at, updated_at
            ) VALUES (?, ?, ?, 'python', 'print(1)', ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            [
                (submission_id, user["user_id"], problem_id, status, score, counts, now, now)
                for submission_id, problem_id, status, score, counts in submissions
            ],
        )
        await connection.commit()

    await login(client, "counted-user", "secret123")
    profile = await client.get(f"/api/users/{user['user_id']}")
    assert profile.status_code == 200
    assert profile.json()["data"]["submit_count"] == 4
    assert profile.json()["data"]["resolve_count"] == 2


@pytest.mark.asyncio
async def test_user_information_is_private(client, test_settings):
    alice = (await register(client, "alice")).json()["data"]
    bob = (await register(client, "bob-user")).json()["data"]
    await login(client, "alice", "secret123")

    forbidden = await client.get(f"/api/users/{bob['user_id']}")
    assert forbidden.status_code == 403

    # Permission is checked before existence so ordinary users cannot enumerate IDs.
    hidden_missing_user = await client.get("/api/users/does-not-exist")
    assert hidden_missing_user.status_code == 403

    await client.post("/api/auth/logout")
    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )
    visible = await client.get(f"/api/users/{alice['user_id']}")
    assert visible.status_code == 200
    missing = await client.get("/api/users/does-not-exist")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_admin_creation_role_updates_and_error_priority(client, app, test_settings):
    user = (await register(client, "regular-user")).json()["data"]

    unauthenticated = await client.put(
        f"/api/users/{user['user_id']}/role", json={"role": "not-a-role"}
    )
    assert unauthenticated.status_code == 401

    await login(client, "regular-user", "secret123")
    non_admin = await client.put(f"/api/users/{user['user_id']}/role", json={"role": "not-a-role"})
    assert non_admin.status_code == 403
    cannot_create_admin = await client.post(
        "/api/users/admin", json={"username": "second-admin", "password": "secret123"}
    )
    assert cannot_create_admin.status_code == 403

    await client.post("/api/auth/logout")
    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )
    invalid_role = await client.put(
        f"/api/users/{user['user_id']}/role", json={"role": "not-a-role"}
    )
    assert invalid_role.status_code == 400

    new_admin = await client.post(
        "/api/users/admin", json={"username": "second-admin", "password": "secret123"}
    )
    assert new_admin.status_code == 200
    assert new_admin.json()["data"]["username"] == "second-admin"

    banned = await client.put(f"/api/users/{user['user_id']}/role", json={"role": "banned"})
    assert banned.status_code == 200
    assert banned.json()["data"] == {"user_id": user["user_id"], "role": "banned"}
    audit = await app.state.database.fetch_one(
        """
        SELECT actor_user_id, target_user_id, old_role, new_role
        FROM role_change_logs
        WHERE target_user_id = ?
        """,
        (user["user_id"],),
    )
    assert audit is not None
    assert audit["target_user_id"] == user["user_id"]
    assert audit["old_role"] == "user"
    assert audit["new_role"] == "banned"

    await client.post("/api/auth/logout")
    banned_login = await login(client, "regular-user", "secret123")
    assert banned_login.status_code == 403


@pytest.mark.asyncio
async def test_role_changes_affect_existing_sessions_immediately(client, app, test_settings):
    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )
    created = await client.post(
        "/api/users/admin",
        json={"username": "session-admin", "password": "secret123"},
    )
    assert created.status_code == 200
    deputy_id = created.json()["data"]["user_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://deputy") as deputy_client:
        assert (
            await deputy_client.post(
                "/api/auth/login",
                json={"username": "session-admin", "password": "secret123"},
            )
        ).status_code == 200
        assert (await deputy_client.get("/api/users/")).status_code == 200

        demoted = await client.put(
            f"/api/users/{deputy_id}/role",
            json={"role": "user"},
        )
        assert demoted.status_code == 200
        assert (await deputy_client.get("/api/users/")).status_code == 403

        promoted = await client.put(
            f"/api/users/{deputy_id}/role",
            json={"role": "admin"},
        )
        assert promoted.status_code == 200
        assert (await deputy_client.get("/api/users/")).status_code == 200

        banned = await client.put(
            f"/api/users/{deputy_id}/role",
            json={"role": "banned"},
        )
        assert banned.status_code == 200
        assert (await deputy_client.get(f"/api/users/{deputy_id}")).status_code == 401
        assert (
            await deputy_client.post(
                "/api/auth/login",
                json={"username": "session-admin", "password": "secret123"},
            )
        ).status_code == 403

        restored = await client.put(
            f"/api/users/{deputy_id}/role",
            json={"role": "user"},
        )
        assert restored.status_code == 200
        assert (
            await deputy_client.post(
                "/api/auth/login",
                json={"username": "session-admin", "password": "secret123"},
            )
        ).status_code == 200

    audit_rows = await app.state.database.fetch_all(
        """
        SELECT old_role, new_role, changed_at
        FROM role_change_logs
        WHERE target_user_id = ?
        ORDER BY change_id
        """,
        (deputy_id,),
    )
    assert [(row["old_role"], row["new_role"]) for row in audit_rows] == [
        ("admin", "user"),
        ("user", "admin"),
        ("admin", "banned"),
        ("banned", "user"),
    ]
    assert all(row["changed_at"] for row in audit_rows)


@pytest.mark.asyncio
async def test_admin_user_list_pagination(client, test_settings):
    for username in ("alice", "bob-user", "charlie"):
        response = await register(client, username)
        assert response.status_code == 200

    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )

    all_users = await client.get("/api/users/")
    assert all_users.status_code == 200
    assert all_users.json()["data"]["total"] == 4
    assert len(all_users.json()["data"]["users"]) == 4

    first_page = await client.get("/api/users/", params={"page_size": 2})
    assert first_page.status_code == 200
    assert first_page.json()["data"]["total"] == 4
    assert len(first_page.json()["data"]["users"]) == 2

    second_page = await client.get("/api/users/", params={"page": 2, "page_size": 2})
    assert second_page.status_code == 200
    assert len(second_page.json()["data"]["users"]) == 2

    invalid_combinations = [
        {"page": 2},
        {"page": 0, "page_size": 2},
        {"page_size": 0},
        {"page": "bad", "page_size": 2},
    ]
    for query in invalid_combinations:
        response = await client.get("/api/users/", params=query)
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_user_list_requires_admin(client):
    await register(client, "regular-user")
    await login(client, "regular-user", "secret123")

    response = await client.get("/api/users/", params={"page": "bad"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_expired_session_is_rejected_and_removed(client, app, test_settings):
    user = (await register(client, "alice")).json()["data"]
    await login(client, "alice", "secret123")
    session_id = client.cookies[test_settings.session_cookie_name]
    await app.state.database.execute(
        "UPDATE sessions SET expires_at = ? WHERE session_id = ?",
        (datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(), session_id),
    )

    response = await client.get(f"/api/users/{user['user_id']}")
    assert response.status_code == 401
    stored_session = await app.state.database.fetch_one(
        "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
    )
    assert stored_session is None
