import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.ai_provider import ProviderResult
from tests.test_users import login, register


def model_config(api_key: str = "super-secret-key") -> dict:
    return {
        "provider_url": "https://models.example/v1",
        "model": "course-model",
        "api_key": api_key,
        "input_price": 2.0,
        "output_price": 4.0,
        "price_unit": 1_000,
    }


def generated_draft() -> dict:
    inputs = ["1 2", "0 0", "-5 2", "5 -2", "-5 -2", "10 20", "999 1", "-1000 1000"]
    return {
        "problem": {
            "id": "AI_SUM",
            "title": "可靠的两数之和",
            "description": "输入两个整数，输出它们的和。",
            "input_description": "一行两个整数 a 和 b。",
            "output_description": "输出 a+b。",
            "samples": [{"input": "1 2", "output": "故意错误的模型输出"}],
            "constraints": "-1000 <= a,b <= 1000",
            "testcases": [{"input": value, "output": "wrong"} for value in inputs],
            "hint": "注意负数。",
            "source": "AI generated",
            "tags": ["基础", "数学"],
            "time_limit": 1.0,
            "memory_limit": 128,
            "author": "AI",
            "difficulty": "入门",
        },
        "reference_solution": "a, b = map(int, input().split())\nprint(a + b)",
        "incorrect_solutions": [
            "a, b = map(int, input().split())\nprint(a - b)",
            "a, b = map(int, input().split())\nprint(abs(a) + abs(b))",
        ],
        "testcase_purposes": [
            "普通正数",
            "最小零值边界",
            "负数与正数",
            "正数与负数",
            "两个负数",
            "普通较大值",
            "上界附近",
            "相消的边界值",
        ],
    }


class StaticProvider:
    def __init__(self, _config, responses: list[str], started: asyncio.Event | None = None):
        self.responses = responses
        self.started = started
        self.calls = 0

    async def complete(self, _system_prompt: str, _user_prompt: str) -> ProviderResult:
        if self.started is not None:
            self.started.set()
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ProviderResult(content=content, input_tokens=100, output_tokens=50, estimated=False)


class BlockingProvider:
    def __init__(self, _config, started: asyncio.Event):
        self.started = started

    async def complete(self, _system_prompt: str, _user_prompt: str) -> ProviderResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("a cancelled provider call must not continue")


