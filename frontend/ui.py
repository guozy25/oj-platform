from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import streamlit as st

from frontend.ai_page import render_ai_page
from frontend.api_client import APIClientError, OJAPIClient, normalize_base_url
from frontend.forms import build_problem_payload

DEFAULT_API_URL = "http://127.0.0.1:8000"
STATUS_LABELS = {
    "pending": "等待评测",
    "success": "评测完成",
    "error": "评测异常",
}


def _new_client(base_url: str) -> OJAPIClient:
    return OJAPIClient(base_url)


def _init_state() -> None:
    if st.session_state.pop("reset_navigation", False):
        st.session_state.pop("navigation", None)
    if "api_base_url" not in st.session_state:
        configured = os.getenv("OJ_API_URL", DEFAULT_API_URL)
        try:
            st.session_state.api_base_url = normalize_base_url(configured)
        except ValueError:
            st.session_state.api_base_url = DEFAULT_API_URL
    if "api_client" not in st.session_state:
        st.session_state.api_client = _new_client(st.session_state.api_base_url)
    st.session_state.setdefault("current_user", None)
    st.session_state.setdefault("flash", None)
    st.session_state.setdefault("last_submission_id", "")
    st.session_state.setdefault("active_submission_id", "")
    st.session_state.setdefault("active_ai_task_id", "")


def _client() -> OJAPIClient:
    return st.session_state.api_client


def _replace_client(base_url: str) -> None:
    old_client = st.session_state.get("api_client")
    if old_client is not None:
        old_client.close()
    st.session_state.api_base_url = base_url
    st.session_state.api_client = _new_client(base_url)
    st.session_state.current_user = None
    st.session_state.last_submission_id = ""
    st.session_state.active_submission_id = ""
    st.session_state.active_ai_task_id = ""
    st.session_state.reset_navigation = True


def _show_error(error: APIClientError) -> None:
    if error.status_code == 401 and st.session_state.get("current_user") is not None:
        _replace_client(st.session_state.api_base_url)
        _set_flash("登录已失效，请重新登录")
        st.rerun()
    if error.status_code is None:
        st.error(error.message)
        return
    labels = {
        400: "请求参数有误",
        401: "请先登录",
        403: "权限不足",
        404: "资源不存在",
        409: "数据冲突",
        429: "操作过于频繁",
        500: "后端服务异常",
    }
    prefix = labels.get(error.status_code, f"HTTP {error.status_code}")
    st.error(f"{prefix}：{error.message}")


def _set_flash(message: str) -> None:
    st.session_state.flash = message


def _render_flash() -> None:
    message = st.session_state.get("flash")
    if message:
        st.success(message)
        st.session_state.flash = None


def _refresh_after_mutation(message: str, *, show_problem_list: bool = False) -> None:
    if show_problem_list:
        st.session_state.pending_problem_operation = "题目列表"
    _set_flash(message)
    st.rerun()


def _configure_sidebar() -> None:
    st.sidebar.header("连接设置")
    with st.sidebar.form("backend_settings"):
        entered_url = st.text_input("后端 API 地址", value=st.session_state.api_base_url)
        changed = st.form_submit_button("应用地址", use_container_width=True)
    if changed:
        try:
            normalized = normalize_base_url(entered_url)
        except ValueError as exc:
            st.sidebar.error(str(exc))
        else:
            if normalized != st.session_state.api_base_url:
                _replace_client(normalized)
                _set_flash("后端地址已更新，请重新登录")
                st.rerun()

    try:
        health = _client().get("/api/health").data
        if isinstance(health, dict) and health.get("status") == "ok":
            st.sidebar.success("后端已连接")
        else:
            st.sidebar.warning("后端健康状态未知")
    except APIClientError:
        st.sidebar.error("后端不可用")


