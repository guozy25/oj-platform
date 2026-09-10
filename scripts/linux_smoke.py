from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _require(response: httpx.Response, expected_status: int = 200) -> dict:
    if response.status_code != expected_status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text}"
        )
    payload = response.json()
    if payload.get("code") != expected_status:
        raise AssertionError(f"response envelope has the wrong code: {payload}")
    return payload


def _problem_payload() -> dict:
    return {
        "id": "linux-smoke",
        "title": "Linux smoke test",
        "description": "Read two integers and print their sum.",
        "input_description": "Two integers.",
        "output_description": "Their sum.",
        "samples": [{"input": "1 2", "output": "3"}],
        "constraints": "-100 <= a, b <= 100",
        "testcases": [
            {"input": "1 2", "output": "3"},
            {"input": "-5 8", "output": "3"},
        ],
        "code_length_limit": 10_000,
        "time_limit": 2,
        "memory_limit": 128,
    }


def _wait_for_submission(client: httpx.Client, submission_id: str) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        payload = _require(client.get(f"/api/submissions/{submission_id}"))["data"]
        if payload["status"] != "pending":
            return payload
        time.sleep(0.1)
    raise AssertionError(f"submission did not finish: {submission_id}")


def _submit_and_expect_ac(client: httpx.Client, language: str, code: str) -> None:
    created = _require(
        client.post(
            "/api/submissions/",
            json={"problem_id": "linux-smoke", "language": language, "code": code},
        )
    )["data"]
    result = _wait_for_submission(client, created["submission_id"])
    if result["status"] != "success" or result["score"] != result["counts"]:
        raise AssertionError(f"{language} judge smoke test failed: {result}")


def _exercise_live_server(base_url: str, temporary_path: Path) -> None:
    with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
        _require(client.get("/api/health"))
        user = _require(
            client.post(
                "/api/users/",
                json={"username": "linux-smoke-user", "password": "secret123"},
            )
        )["data"]
        _require(
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admintestpassword"},
            )
        )
        _require(client.post("/api/problems/", json=_problem_payload()))
        _require(client.post("/api/auth/logout"))
        _require(
            client.post(
                "/api/auth/login",
                json={"username": "linux-smoke-user", "password": "secret123"},
            )
        )

        _submit_and_expect_ac(
            client,
            "python",
            "a, b = map(int, input().split())\nprint(a + b)\n",
        )
        _submit_and_expect_ac(
            client,
            "cpp",
            "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b;}\n",
        )
        marker = temporary_path / "sandbox-escaped.txt"
        secret = temporary_path / "host-secret.txt"
        secret.write_text("must-not-leak", encoding="utf-8")
        port = int(base_url.rsplit(":", 1)[1])
        sandbox_probe = "\n".join(
            [
                "import os, socket",
                "safe = True",
                f"secret = {str(secret)!r}",
                f"marker = {str(marker)!r}",
                "try:",
                "    open(secret).read()",
                "    safe = False",
                "except OSError:",
                "    pass",
                "try:",
                "    open(marker, 'w').write('escaped')",
                "    safe = False",
                "except OSError:",
                "    pass",
                "try:",
                f"    socket.create_connection(('127.0.0.1', {port}), timeout=0.2)",
                "    safe = False",
                "except OSError:",
                "    pass",
                "if 'OJ_SANDBOX_SMOKE_SECRET' in os.environ:",
                "    safe = False",
                "print(3 if safe else 0)",
            ]
        )
        _submit_and_expect_ac(client, "python", sandbox_probe)
        if marker.exists():
            raise AssertionError("sandboxed submission wrote outside its workspace")

        _require(client.post("/api/auth/logout"))
        _require(
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admintestpassword"},
            )
        )
        _require(client.post("/api/reset/"))
        _require(client.get(f"/api/users/{user['user_id']}"), expected_status=401)
        _require(
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admintestpassword"},
            )
        )


def main() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="oj-linux-smoke-") as temporary_dir:
        temporary_path = Path(temporary_dir)
        environment = os.environ.copy()
        environment.update(
            {
                "OJ_DATABASE_PATH": str(temporary_path / "data" / "oj.db"),
                "OJ_PROBLEMS_DIR": str(temporary_path / "problems"),
                "OJ_RUNTIME_DIR": str(temporary_path / "runtime"),
                "OJ_SANDBOX_SMOKE_SECRET": "must-not-leak",
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=project_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        failure: BaseException | None = None
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(timeout=1, trust_env=False) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Uvicorn exited during startup")
                    try:
                        response = probe.get(f"{base_url}/api/health")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise TimeoutError("Uvicorn did not become ready")

            _exercise_live_server(base_url, temporary_path)
        except BaseException as exc:
            failure = exc
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        output = process.stdout.read() if process.stdout is not None else ""
        if failure is not None:
            raise RuntimeError(f"Linux smoke test failed. Uvicorn output:\n{output}") from failure

    print("Live-server Python/C++ judge and reset smoke test passed.")


if __name__ == "__main__":
    main()
