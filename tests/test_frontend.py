import json

import httpx
import pytest

from frontend.api_client import APIClientError, OJAPIClient, normalize_base_url
from frontend.forms import build_problem_payload, parse_io_pairs, parse_tags


def envelope(code: int, msg: str = "success", data=None) -> httpx.Response:
    return httpx.Response(code, json={"code": code, "msg": msg, "data": data})


def test_api_client_preserves_session_cookie_and_decodes_envelope():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                headers={"set-cookie": "oj_session=session-token; Path=/; HttpOnly"},
                json={
                    "code": 200,
                    "msg": "login success",
                    "data": {"user_id": "u1", "username": "alice", "role": "user"},
                },
            )
        assert request.headers["cookie"] == "oj_session=session-token"
        return envelope(200, data=[{"id": "P1", "title": "A+B"}])

    client = OJAPIClient("http://backend.test/", transport=httpx.MockTransport(handler))
    logged_in = client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret123"}
    )
    problems = client.get("/api/problems/")

    assert logged_in.data["username"] == "alice"
    assert problems.data == [{"id": "P1", "title": "A+B"}]
    assert len(requests) == 2
    client.close()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (envelope(403, "permission denied"), "permission denied"),
        (httpx.Response(200, text="not json"), "无法解析"),
        (httpx.Response(200, json={"code": 200, "msg": "ok"}), "缺少"),
        (
            httpx.Response(201, json={"code": 200, "msg": "created", "data": None}),
            "不一致",
        ),
    ],
)
def test_api_client_reports_backend_and_protocol_errors(response, message):
    client = OJAPIClient(
        "http://backend.test",
        transport=httpx.MockTransport(lambda _request: response),
    )
    with pytest.raises(APIClientError, match=message):
        client.get("/api/test")
    client.close()


def test_api_client_reports_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = OJAPIClient("http://backend.test", transport=httpx.MockTransport(handler))
    with pytest.raises(APIClientError, match="无法连接后端") as captured:
        client.get("/api/health")
    assert captured.value.status_code is None
    client.close()


@pytest.mark.parametrize("value", ["localhost:8000", "ftp://example.com", "http://u:p@host"])
def test_base_url_validation(value):
    with pytest.raises(ValueError):
        normalize_base_url(value)


def test_problem_form_parsers_build_complete_payload():
    values = {
        "id": "P1001",
        "title": " A+B ",
        "description": "description",
        "input_description": "input",
        "output_description": "output",
        "constraints": "small",
        "samples": json.dumps([{"input": "1 2", "output": "3"}]),
        "testcases": json.dumps([{"input": "2 3", "output": "5"}]),
        "hint": "hint",
        "source": "course",
        "tags": " basic, math, ,",
        "time_limit": 1.5,
        "memory_limit": 128,
        "author": " teacher ",
        "difficulty": " easy ",
    }
    payload = build_problem_payload(values)

    assert payload["title"] == "A+B"
    assert payload["tags"] == ["basic", "math"]
    assert payload["samples"] == [{"input": "1 2", "output": "3"}]
    assert payload["testcases"] == [{"input": "2 3", "output": "5"}]
    assert payload["time_limit"] == 1.5
    assert payload["memory_limit"] == 128
    assert payload["author"] == "teacher"
    assert payload["difficulty"] == "easy"


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        "[]",
        '[{"input": "1"}]',
        '[{"input": 1, "output": "1"}]',
        '[{"input": "1", "output": "1", "extra": true}]',
    ],
)
def test_io_pair_parser_rejects_invalid_json_shapes(value):
    with pytest.raises(ValueError):
        parse_io_pairs(value, "测试点")


def test_tags_ignore_empty_items():
    assert parse_tags("one, two, ,three") == ["one", "two", "three"]


def test_frontend_does_not_import_backend_storage_or_services():
    source_files = [
        "streamlit_app.py",
        "frontend/api_client.py",
        "frontend/forms.py",
        "frontend/ui.py",
    ]
    forbidden = ("app.db", "app.repositories", "app.services")
    for path in source_files:
        source = open(path, encoding="utf-8").read()
        assert all(module not in source for module in forbidden)
