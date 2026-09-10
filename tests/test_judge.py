import asyncio
import json
import shutil

import pytest


def problem_payload(
    problem_id: str,
    *,
    time_limit: float | None = None,
    memory_limit: int | None = None,
    code_length_limit: int | None = None,
    testcases: list[dict] | None = None,
) -> dict:
    payload = {
        "id": problem_id,
        "title": f"Problem {problem_id}",
        "description": "Read two integers and print their sum.",
        "input_description": "Two integers.",
        "output_description": "Their sum.",
        "samples": [{"input": "1 2", "output": "3"}],
        "constraints": "Small test data.",
        "testcases": testcases
        or [
            {"input": "1 2", "output": "3"},
            {"input": "-5 8", "output": "3"},
        ],
    }
    if time_limit is not None:
        payload["time_limit"] = time_limit
    if memory_limit is not None:
        payload["memory_limit"] = memory_limit
    if code_length_limit is not None:
        payload["code_length_limit"] = code_length_limit
    return payload


async def register_login_and_create_problem(
    client,
    app,
    *,
    username: str,
    problem_id: str,
    time_limit: float | None = None,
    memory_limit: int | None = None,
    code_length_limit: int | None = None,
    testcases: list[dict] | None = None,
) -> None:
    registered = await client.post(
        "/api/users/", json={"username": username, "password": "secret123"}
    )
    assert registered.status_code == 200
    logged_in = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert logged_in.status_code == 200
    await client.post("/api/auth/logout")
    settings = app.state.settings
    teacher_login = await client.post(
        "/api/auth/login",
        json={
            "username": settings.initial_admin_username,
            "password": settings.initial_admin_password,
        },
    )
    assert teacher_login.status_code == 200
    created = await client.post(
        "/api/problems/",
        json=problem_payload(
            problem_id,
            time_limit=time_limit,
            memory_limit=memory_limit,
            code_length_limit=code_length_limit,
            testcases=testcases,
        ),
    )
    assert created.status_code == 200
    await client.post("/api/auth/logout")
    logged_in = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert logged_in.status_code == 200


async def submit(client, problem_id: str, code: str, language: str = "python"):
    return await client.post(
        "/api/submissions/",
        json={"problem_id": problem_id, "language": language, "code": code},
    )


