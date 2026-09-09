from datetime import datetime, timezone

import pytest

from app.main import create_app
from app.models.judge import SubmissionCreate
from app.repositories.problems import ProblemRepository
from app.services.submissions import SubmissionService
from tests.test_judge import problem_payload, submit, wait_for_result
from tests.test_users import login, register


async def create_problem(client, problem_id: str, *, sleep_limit: float | None = None) -> None:
    response = await client.post(
        "/api/problems/",
        json=problem_payload(
            problem_id,
            time_limit=sleep_limit,
            testcases=[{"input": "", "output": "done"}],
        ),
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_submission_detail_permissions_and_response_shape(client, app, test_settings):
    alice = (await register(client, "detail-alice")).json()["data"]
    await login(client, "detail-alice", "secret123")
    await create_problem(client, "detail-problem")

    created = await submit(
        client,
        "detail-problem",
        "import time\ntime.sleep(0.3)\nprint('done')",
    )
    submission_id = created.json()["data"]["submission_id"]

    pending = await client.get(f"/api/submissions/{submission_id}")
    assert pending.status_code == 200
    assert pending.json()["data"] == {
        "submission_id": submission_id,
        "status": "pending",
        "score": None,
        "counts": None,
        "compile_info": None,
        "run_info": None,
        "error_info": None,
    }

    await wait_for_result(app, submission_id)
    finished = await client.get(f"/api/submissions/{submission_id}")
    assert finished.status_code == 200
    assert finished.json()["data"] == {
        "submission_id": submission_id,
        "status": "success",
        "score": 10,
        "counts": 10,
        "compile_info": None,
        "run_info": {"result": "finished", "message": "1 test cases finished"},
        "error_info": "",
    }

    bob = (await register(client, "detail-bob")).json()["data"]
    await client.post("/api/auth/logout")
    await login(client, "detail-bob", "secret123")
    assert (await client.get(f"/api/submissions/{submission_id}")).status_code == 403
    assert (await client.get("/api/submissions/not-a-real-id")).status_code == 404

    await client.post("/api/auth/logout")
    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )
    assert (await client.get(f"/api/submissions/{submission_id}")).status_code == 200
    assert (await client.get("/api/submissions/not-a-real-id")).status_code == 404

    await client.post("/api/auth/logout")
    assert (await client.get(f"/api/submissions/{submission_id}")).status_code == 401
    assert alice["user_id"] != bob["user_id"]


@pytest.mark.asyncio
async def test_submission_list_filters_pagination_and_summaries(client, app, test_settings):
    alice = (await register(client, "list-alice")).json()["data"]
    await login(client, "list-alice", "secret123")
    await create_problem(client, "list-problem-a")
    await create_problem(client, "list-problem-b")

    created = [
        await submit(client, "list-problem-a", "print('done')"),
        await submit(client, "list-problem-a", "print('wrong')"),
        await submit(client, "list-problem-b", "print('done')"),
    ]
    alice_ids = [response.json()["data"]["submission_id"] for response in created]
    for submission_id in alice_ids:
        await wait_for_result(app, submission_id)

    now = datetime.now(timezone.utc).isoformat()
    await app.state.database.execute(
        """
        UPDATE submissions
        SET status = 'pending', score = NULL, counts = NULL,
            compile_info = NULL, run_info = NULL, error_info = NULL, updated_at = ?
        WHERE submission_id = ?
        """,
        (now, alice_ids[1]),
    )
    await app.state.database.execute(
        """
        UPDATE submissions
        SET status = 'error', score = NULL, counts = NULL,
            compile_info = NULL, run_info = NULL,
            error_info = 'judge internal error', updated_at = ?
        WHERE submission_id = ?
        """,
        (now, alice_ids[2]),
    )

    bob = (await register(client, "list-bob")).json()["data"]
    await client.post("/api/auth/logout")
    await login(client, "list-bob", "secret123")
    bob_submission = await submit(client, "list-problem-a", "print('done')")
    bob_id = bob_submission.json()["data"]["submission_id"]
    await wait_for_result(app, bob_id)

    await client.post("/api/auth/logout")
    await login(client, "list-alice", "secret123")

    assert (await client.get("/api/submissions/")).status_code == 400
    assert (
        await client.get("/api/submissions/", params={"user_id": bob["user_id"], "page": "bad"})
    ).status_code == 403
    assert (
        await client.get("/api/submissions/", params={"problem_id": "list-problem-a", "page": 2})
    ).status_code == 400

    own_problem = await client.get("/api/submissions/", params={"problem_id": "list-problem-a"})
    assert own_problem.status_code == 200
    assert own_problem.json()["data"]["total"] == 2
    assert {item["submission_id"] for item in own_problem.json()["data"]["submissions"]} == {
        alice_ids[0],
        alice_ids[1],
    }

    own_all = await client.get("/api/submissions/", params={"user_id": alice["user_id"]})
    summaries = {item["submission_id"]: item for item in own_all.json()["data"]["submissions"]}
    assert own_all.json()["data"]["total"] == 3
    assert set(summaries[alice_ids[0]]) == {"submission_id", "status", "score", "counts"}
    assert summaries[alice_ids[1]] == {
        "submission_id": alice_ids[1],
        "status": "pending",
    }
    assert summaries[alice_ids[2]] == {
        "submission_id": alice_ids[2],
        "status": "error",
    }

    first_page = await client.get(
        "/api/submissions/",
        params={"user_id": alice["user_id"], "page_size": 1},
    )
    assert first_page.status_code == 200
    assert first_page.json()["data"]["total"] == 3
    assert len(first_page.json()["data"]["submissions"]) == 1

    await client.post("/api/auth/logout")
    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )
    all_for_problem = await client.get("/api/submissions/", params={"problem_id": "list-problem-a"})
    assert all_for_problem.status_code == 200
    assert all_for_problem.json()["data"]["total"] == 3
    assert bob_id in {
        item["submission_id"] for item in all_for_problem.json()["data"]["submissions"]
    }

    only_errors = await client.get(
        "/api/submissions/",
        params={"user_id": alice["user_id"], "status": "error"},
    )
    assert only_errors.json()["data"] == {
        "total": 1,
        "submissions": [{"submission_id": alice_ids[2], "status": "error"}],
    }
    assert (
        await client.get(
            "/api/submissions/",
            params={"problem_id": "list-problem-a", "status": "unknown"},
        )
    ).status_code == 400

    await client.post("/api/auth/logout")
    assert (await client.get("/api/submissions/")).status_code == 401