def _render_auth() -> None:
    st.title("在线评测系统")
    st.caption("登录后可浏览题目、提交代码并查看评测结果。")
    _render_flash()
    login_tab, register_tab = st.tabs(["登录", "注册"])

    with login_tab:
        with st.form("login_form"):
            username = st.text_input("用户名", key="login_username")
            password = st.text_input("密码", type="password", key="login_password")
            submitted = st.form_submit_button("登录", type="primary")
        if submitted:
            try:
                response = _client().post(
                    "/api/auth/login",
                    json={"username": username, "password": password},
                )
            except APIClientError as exc:
                _show_error(exc)
            else:
                st.session_state.current_user = response.data
                _set_flash(f"欢迎回来，{response.data['username']}")
                st.rerun()

    with register_tab:
        with st.form("register_form"):
            username = st.text_input("用户名", key="register_username")
            password = st.text_input("密码（至少 6 位）", type="password", key="register_password")
            password_again = st.text_input(
                "确认密码", type="password", key="register_password_again"
            )
            submitted = st.form_submit_button("创建账号", type="primary")
        if submitted:
            if password != password_again:
                st.error("两次输入的密码不一致")
            else:
                try:
                    _client().post(
                        "/api/users/",
                        json={"username": username, "password": password},
                    )
                except APIClientError as exc:
                    _show_error(exc)
                else:
                    _set_flash("注册成功，请使用新账号登录")
                    st.rerun()


def _sidebar_navigation(user: dict[str, Any]) -> str:
    st.sidebar.divider()
    st.sidebar.write(f"**{user['username']}**")
    st.sidebar.caption(f"角色：{user['role']}\n\nID：{user['user_id']}")
    pages = ["我的信息", "题目", "提交代码", "提交记录", "AI 智能命题"]
    if user.get("role") == "admin":
        pages.extend(["用户管理", "访问审计"])
    page = st.sidebar.radio("导航", pages, key="navigation")

    if st.sidebar.button("退出登录", use_container_width=True):
        try:
            _client().post("/api/auth/logout")
        except APIClientError:
            pass
        _replace_client(st.session_state.api_base_url)
        _set_flash("已退出登录")
        st.rerun()
    return page


def _profile_page(user: dict[str, Any]) -> None:
    st.header("我的信息")
    try:
        profile = _client().get(f"/api/users/{user['user_id']}").data
    except APIClientError as exc:
        _show_error(exc)
        return

    st.session_state.current_user = {
        "user_id": profile["user_id"],
        "username": profile["username"],
        "role": profile["role"],
    }
    first, second, third = st.columns(3)
    first.metric("提交次数", profile["submit_count"])
    second.metric("通过题数", profile["resolve_count"])
    third.metric("当前角色", profile["role"])
    st.write(f"**用户名：** {profile['username']}")
    st.write(f"**用户 ID：** `{profile['user_id']}`")
    st.write(f"**加入日期：** {profile['join_time']}")


def _load_problems() -> list[dict[str, str]]:
    data = _client().get("/api/problems/").data
    if not isinstance(data, list):
        raise APIClientError("题目列表响应格式不正确", status_code=500)
    return data


def _render_problem_detail(problem_id: str) -> None:
    try:
        problem = _client().get(f"/api/problems/{problem_id}").data
    except APIClientError as exc:
        _show_error(exc)
        return
    st.subheader(f"{problem['id']} · {problem['title']}")
    meta = []
    if problem.get("difficulty"):
        meta.append(f"难度：{problem['difficulty']}")
    if problem.get("author"):
        meta.append(f"作者：{problem['author']}")
    meta.extend(
        [
            f"时间限制：{problem['time_limit']} s",
            f"内存限制：{problem['memory_limit']} MB",
        ]
    )
    st.caption(" · ".join(meta))
    if problem.get("tags"):
        st.write("标签：" + " / ".join(problem["tags"]))
    st.markdown(problem["description"])
    st.markdown("#### 输入说明")
    st.markdown(problem["input_description"])
    st.markdown("#### 输出说明")
    st.markdown(problem["output_description"])
    st.markdown("#### 数据范围")
    st.markdown(problem["constraints"])
    st.markdown("#### 样例")
    for index, sample in enumerate(problem["samples"], start=1):
        left, right = st.columns(2)
        left.code(sample["input"], language="text")
        right.code(sample["output"], language="text")
        st.caption(f"样例 {index}：左侧为输入，右侧为输出")
    if problem.get("hint"):
        st.markdown("#### 提示")
        st.markdown(problem["hint"])
    if problem.get("source"):
        st.caption(f"来源：{problem['source']}")