async def wait_for_ai_task(app, task_id: str, terminal: set[str] | None = None) -> dict:
    terminal = terminal or {"completed", "failed", "cancelled"}
    for _ in range(300):
        row = await app.state.database.fetch_one(
            "SELECT status, result, usage, error_info FROM ai_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert row is not None
        if row["status"] in terminal:
            return dict(row)
        await asyncio.sleep(0.02)
    raise AssertionError("AI task did not finish")


@pytest.mark.asyncio
async def test_ai_endpoints_require_authentication_before_body_validation(client):
    assert (await client.get("/api/ai/model-config")).status_code == 401
    assert (await client.put("/api/ai/model-config", json={})).status_code == 401
    assert (await client.post("/api/ai/problem-tasks/", json={})).status_code == 401
    assert (await client.get("/api/ai/problem-tasks/missing")).status_code == 401
    assert (await client.put("/api/ai/problem-tasks/missing/cancel")).status_code == 401


@pytest.mark.asyncio
async def test_model_config_is_validated_and_never_returns_api_key(client):
    await register(client, "ai-config-user")
    await login(client, "ai-config-user", "secret123")

    missing = await client.get("/api/ai/model-config")
    assert missing.json()["data"] == {"api_key_configured": False}

    invalid = model_config()
    invalid["provider_url"] = "file:///etc/passwd"
    assert (await client.put("/api/ai/model-config", json=invalid)).status_code == 400

    configured = await client.put("/api/ai/model-config", json=model_config())
    assert configured.status_code == 200
    assert configured.json()["data"] == {
        "provider_url": "https://models.example/v1",
        "model": "course-model",
        "api_key_configured": True,
        "input_price": 2.0,
        "output_price": 4.0,
        "price_unit": 1_000,
    }
    assert "super-secret-key" not in configured.text
    assert "super-secret-key" not in (await client.get("/api/ai/model-config")).text


@pytest.mark.asyncio
async def test_generated_problem_is_verified_costed_and_ready_for_import(client, app):
    await register(client, "ai-author")
    await login(client, "ai-author", "secret123")
    await client.put("/api/ai/model-config", json=model_config())
    provider = StaticProvider(None, [json.dumps(generated_draft(), ensure_ascii=False)])
    app.state.ai_provider_factory = lambda _config: provider

    created = await client.post(
        "/api/ai/problem-tasks/",
        json={"requirement": "生成一道覆盖正负数边界的两数之和题目"},
    )
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "pending"
    task_id = created.json()["data"]["task_id"]
    await wait_for_ai_task(app, task_id)

    detail = await client.get(f"/api/ai/problem-tasks/{task_id}")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["status"] == "completed"
    assert data["result"]["ready_for_import"] is True
    assert data["result"]["problem"]["samples"][0]["output"] == "3"
    assert data["result"]["problem"]["testcases"][2]["output"] == "-3"
    validation = data["result"]["validation"]
    assert validation["reference_outputs_verified"] is True
    assert validation["source_safety_checked"] is True
    assert validation["mutants_killed"] == validation["mutants_total"] == 2
    assert validation["warnings"] == []
    assert data["usage"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost": 0.4,
        "currency": "USD",
        "estimated": False,
        "price_unit": 1_000,
    }
    assert provider.calls == 1

    history = (await client.get("/api/ai/problem-tasks/")).json()["data"]
    assert history[0]["task_id"] == task_id
    assert history[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_invalid_first_draft_is_repaired_and_usage_is_accumulated(client, app):
    await register(client, "ai-repair-user")
    await login(client, "ai-repair-user", "secret123")
    await client.put("/api/ai/model-config", json=model_config())
    provider = StaticProvider(
        None,
        ["not valid JSON", json.dumps(generated_draft(), ensure_ascii=False)],
    )
    app.state.ai_provider_factory = lambda _config: provider

    created = await client.post(
        "/api/ai/problem-tasks/", json={"requirement": "生成并自动修复一道基础题"}
    )
    task_id = created.json()["data"]["task_id"]
    await wait_for_ai_task(app, task_id)
    detail = (await client.get(f"/api/ai/problem-tasks/{task_id}")).json()["data"]

    assert detail["status"] == "completed"
    assert detail["usage"]["input_tokens"] == 200
    assert detail["usage"]["output_tokens"] == 100
    assert detail["usage"]["cost"] == 0.8
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_generated_code_with_side_effects_is_rejected_without_leaking_key(client, app):
    await register(client, "ai-safety-user")
    await login(client, "ai-safety-user", "secret123")
    await client.put("/api/ai/model-config", json=model_config())
    unsafe = generated_draft()
    unsafe["reference_solution"] = "import os\nos.remove('important-file')"
    provider = StaticProvider(None, [json.dumps(unsafe)])
    app.state.ai_provider_factory = lambda _config: provider

    created = await client.post(
        "/api/ai/problem-tasks/", json={"requirement": "安全检查测试题目"}
    )
    task_id = created.json()["data"]["task_id"]
    await wait_for_ai_task(app, task_id)
    response = await client.get(f"/api/ai/problem-tasks/{task_id}")

    assert response.json()["data"]["status"] == "failed"
    assert "blocked" in response.json()["data"]["error_info"]
    assert "super-secret-key" not in response.text
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_task_owner_admin_permissions_and_missing_config(client, app, test_settings):
    owner = (await register(client, "ai-owner")).json()["data"]
    await login(client, "ai-owner", "secret123")
    without_config = await client.post(
        "/api/ai/problem-tasks/", json={"requirement": "尚未配置模型"}
    )
    assert without_config.status_code == 400

    await client.put("/api/ai/model-config", json=model_config())
    app.state.ai_provider_factory = lambda _config: StaticProvider(
        None, [json.dumps(generated_draft())]
    )
    created = await client.post(
        "/api/ai/problem-tasks/", json={"requirement": "权限测试题目"}
    )
    task_id = created.json()["data"]["task_id"]
    await wait_for_ai_task(app, task_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as other_client:
        await register(other_client, "ai-other")
        await login(other_client, "ai-other", "secret123")
        assert (await other_client.get(f"/api/ai/problem-tasks/{task_id}")).status_code == 403
        await other_client.post("/api/auth/logout")
        await login(
            other_client,
            test_settings.initial_admin_username,
            test_settings.initial_admin_password,
        )
        visible = await other_client.get(f"/api/ai/problem-tasks/{task_id}")
        assert visible.status_code == 200
        assert visible.json()["data"]["task_id"] == task_id
    assert owner["user_id"]


@pytest.mark.asyncio
async def test_cancel_actually_stops_running_provider(client, app):
    await register(client, "ai-cancel-user")
    await login(client, "ai-cancel-user", "secret123")
    await client.put("/api/ai/model-config", json=model_config())
    started = asyncio.Event()
    app.state.ai_provider_factory = lambda config: BlockingProvider(config, started)

    created = await client.post(
        "/api/ai/problem-tasks/", json={"requirement": "创建一个随后中断的任务"}
    )
    task_id = created.json()["data"]["task_id"]
    await asyncio.wait_for(started.wait(), timeout=1)
    duplicate = await client.post(
        "/api/ai/problem-tasks/", json={"requirement": "不能并发创建第二个任务"}
    )
    assert duplicate.status_code == 409
    cancelled = await client.put(f"/api/ai/problem-tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"] == {"task_id": task_id, "status": "cancelled"}

    row = await wait_for_ai_task(app, task_id)
    assert row["status"] == "cancelled"
    assert task_id not in app.state.ai_task_handles
    assert (await client.put(f"/api/ai/problem-tasks/{task_id}/cancel")).status_code == 409