async def wait_for_result(app, submission_id: str, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        row = await app.state.database.fetch_one(
            "SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)
        )
        if row is not None and row["status"] != "pending":
            return row
        await asyncio.sleep(0.02)
    pytest.fail(f"submission {submission_id} did not finish within {timeout} seconds")


async def load_testcase_results(app, submission_id: str):
    return await app.state.database.fetch_all(
        """
        SELECT testcase_id, result, time_seconds, memory_mb
        FROM testcase_results
        WHERE submission_id = ?
        ORDER BY testcase_id
        """,
        (submission_id,),
    )


@pytest.mark.asyncio
async def test_submission_requires_login_before_body_validation(client):
    response = await client.post("/api/submissions/", json={})
    assert response.status_code == 401
    assert response.json()["code"] == 401


@pytest.mark.asyncio
async def test_language_registration_and_listing(client):
    assert (await client.get("/api/languages/")).status_code == 401
    invalid_unauthenticated = await client.post("/api/languages/", json={})
    assert invalid_unauthenticated.status_code == 401

    registered = await client.post(
        "/api/users/", json={"username": "language-user", "password": "secret123"}
    )
    assert registered.status_code == 200
    await client.post(
        "/api/auth/login", json={"username": "language-user", "password": "secret123"}
    )

    defaults = await client.get("/api/languages/")
    assert defaults.status_code == 200
    assert defaults.json()["data"] == {"name": ["cpp", "python"]}

    go_language = {
        "name": "Go",
        "file_ext": ".go",
        "compile_cmd": "go build -o {exe} {src}",
        "run_cmd": "{exe}",
        "time_limit": 2,
        "memory_limit": 256,
    }
    created = await client.post("/api/languages/", json=go_language)
    assert created.status_code == 200
    assert created.json() == {
        "code": 200,
        "msg": "language registered",
        "data": {"name": "go"},
    }
    assert (await client.post("/api/languages/", json=go_language)).status_code == 409

    listing = await client.get("/api/languages/")
    assert listing.json()["data"] == {"name": ["cpp", "go", "python"]}


@pytest.mark.asyncio
async def test_rejects_unsafe_or_incomplete_language_templates(client):
    await client.post("/api/users/", json={"username": "language-user", "password": "secret123"})
    await client.post(
        "/api/auth/login", json={"username": "language-user", "password": "secret123"}
    )
    invalid_languages = [
        {"name": "bad1", "file_ext": ".x", "run_cmd": "runner"},
        {"name": "bad2", "file_ext": "x", "run_cmd": "runner {src}"},
        {"name": "bad3", "file_ext": ".x", "run_cmd": "runner {unknown}"},
        {
            "name": "bad4",
            "file_ext": ".x",
            "compile_cmd": "compiler {exe}",
            "run_cmd": "{exe}",
        },
        {"name": "bad5", "file_ext": ".x", "run_cmd": "runner {src}\nwhoami"},
        {"name": "bad6", "file_ext": ".x", "run_cmd": "/bin/sh {src}"},
        {"name": "bad7", "file_ext": ".x", "run_cmd": "sh -c {src}"},
        {"name": "bad8", "file_ext": ".x", "run_cmd": "python3 -c pass {src}"},
        {"name": "bad8b", "file_ext": ".x", "run_cmd": "python3 -cpass {src}"},
        {"name": "bad9", "file_ext": ".x", "run_cmd": "python3 {src}; touch owned"},
        {"name": "bad10", "file_ext": ".x", "run_cmd": "python3 ../{src}"},
        {"name": "bad11", "file_ext": ".x", "run_cmd": "python3 {src} /etc/passwd"},
        {
            "name": "bad12",
            "file_ext": ".x",
            "compile_cmd": "g++ {src} -o {exe} -fplugin=evil",
            "run_cmd": "{exe}",
        },
        {
            "name": "bad13",
            "file_ext": ".x",
            "compile_cmd": "go build -toolexec=evil -o {exe} {src}",
            "run_cmd": "{exe}",
        },
        {
            "name": "bad14",
            "file_ext": ".x",
            "compile_cmd": "g++ {src}",
            "run_cmd": "{exe}",
        },
    ]
    for language in invalid_languages:
        response = await client.post("/api/languages/", json=language)
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_stored_language_commands_are_revalidated_before_execution(
    client, app, test_settings
):
    await register_login_and_create_problem(
        client, app, username="stored-command-user", problem_id="stored-command"
    )
    user = await app.state.database.fetch_one(
        "SELECT user_id FROM users WHERE username = 'stored-command-user'"
    )
    marker = test_settings.project_root / "unsafe-command-ran"
    await app.state.database.execute(
        """
        INSERT INTO languages (
            name, file_ext, compile_cmd, run_cmd,
            time_limit, memory_limit, created_by, created_at
        ) VALUES ('unsafe-shell', '.sh', NULL, '/bin/sh {src}', 1, 128, ?, datetime('now'))
        """,
        (user["user_id"],),
    )

    response = await submit(
        client,
        "stored-command",
        f"touch {marker}",
        language="unsafe-shell",
    )
    submission_id = response.json()["data"]["submission_id"]
    result = await wait_for_result(app, submission_id)

    assert result["status"] == "error"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_python_accepted_and_wrong_answer_results(client, app, test_settings):
    await register_login_and_create_problem(
        client, app, username="python-user", problem_id="python-verdicts"
    )

    accepted = await submit(
        client,
        "python-verdicts",
        "a, b = map(int, input().split())\nprint(a + b, '   ')",
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["status"] == "pending"
    accepted_id = accepted.json()["data"]["submission_id"]
    accepted_row = await wait_for_result(app, accepted_id)
    assert accepted_row["status"] == "success"
    assert accepted_row["score"] == 20
    assert accepted_row["counts"] == 20
    assert json.loads(accepted_row["compile_info"]) is None
    assert json.loads(accepted_row["run_info"])["result"] == "finished"
    assert [row["result"] for row in await load_testcase_results(app, accepted_id)] == [
        "AC",
        "AC",
    ]

    wrong = await submit(client, "python-verdicts", "print(0)")
    wrong_id = wrong.json()["data"]["submission_id"]
    wrong_row = await wait_for_result(app, wrong_id)
    assert wrong_row["status"] == "success"
    assert wrong_row["score"] == 0
    assert [row["result"] for row in await load_testcase_results(app, wrong_id)] == [
        "WA",
        "WA",
    ]
    assert list(test_settings.runtime_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_python_runtime_error_and_timeout(client, app):
    await register_login_and_create_problem(
        client,
        app,
        username="runtime-user",
        problem_id="runtime-limits",
        time_limit=0.1,
        testcases=[{"input": "", "output": "done"}],
    )

    runtime_error = await submit(client, "runtime-limits", "raise RuntimeError('boom')")
    runtime_id = runtime_error.json()["data"]["submission_id"]
    runtime_row = await wait_for_result(app, runtime_id)
    assert (await load_testcase_results(app, runtime_id))[0]["result"] == "RE"
    assert "RuntimeError" in json.loads(runtime_row["run_info"])["message"]

    timed_out = await submit(client, "runtime-limits", "import time\ntime.sleep(2)\nprint('done')")
    timeout_id = timed_out.json()["data"]["submission_id"]
    await wait_for_result(app, timeout_id)
    assert (await load_testcase_results(app, timeout_id))[0]["result"] == "TLE"


@pytest.mark.asyncio
async def test_python_memory_limit(client, app):
    await register_login_and_create_problem(
        client,
        app,
        username="memory-user",
        problem_id="memory-limit",
        time_limit=2,
        memory_limit=32,
        testcases=[{"input": "", "output": "done"}],
    )
    code = "import time\nx = bytearray(200 * 1024 * 1024)\ntime.sleep(0.3)\nprint('done')"
    response = await submit(client, "memory-limit", code)
    submission_id = response.json()["data"]["submission_id"]
    await wait_for_result(app, submission_id)
    assert (await load_testcase_results(app, submission_id))[0]["result"] == "MLE"


@pytest.mark.asyncio
async def test_program_output_limit(client, app):
    await register_login_and_create_problem(
        client,
        app,
        username="output-user",
        problem_id="output-limit",
        time_limit=2,
        testcases=[{"input": "", "output": "done"}],
    )
    response = await submit(client, "output-limit", "print('x' * 1100000)")
    submission_id = response.json()["data"]["submission_id"]
    result = await wait_for_result(app, submission_id)
    assert (await load_testcase_results(app, submission_id))[0]["result"] == "RE"
    assert "output limit exceeded" in json.loads(result["run_info"])["message"]


@pytest.mark.asyncio
async def test_problem_default_limit_overrides_language_limit(client, app):
    await register_login_and_create_problem(
        client,
        app,
        username="fallback-user",
        problem_id="language-fallback",
        testcases=[{"input": "", "output": "done"}],
    )
    language = {
        "name": "python-fast",
        "file_ext": ".py",
        "run_cmd": "python3 {src}",
        "time_limit": 0.05,
        "memory_limit": 128,
    }
    assert (await client.post("/api/languages/", json=language)).status_code == 200
    response = await submit(
        client,
        "language-fallback",
        "import time\ntime.sleep(0.2)\nprint('done')",
        language="python-fast",
    )
    submission_id = response.json()["data"]["submission_id"]
    await wait_for_result(app, submission_id)
    assert (await load_testcase_results(app, submission_id))[0]["result"] == "AC"


@pytest.mark.asyncio
async def test_problem_code_length_limit_is_enforced_before_submission(client, app):
    await register_login_and_create_problem(
        client,
        app,
        username="code-length-user",
        problem_id="code-length-limit",
        code_length_limit=13,
        testcases=[{"input": "", "output": "done"}],
    )

    accepted = await submit(client, "code-length-limit", "print('done')")
    assert accepted.status_code == 200
    await wait_for_result(app, accepted.json()["data"]["submission_id"])

    rejected = await submit(client, "code-length-limit", "print('too long')")
    assert rejected.status_code == 400
    assert rejected.json() == {
        "code": 400,
        "msg": "code length exceeds problem limit of 13 characters",
        "data": None,
    }


@pytest.mark.asyncio
async def test_judge_rechecks_code_length_for_stored_submissions(client, app):
    await register_login_and_create_problem(
        client,
        app,
        username="stored-length-user",
        problem_id="stored-length-limit",
        code_length_limit=5,
    )
    user = await app.state.database.fetch_one(
        "SELECT user_id FROM users WHERE username = 'stored-length-user'"
    )
    await app.state.database.execute(
        """
        INSERT INTO submissions (
            submission_id, user_id, problem_id, language, code, status,
            created_at, updated_at
        ) VALUES (
            'stored-too-long', ?, 'stored-length-limit', 'python',
            'print(3)', 'pending', datetime('now'), datetime('now')
        )
        """,
        (user["user_id"],),
    )

    from app.repositories.problems import ProblemRepository
    from app.services.judge import JudgeService

    judge = JudgeService(
        app.state.database,
        ProblemRepository(app.state.settings.problems_dir),
        app.state.settings,
    )
    await judge.judge_submission("stored-too-long")
    result = await app.state.database.fetch_one(
        "SELECT status, error_info FROM submissions WHERE submission_id = 'stored-too-long'"
    )
    assert result["status"] == "error"
    assert result["error_info"] == "code length exceeds problem limit of 5 characters"


@pytest.mark.asyncio
async def test_submission_errors_and_rate_limit(client, app):
    await register_login_and_create_problem(
        client, app, username="rate-user", problem_id="rate-limit"
    )
    invalid = await client.post("/api/submissions/", json={})
    assert invalid.status_code == 400
    assert (await submit(client, "missing-problem", "print(1)")).status_code == 404
    assert (
        await submit(client, "rate-limit", "print(1)", language="missing-language")
    ).status_code == 404

    accepted_ids = []
    for _index in range(3):
        response = await submit(client, "rate-limit", "print(3)")
        assert response.status_code == 200
        accepted_ids.append(response.json()["data"]["submission_id"])
    # 429 has higher priority than the missing-problem 404 in the API contract.
    limited = await submit(client, "missing-after-limit", "print(3)")
    assert limited.status_code == 429
    assert limited.json()["code"] == 429
    for submission_id in accepted_ids:
        await wait_for_result(app, submission_id)


@pytest.mark.asyncio
async def test_submission_rate_limit_is_atomic_for_concurrent_requests(client, app):
    await register_login_and_create_problem(
        client, app, username="concurrent-rate-user", problem_id="concurrent-rate-limit"
    )

    responses = await asyncio.gather(
        *(
            submit(
                client,
                "concurrent-rate-limit",
                "a, b = map(int, input().split())\nprint(a + b)",
            )
            for _index in range(8)
        )
    )

    assert sum(response.status_code == 200 for response in responses) == 3
    assert sum(response.status_code == 429 for response in responses) == 5
    accepted_ids = [
        response.json()["data"]["submission_id"]
        for response in responses
        if response.status_code == 200
    ]
    for submission_id in accepted_ids:
        await wait_for_result(app, submission_id)


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is required for C++ judging")
async def test_cpp_compilation_success_and_error(client, app, test_settings):
    await register_login_and_create_problem(
        client, app, username="cpp-user", problem_id="cpp-judge"
    )
    accepted_code = """
#include <iostream>
int main() {
    long long a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""
    accepted = await submit(client, "cpp-judge", accepted_code, language="cpp")
    accepted_id = accepted.json()["data"]["submission_id"]
    accepted_row = await wait_for_result(app, accepted_id, timeout=20)
    assert accepted_row["status"] == "success"
    assert accepted_row["score"] == 20
    assert json.loads(accepted_row["compile_info"])["result"] == "success"
    assert [row["result"] for row in await load_testcase_results(app, accepted_id)] == [
        "AC",
        "AC",
    ]

    compilation_error = await submit(client, "cpp-judge", "int main( { return 0; }", language="cpp")
    error_id = compilation_error.json()["data"]["submission_id"]
    error_row = await wait_for_result(app, error_id, timeout=20)
    compile_info = json.loads(error_row["compile_info"])
    assert compile_info["result"] == "CE"
    assert str(test_settings.runtime_dir) not in compile_info["message"]
    assert [row["result"] for row in await load_testcase_results(app, error_id)] == [
        "CE",
        "CE",
    ]