def _problem_form(
    form_key: str,
    button_label: str,
    *,
    initial: dict[str, Any] | None = None,
    lock_id: bool | None = None,
) -> dict[str, Any] | None:
    data = initial or {}
    id_is_locked = initial is not None if lock_id is None else lock_id
    samples = json.dumps(
        data.get("samples", [{"input": "1 2", "output": "3"}]),
        ensure_ascii=False,
        indent=2,
    )
    testcases = json.dumps(
        data.get("testcases", [{"input": "1 2", "output": "3"}]),
        ensure_ascii=False,
        indent=2,
    )
    with st.form(form_key):
        id_column, title_column = st.columns([1, 2])
        problem_id = id_column.text_input(
            "题目 ID",
            value=data.get("id", ""),
            disabled=id_is_locked,
        )
        title = title_column.text_input("标题", value=data.get("title", ""))
        description = st.text_area("题目描述（支持 Markdown）", value=data.get("description", ""))
        input_description = st.text_area(
            "输入说明", value=data.get("input_description", "")
        )
        output_description = st.text_area(
            "输出说明", value=data.get("output_description", "")
        )
        constraints = st.text_area("数据范围", value=data.get("constraints", ""))
        st.caption('JSON 格式示例：[{"input": "1 2", "output": "3"}]')
        samples_text = st.text_area("样例 JSON", value=samples, height=140)
        testcases_text = st.text_area("测试点 JSON", value=testcases, height=180)
        hint = st.text_area("提示", value=data.get("hint", ""))
        source = st.text_input("来源", value=data.get("source", ""))
        tags = st.text_input(
            "标签（逗号分隔）", value=", ".join(data.get("tags", []))
        )
        limit_column, memory_column = st.columns(2)
        time_limit = limit_column.number_input(
            "时间限制（秒）",
            min_value=0.01,
            max_value=3600.0,
            value=float(data.get("time_limit") or 3.0),
        )
        memory_limit = memory_column.number_input(
            "内存限制（MB）",
            min_value=1,
            max_value=65_536,
            value=int(data.get("memory_limit") or 128),
        )
        author_column, difficulty_column = st.columns(2)
        author = author_column.text_input("作者", value=data.get("author", ""))
        difficulty = difficulty_column.text_input(
            "难度", value=data.get("difficulty", "")
        )
        submitted = st.form_submit_button(button_label, type="primary")

    if not submitted:
        return None
    values = {
        "id": data.get("id", problem_id) if id_is_locked else problem_id,
        "title": title,
        "description": description,
        "input_description": input_description,
        "output_description": output_description,
        "constraints": constraints,
        "samples": samples_text,
        "testcases": testcases_text,
        "hint": hint,
        "source": source,
        "tags": tags,
        "time_limit": time_limit,
        "memory_limit": memory_limit,
        "author": author,
        "difficulty": difficulty,
    }
    try:
        return build_problem_payload(values)
    except ValueError as exc:
        st.error(str(exc))
        return None