@pytest.mark.asyncio
async def test_admin_rejudge_reuses_submission_and_replaces_results(client, app, test_settings):
    await register(client, "rejudge-user")
    await login(client, "rejudge-user", "secret123")
    await create_problem(client, "rejudge-problem")
    created = await submit(
        client,
        "rejudge-problem",
        "import time\ntime.sleep(0.2)\nprint('done')",
    )
    submission_id = created.json()["data"]["submission_id"]
    await wait_for_result(app, submission_id)

    assert (await client.put(f"/api/submissions/{submission_id}/rejudge")).status_code == 403
    assert (await client.put("/api/submissions/missing/rejudge")).status_code == 403

    await client.post("/api/auth/logout")
    await login(
        client,
        test_settings.initial_admin_username,
        test_settings.initial_admin_password,
    )
    rejudged = await client.put(f"/api/submissions/{submission_id}/rejudge")
    assert rejudged.status_code == 200
    assert rejudged.json() == {
        "code": 200,
        "msg": "rejudge started",
        "data": {"submission_id": submission_id, "status": "pending"},
    }
    final_row = await wait_for_result(app, submission_id)
    assert final_row["status"] == "success"
    assert final_row["score"] == 10
    details = await app.state.database.fetch_all(
        "SELECT testcase_id, result FROM testcase_results WHERE submission_id = ?",
        (submission_id,),
    )
    assert [(row["testcase_id"], row["result"]) for row in details] == [(1, "AC")]
    assert (await client.put("/api/submissions/missing/rejudge")).status_code == 404

    await client.post("/api/auth/logout")
    assert (await client.put(f"/api/submissions/{submission_id}/rejudge")).status_code == 401


@pytest.mark.asyncio
async def test_pending_submission_is_resumed_after_restart(client, app, test_settings):
    user = (await register(client, "restart-user")).json()["data"]
    await login(client, "restart-user", "secret123")
    await create_problem(client, "restart-problem")
    service = SubmissionService(
        app.state.database,
        ProblemRepository(test_settings.problems_dir),
    )
    pending = await service.create(
        SubmissionCreate(
            problem_id="restart-problem",
            language="python",
            code="print('done')",
        ),
        user["user_id"],
    )

    restarted_app = create_app(test_settings)
    async with restarted_app.router.lifespan_context(restarted_app):
        result = await wait_for_result(restarted_app, pending["submission_id"])
        assert result["status"] == "success"
        assert result["score"] == 10
