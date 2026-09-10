from types import SimpleNamespace

import httpx
import pytest

from frontend import ai_page, forms, ui
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


def test_registration_duplicate_username_shows_friendly_message(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(ui, "st", SimpleNamespace(error=messages.append))

    ui._show_registration_error(APIClientError("username already exists", status_code=400))

    assert messages == ["用户名已存在"]


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


def test_student_navigation_embeds_submission_features_under_problems():
    student_pages = ui._navigation_pages({"role": "user"})
    teacher_pages = ui._navigation_pages({"role": "admin"})

    assert "题目" in student_pages
    assert "题目" in teacher_pages
    assert "提交代码" not in student_pages
    assert "提交代码" not in teacher_pages
    assert "提交记录" not in student_pages
    assert "提交记录" in teacher_pages
    assert "AI 智能命题" not in student_pages
    assert "AI 智能命题" in teacher_pages


def test_problem_operations_show_management_actions_only_to_admins():
    assert ui._problem_operations({"role": "user"}) == ["题目列表"]
    assert ui._problem_operations({"role": "admin"}) == [
        "题目列表",
        "新建题目",
        "编辑题目",
        "删除题目",
    ]


def test_testcase_result_rows_use_human_readable_verdicts_and_resource_units():
    assert ui._testcase_result_rows(
        [
            {"id": 1, "result": "AC", "time": 0.01234, "memory": 8.5},
            {"id": 2, "result": "TLE", "time": 3, "memory": 12},
            {"id": 3, "result": "MLE", "time": 0.2, "memory": 128},
            {"id": 4, "result": "CE", "time": 0, "memory": 0},
            {"id": 5, "result": "RE", "time": 0.1, "memory": 6},
        ]
    ) == [
        {"测试点": "#1", "状态": "通过", "运行时间": "0.012 s", "内存": "8.500 MB"},
        {"测试点": "#2", "状态": "时间超限", "运行时间": "3.000 s", "内存": "12.000 MB"},
        {"测试点": "#3", "状态": "内存超限", "运行时间": "0.200 s", "内存": "128.000 MB"},
        {"测试点": "#4", "状态": "编译失败", "运行时间": "0.000 s", "内存": "0.000 MB"},
        {"测试点": "#5", "状态": "运行错误", "运行时间": "0.100 s", "内存": "6.000 MB"},
    ]


def test_submission_detail_loads_testcases_from_step5_log_endpoint(monkeypatch):
    requests: list[str] = []
    rendered_tables: list[list[dict[str, str]]] = []

    class FakeColumn:
        def metric(self, _label, _value):
            return None

        def button(self, label, **_kwargs):
            return label == "查看评测日志"

    class FakeClient:
        def get(self, path):
            requests.append(path)
            if path.endswith("/log"):
                return SimpleNamespace(
                    data={
                        "score": 10,
                        "counts": 20,
                        "details": [
                            {"id": 1, "result": "AC", "time": 0.01, "memory": 4.0}
                        ],
                    }
                )
            return SimpleNamespace(
                data={
                    "submission_id": "submission-1",
                    "status": "success",
                    "score": 10,
                    "counts": 20,
                    "compile_info": None,
                    "run_info": None,
                    "error_info": "",
                }
            )

    fake_streamlit = SimpleNamespace(
        subheader=lambda _text: None,
        columns=lambda count: [FakeColumn() for _ in range(count)],
        info=lambda _text: None,
        error=lambda _text: None,
        caption=lambda _text: None,
        markdown=lambda _text: None,
        dataframe=lambda rows, **_kwargs: rendered_tables.append(rows),
    )
    monkeypatch.setattr(ui, "st", fake_streamlit)
    monkeypatch.setattr(ui, "_client", lambda: FakeClient())

    ui._render_submission_detail("submission-1", is_admin=False)

    assert requests == [
        "/api/submissions/submission-1",
        "/api/submissions/submission-1/log",
    ]
    assert rendered_tables == [
        [
            {
                "测试点": "#1",
                "状态": "通过",
                "运行时间": "0.010 s",
                "内存": "4.000 MB",
            }
        ]
    ]


def test_audit_rows_show_username_and_explain_the_audited_action():
    assert ui._audit_display_rows(
        [
            {
                "user_id": "user-1",
                "problem_id": "P1001",
                "action": "view_logs",
                "time": "2026-09-10T01:02:03+00:00",
                "status": "403",
            }
        ],
        {"user-1": "alice"},
    ) == [
        {
            "用户": "alice",
            "用户 ID": "user-1",
            "题目 ID": "P1001",
            "操作": "查看评测日志",
            "访问时间": "2026-09-10T01:02:03+00:00",
            "HTTP 状态": "403",
        }
    ]


def test_problem_deletion_displays_the_selected_problem_before_confirmation(monkeypatch):
    events: list[str] = []

    fake_streamlit = SimpleNamespace(
        info=lambda message: events.append(f"info:{message}"),
        selectbox=lambda _label, options, **_kwargs: options[1],
        divider=lambda: events.append("divider"),
        warning=lambda message: events.append(f"warning:{message}"),
        checkbox=lambda _label, **_kwargs: False,
        button=lambda _label, **_kwargs: False,
    )
    monkeypatch.setattr(ui, "st", fake_streamlit)
    monkeypatch.setattr(
        ui,
        "_render_problem_detail",
        lambda problem_id: events.append(f"detail:{problem_id}") or True,
    )

    ui._render_problem_deletion(
        [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second"}]
    )

    assert events == [
        "detail:P2",
        "divider",
        "warning:删除题目后无法恢复，请谨慎操作。",
    ]


def test_pending_navigation_is_applied_before_sidebar_widget(monkeypatch):
    class SessionState(dict):
        def __setattr__(self, name, value):
            self[name] = value

    state = SessionState(pending_navigation="题目", navigation="AI 智能命题")
    monkeypatch.setattr(ui, "st", SimpleNamespace(session_state=state))

    ui._apply_pending_navigation(["题目", "AI 智能命题"])

    assert state["navigation"] == "题目"
    assert "pending_navigation" not in state


def test_problem_submission_query_is_scoped_to_current_user_and_problem():
    params = ui._problem_submission_query_params(
        "P1001", {"user_id": "student-1", "role": "user"}, 2
    )

    assert params == {
        "user_id": "student-1",
        "problem_id": "P1001",
        "page": 2,
        "page_size": 20,
    }


def test_ai_task_conflict_recovers_the_running_task_id():
    error = APIClientError(
        "an AI task is already running for this user",
        status_code=409,
        data={"task_id": "ai-running", "status": "running"},
    )

    assert ai_page._conflicting_task_id(error) == "ai-running"
    assert (
        ai_page._conflicting_task_id(APIClientError("conflict", status_code=409))
        is None
    )


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