def _problem_page(user: dict[str, Any]) -> None:
    st.header("题目")
    pending_operation = st.session_state.pop("pending_problem_operation", None)
    if pending_operation is not None:
        st.session_state.problem_operation = pending_operation
    operation = st.radio(
        "操作",
        ["题目列表", "新建题目", "编辑题目"],
        horizontal=True,
        label_visibility="collapsed",
        key="problem_operation",
    )
    try:
        problems = _load_problems()
    except APIClientError as exc:
        _show_error(exc)
        return

    if operation == "题目列表":
        if not problems:
            st.info("暂无题目，可切换到“新建题目”开始录入。")
            return
        st.dataframe(problems, use_container_width=True, hide_index=True)
        selected = st.selectbox(
            "查看题目",
            [item["id"] for item in problems],
            format_func=lambda problem_id: next(
                item["title"] for item in problems if item["id"] == problem_id
            ),
        )
        _render_problem_detail(selected)
        return

    if operation == "新建题目":
        generated = st.session_state.get("ai_generated_problem")
        generated_form_key = st.session_state.get("ai_generated_form_key", "manual")
        if generated is not None:
            st.info("已载入 AI 生成结果。请检查并修改后再正式创建题目。")
        payload = _problem_form(
            f"create_problem_{generated_form_key}",
            "创建题目",
            initial=generated,
            lock_id=False,
        )
        if payload is not None:
            try:
                response = _client().post("/api/problems/", json=payload)
            except APIClientError as exc:
                _show_error(exc)
            else:
                st.session_state.pop("ai_generated_problem", None)
                st.session_state.pop("ai_generated_form_key", None)
                _refresh_after_mutation(
                    f"{response.msg}：{payload['id']}", show_problem_list=True
                )
        return

    if not problems:
        st.info("暂无可编辑的题目。")
        return
    generated = st.session_state.get("ai_generated_problem")
    generated_id = generated.get("id") if isinstance(generated, dict) else None
    problem_ids = [item["id"] for item in problems]
    default_index = problem_ids.index(generated_id) if generated_id in problem_ids else 0
    problem_id = st.selectbox("选择题目", problem_ids, index=default_index)
    try:
        detail = _client().get(f"/api/problems/{problem_id}").data
    except APIClientError as exc:
        _show_error(exc)
        return
    form_initial = generated if generated_id == problem_id else detail
    if generated_id == problem_id:
        st.info("已载入 AI 改进结果。请检查差异后再保存。")
    payload = _problem_form(
        f"edit_problem_{problem_id}_{st.session_state.get('ai_generated_form_key', 'manual')}",
        "保存修改",
        initial=form_initial,
    )
    if payload is not None:
        try:
            response = _client().put(f"/api/problems/{problem_id}", json=payload)
        except APIClientError as exc:
            _show_error(exc)
        else:
            st.session_state.pop("ai_generated_problem", None)
            st.session_state.pop("ai_generated_form_key", None)
            _refresh_after_mutation(response.msg, show_problem_list=True)

    if user.get("role") == "admin":
        st.divider()
        st.subheader("管理员操作")
        try:
            visibility_data = _client().get(
                f"/api/problems/{problem_id}/log_visibility"
            ).data
        except APIClientError as exc:
            _show_error(exc)
        else:
            current_visibility = bool(visibility_data["public_cases"])
            current_label = "已向已登录用户公开" if current_visibility else "仅管理员可见"
            st.info(f"当前后端状态：{current_label}")

            widget_key = f"visibility_{problem_id}"
            server_state_key = f"visibility_server_{problem_id}"
            if st.session_state.get(server_state_key) != current_visibility:
                st.session_state[widget_key] = current_visibility
                st.session_state[server_state_key] = current_visibility

            desired_visibility = st.radio(
                "测试点日志可见性",
                [False, True],
                format_func=lambda public: (
                    "向已登录用户公开详情" if public else "仅管理员可见详情"
                ),
                key=widget_key,
            )
            if st.button("更新日志可见性"):
                try:
                    response = _client().put(
                        f"/api/problems/{problem_id}/log_visibility",
                        json={"public_cases": desired_visibility},
                    )
                except APIClientError as exc:
                    _show_error(exc)
                else:
                    st.session_state[server_state_key] = None
                    _refresh_after_mutation(response.msg)

        confirmed = st.checkbox(
            f"我确认删除题目 {problem_id}", key=f"delete_confirm_{problem_id}"
        )
        if st.button("删除题目", disabled=not confirmed, type="secondary"):
            try:
                response = _client().delete(f"/api/problems/{problem_id}")
            except APIClientError as exc:
                _show_error(exc)
            else:
                _refresh_after_mutation(
                    f"{response.msg}：{problem_id}", show_problem_list=True
                )


def _submit_page() -> None:
    st.header("提交代码")
    try:
        problems = _load_problems()
        language_data = _client().get("/api/languages/").data
        languages = language_data.get("name", [])
    except APIClientError as exc:
        _show_error(exc)
        return
    if not problems:
        st.info("尚未录入题目。")
        return
    if not languages:
        st.warning("后端尚未注册可用语言。")
        return

    with st.form("submit_code"):
        problem_id = st.selectbox(
            "题目",
            [item["id"] for item in problems],
            format_func=lambda selected: next(
                f"{item['id']} · {item['title']}" for item in problems if item["id"] == selected
            ),
        )
        language = st.selectbox("语言", languages)
        code = st.text_area(
            "源代码",
            height=420,
            placeholder="在这里输入完整程序……",
        )
        submitted = st.form_submit_button("提交评测", type="primary")
    if submitted:
        if not code:
            st.error("源代码不能为空")
            return
        try:
            response = _client().post(
                "/api/submissions/",
                json={"problem_id": problem_id, "language": language, "code": code},
            )
        except APIClientError as exc:
            _show_error(exc)
        else:
            submission_id = response.data["submission_id"]
            st.session_state.last_submission_id = submission_id
            st.session_state.active_submission_id = submission_id
            st.success(f"已提交，ID：{submission_id}")
            st.info("请前往“提交记录”页面查看或刷新评测结果。")


