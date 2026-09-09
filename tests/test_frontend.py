from types import SimpleNamespace

import httpx
import pytest

from frontend import forms, ui
from frontend.api_client import APIClientError, OJAPIClient, normalize_base_url
from frontend.forms import (
    build_problem_payload,
    generate_unique_problem_id,
    parse_io_pairs,
    parse_tags,
    validate_io_pairs,
    validate_new_problem_id,
)


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


def test_authenticated_401_resets_local_session_and_reruns(monkeypatch):
    class RerunRequested(RuntimeError):
        pass

    class SessionState(dict):
        def __getattr__(self, name):
            return self[name]

    state = SessionState(
        current_user={"user_id": "u1", "username": "alice", "role": "user"},
        api_base_url="http://backend.test",
    )
    reset_urls: list[str] = []
    flash_messages: list[str] = []
    fake_streamlit = SimpleNamespace(
        session_state=state,
        error=lambda _message: pytest.fail("expired sessions should rerun before rendering"),
        rerun=lambda: (_ for _ in ()).throw(RerunRequested()),
    )
    monkeypatch.setattr(ui, "st", fake_streamlit)
    monkeypatch.setattr(ui, "_replace_client", reset_urls.append)
    monkeypatch.setattr(ui, "_set_flash", flash_messages.append)

    with pytest.raises(RerunRequested):
        ui._show_error(APIClientError("not logged in", status_code=401))

    assert reset_urls == ["http://backend.test"]
    assert flash_messages == ["登录已失效，请重新登录"]


def test_login_form_401_remains_a_visible_credentials_error(monkeypatch):
    messages: list[str] = []
    fake_streamlit = SimpleNamespace(
        session_state={"current_user": None},
        error=messages.append,
        rerun=lambda: pytest.fail("a failed login must not trigger a rerun"),
    )
    monkeypatch.setattr(ui, "st", fake_streamlit)

    ui._show_error(APIClientError("invalid username or password", status_code=401))

    assert messages == ["请先登录：invalid username or password"]


def test_successful_problem_mutation_schedules_fresh_list_and_reruns(monkeypatch):
    class RerunRequested(RuntimeError):
        pass

    class SessionState(dict):
        def __setattr__(self, name, value):
            self[name] = value

    state = SessionState()
    flash_messages: list[str] = []
    fake_streamlit = SimpleNamespace(
        session_state=state,
        rerun=lambda: (_ for _ in ()).throw(RerunRequested()),
    )
    monkeypatch.setattr(ui, "st", fake_streamlit)
    monkeypatch.setattr(ui, "_set_flash", flash_messages.append)

    with pytest.raises(RerunRequested):
        ui._refresh_after_mutation("saved", show_problem_list=True)

    assert state["pending_problem_operation"] == "题目列表"
    assert flash_messages == ["saved"]


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
        "samples": [{"input": "1 2", "output": "3"}],
        "testcases": [
            {"input": "2 3", "output": "5"},
            {"input": "-1 4\n", "output": "3\n"},
        ],
        "hint": "hint",
        "source": "course",
        "tags": " basic, math, ,",
        "code_length_limit": 4096,
        "time_limit": 1.5,
        "memory_limit": 128,
        "author": " teacher ",
        "difficulty": " easy ",
    }
    payload = build_problem_payload(values)

    assert payload["title"] == "A+B"
    assert payload["tags"] == ["basic", "math"]
    assert payload["samples"] == [{"input": "1 2", "output": "3"}]
    assert payload["testcases"] == [
        {"input": "2 3", "output": "5"},
        {"input": "-1 4\n", "output": "3\n"},
    ]
    assert payload["code_length_limit"] == 4096
    assert payload["time_limit"] == 1.5
    assert payload["memory_limit"] == 128
    assert payload["author"] == "teacher"
    assert payload["difficulty"] == "easy"


def test_student_problem_payload_omits_teacher_only_resource_limits():
    values = {
        "id": "P1002",
        "title": "Student draft",
        "description": "description",
        "input_description": "input",
        "output_description": "output",
        "constraints": "small",
        "samples": [{"input": "", "output": ""}],
        "testcases": [{"input": "", "output": ""}],
    }

    payload = build_problem_payload(values)

    assert "code_length_limit" not in payload
    assert "time_limit" not in payload
    assert "memory_limit" not in payload


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


@pytest.mark.parametrize(
    "value",
    [
        [],
        [{"input": "1"}],
        [{"input": 1, "output": "1"}],
        [{"input": "1", "output": "1", "extra": True}],
    ],
)
def test_structured_io_pairs_reject_invalid_shapes(value):
    with pytest.raises(ValueError):
        validate_io_pairs(value, "测试点")


def test_tags_ignore_empty_items():
    assert parse_tags("one, two, ,three") == ["one", "two", "three"]


def test_random_problem_id_skips_existing_candidates(monkeypatch):
    candidates = iter(["A1B2C3D4", "11223344"])
    monkeypatch.setattr(forms.secrets, "token_hex", lambda _length: next(candidates))

    generated = generate_unique_problem_id({"PA1B2C3D4", "P1001"})

    assert generated == "P11223344"


def test_new_problem_id_duplicate_is_rejected():
    with pytest.raises(ValueError, match="P1001 已存在"):
        validate_new_problem_id(" P1001 ", {"P1001", "P1002"})


def test_frontend_does_not_import_backend_storage_or_services():
    source_files = [
        "streamlit_app.py",
        "frontend/api_client.py",
        "frontend/forms.py",
        "frontend/ui.py",
        "frontend/ai_page.py",
    ]
    forbidden = ("app.db", "app.repositories", "app.services")
    for path in source_files:
        source = open(path, encoding="utf-8").read()
        assert all(module not in source for module in forbidden)