def _render_submission_detail(submission_id: str, is_admin: bool) -> None:
    try:
        detail = _client().get(f"/api/submissions/{submission_id}").data
    except APIClientError as exc:
        _show_error(exc)
        return

    status = detail["status"]
    st.subheader(f"提交 {detail['submission_id']}")
    status_column, score_column, count_column = st.columns(3)
    status_column.metric("状态", STATUS_LABELS.get(status, status))
    score_column.metric("得分", detail["score"] if detail["score"] is not None else "—")
    count_column.metric(
        "满分", detail["counts"] if detail["counts"] is not None else "—"
    )
    if status == "pending":
        st.info("评测任务正在排队或执行，请点击“查询 / 刷新”获取最新状态。")
    if detail.get("error_info"):
        st.error(f"评测错误：{detail['error_info']}")
    if detail.get("compile_info") is not None:
        st.markdown("#### 编译信息")
        st.json(detail["compile_info"])
    if detail.get("run_info") is not None:
        st.markdown("#### 运行信息")
        st.json(detail["run_info"])

    log_column, rejudge_column = st.columns(2)
    show_log = log_column.button("查看评测日志", key=f"log_{submission_id}")
    if is_admin:
        rejudge = rejudge_column.button("重新评测", key=f"rejudge_{submission_id}")
        if rejudge:
            try:
                response = _client().put(f"/api/submissions/{submission_id}/rejudge")
            except APIClientError as exc:
                _show_error(exc)
            else:
                st.session_state.last_submission_id = submission_id
                _refresh_after_mutation(response.msg)
    if show_log:
        try:
            log = _client().get(f"/api/submissions/{submission_id}/log").data
        except APIClientError as exc:
            _show_error(exc)
        else:
            st.markdown("#### 评测日志")
            score_column, full_score_column = st.columns(2)
            score_column.metric("得分", log.get("score") if log.get("score") is not None else "—")
            full_score_column.metric(
                "满分", log.get("counts") if log.get("counts") is not None else "—"
            )
            details = log.get("details")
            if details is None:
                st.caption("当前题目未公开逐测试点详情。")
            elif details:
                st.dataframe(details, use_container_width=True, hide_index=True)
            else:
                st.info("暂无测试点日志。")


def _submissions_page(user: dict[str, Any]) -> None:
    st.header("提交记录")
    st.caption("评测中的提交可通过“查询 / 刷新”手动获取最新结果。")
    is_admin = user.get("role") == "admin"
    with st.expander("列表筛选", expanded=True):
        filter_type = "按我的用户 ID"
        if is_admin:
            filter_type = st.radio(
                "主要筛选条件", ["按用户 ID", "按题目 ID"], horizontal=True
            )
        first, second, third = st.columns(3)
        if filter_type == "按题目 ID":
            filter_value = first.text_input("题目 ID")
            filter_key = "problem_id"
        else:
            filter_value = first.text_input(
                "用户 ID",
                value=user["user_id"],
                disabled=not is_admin,
            )
            filter_key = "user_id"
        status = second.selectbox("状态", ["全部", "pending", "success", "error"])
        page = third.number_input("页码", min_value=1, value=1)
        page_size = st.number_input("每页条数", min_value=1, max_value=100, value=20)
        load_list = st.button("加载提交列表")
    if load_list:
        if not filter_value.strip():
            st.error("筛选值不能为空")
        else:
            params: dict[str, Any] = {
                filter_key: filter_value.strip(),
                "page": int(page),
                "page_size": int(page_size),
            }
            if status != "全部":
                params["status"] = status
            try:
                listing = _client().get("/api/submissions/", params=params).data
            except APIClientError as exc:
                _show_error(exc)
            else:
                st.write(f"共 {listing['total']} 条")
                if listing["submissions"]:
                    display_rows = [
                        {
                            "提交 ID": item["submission_id"],
                            "状态": STATUS_LABELS.get(item["status"], item["status"]),
                            "得分": item.get("score", "—"),
                            "满分": item.get("counts", "—"),
                        }
                        for item in listing["submissions"]
                    ]
                    st.dataframe(
                        display_rows, use_container_width=True, hide_index=True
                    )
                else:
                    st.info("没有符合条件的提交。")

    st.divider()
    with st.form("submission_lookup"):
        submission_id = st.text_input(
            "提交 ID", value=st.session_state.get("last_submission_id", "")
        )
        lookup = st.form_submit_button("查询 / 刷新", type="primary")
    if lookup:
        if not submission_id.strip():
            st.error("请输入提交 ID")
        else:
            st.session_state.last_submission_id = submission_id.strip()
            st.session_state.active_submission_id = submission_id.strip()

    active_submission_id = st.session_state.get("active_submission_id", "")
    if active_submission_id:
        _render_submission_detail(active_submission_id, is_admin)


def _users_page() -> None:
    st.header("用户管理")
    page_column, size_column = st.columns(2)
    page = page_column.number_input("页码", min_value=1, value=1, key="users_page")
    page_size = size_column.number_input(
        "每页条数", min_value=1, max_value=100, value=20, key="users_page_size"
    )
    try:
        listing = _client().get(
            "/api/users/", params={"page": int(page), "page_size": int(page_size)}
        ).data
    except APIClientError as exc:
        _show_error(exc)
        return
    st.write(f"共 {listing['total']} 个用户")
    st.dataframe(listing["users"], use_container_width=True, hide_index=True)

    st.subheader("修改角色")
    with st.form("update_role"):
        user_id = st.text_input("目标用户 ID")
        role = st.selectbox("新角色", ["user", "admin", "banned"])
        update = st.form_submit_button("更新角色")
    if update:
        try:
            response = _client().put(f"/api/users/{user_id.strip()}/role", json={"role": role})
        except APIClientError as exc:
            _show_error(exc)
        else:
            _refresh_after_mutation(response.msg)

    st.subheader("创建管理员")
    with st.form("create_admin"):
        username = st.text_input("管理员用户名")
        password = st.text_input("初始密码", type="password")
        create = st.form_submit_button("创建管理员")
    if create:
        try:
            response = _client().post(
                "/api/users/admin", json={"username": username, "password": password}
            )
        except APIClientError as exc:
            _show_error(exc)
        else:
            _refresh_after_mutation(f"{response.msg}：{response.data['username']}")


def _audit_page() -> None:
    st.header("访问审计")
    first, second = st.columns(2)
    user_id = first.text_input("用户 ID（可选）")
    problem_id = second.text_input("题目 ID（可选）")
    third, fourth = st.columns(2)
    page = third.number_input("页码", min_value=1, value=1, key="audit_page")
    page_size = fourth.number_input(
        "每页条数", min_value=1, max_value=100, value=20, key="audit_page_size"
    )
    if st.button("查询访问日志", type="primary"):
        params: dict[str, Any] = {"page": int(page), "page_size": int(page_size)}
        if user_id.strip():
            params["user_id"] = user_id.strip()
        if problem_id.strip():
            params["problem_id"] = problem_id.strip()
        try:
            logs = _client().get("/api/logs/access/", params=params).data
        except APIClientError as exc:
            _show_error(exc)
        else:
            if logs:
                st.dataframe(logs, use_container_width=True, hide_index=True)
            else:
                st.info("没有符合条件的访问日志。")


def run() -> None:
    st.set_page_config(page_title="Async OJ", page_icon="🧪", layout="wide")
    _init_state()
    _configure_sidebar()
    user = st.session_state.current_user
    if user is None:
        _render_auth()
        return

    _render_flash()
    page = _sidebar_navigation(user)
    try:
        ai_problems = _load_problems() if page == "AI 智能命题" else []
    except APIClientError as exc:
        _show_error(exc)
        return
    renderers: dict[str, Callable[[], None]] = {
        "我的信息": lambda: _profile_page(user),
        "题目": lambda: _problem_page(user),
        "提交代码": _submit_page,
        "提交记录": lambda: _submissions_page(user),
        "用户管理": _users_page,
        "访问审计": _audit_page,
        "AI 智能命题": lambda: render_ai_page(_client(), ai_problems, _show_error),
    }
    renderers[page]()
