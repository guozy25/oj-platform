from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import streamlit as st

from frontend.api_client import APIClientError, OJAPIClient

TASK_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "正在生成",
    "completed": "生成完成",
    "cancelled": "已中断",
    "failed": "生成失败",
}


def _model_config_payload(
    provider_url: str,
    model: str,
    api_key: str,
    input_price: float,
    output_price: float,
    price_unit: int,
    max_output_tokens: int,
) -> dict[str, Any]:
    return {
        "provider_url": provider_url,
        "model": model,
        "api_key": api_key,
        "input_price": input_price,
        "output_price": output_price,
        "price_unit": int(price_unit),
        "max_output_tokens": int(max_output_tokens),
    }


def _render_config(
    client: OJAPIClient, show_error: Callable[[APIClientError], None]
) -> bool:
    try:
        current = client.get("/api/ai/model-config").data
    except APIClientError as exc:
        show_error(exc)
        return False

    configured = bool(current.get("api_key_configured"))
    if configured:
        habit_name = current.get("habit_config_name")
        suffix = f" · 习惯配置：{habit_name}" if habit_name else ""
        st.success(
            f"已选定模型：{current['model']} · {current['provider_url']}{suffix}"
        )
        st.caption("API Key 仅保存在后端进程内存中，服务重启后需要重新配置。")
    else:
        st.info("请先配置一个兼容 OpenAI Chat Completions 的模型服务。")

    with st.expander("模型与价格配置", expanded=not configured):
        with st.form("ai_model_config"):
            provider_url = st.text_input(
                "Provider URL",
                value=current.get("provider_url", "https://api.openai.com/v1"),
                help="填写 API 根地址或完整的 /chat/completions 地址。",
            )
            model = st.text_input("模型名称", value=current.get("model", ""))
            api_key = st.text_input(
                "API Key",
                type="password",
                help="密钥不会显示在响应、日志或页面中。每次更新配置都需要重新填写。",
            )
            first, second, third, fourth = st.columns(4)
            input_price = first.number_input(
                "输入价格（USD）",
                min_value=0.0,
                value=float(current.get("input_price", 0.0)),
                format="%.6f",
            )
            output_price = second.number_input(
                "输出价格（USD）",
                min_value=0.0,
                value=float(current.get("output_price", 0.0)),
                format="%.6f",
            )
            price_unit = third.number_input(
                "计价 Token 单位",
                min_value=1,
                value=int(current.get("price_unit", 1_000_000)),
            )
            max_output_tokens = fourth.number_input(
                "单次最大输出 Token",
                min_value=256,
                max_value=128_000,
                value=int(current.get("max_output_tokens", 12_000)),
                step=256,
                help="限制单次命题请求的最大输出；越大越可能生成更完整内容，但费用和耗时也更高。",
            )
            habit_name = st.text_input(
                "习惯配置名称（保存为习惯配置时必填）",
                max_chars=100,
                placeholder="例如：GPT-4o 日常命题",
            )
            select_column, save_column = st.columns(2)
            selected = select_column.form_submit_button(
                "选定模型配置", type="primary", use_container_width=True
            )
            saved_as_habit = save_column.form_submit_button(
                "保存为习惯配置", type="primary", use_container_width=True
            )
        if selected or saved_as_habit:
            if not api_key.strip():
                st.error("请输入 API Key；出于安全考虑，后端不会回传已经保存的密钥。")
            elif saved_as_habit and not habit_name.strip():
                st.error("保存为习惯配置时必须填写配置名称。")
            else:
                try:
                    payload = _model_config_payload(
                        provider_url,
                        model,
                        api_key,
                        input_price,
                        output_price,
                        int(price_unit),
                        int(max_output_tokens),
                    )
                    if saved_as_habit:
                        client.post(
                            "/api/ai/habit-configs/",
                            json={"name": habit_name.strip(), **payload},
                        )
                    else:
                        client.put("/api/ai/model-config", json=payload)
                except APIClientError as exc:
                    show_error(exc)
                else:
                    st.session_state.flash = (
                        f"习惯配置“{habit_name.strip()}”已保存并选定"
                        if saved_as_habit
                        else "模型配置已选定"
                    )
                    st.rerun()
    return configured


def _render_habit_configs(
    client: OJAPIClient, show_error: Callable[[APIClientError], None]
) -> None:
    st.subheader("习惯配置")
    try:
        habits = client.get("/api/ai/habit-configs/").data
    except APIClientError as exc:
        show_error(exc)
        return

    st.caption(f"已保存 {len(habits)}/10 个；API Key 不会在页面或接口中显示。")
    if not habits:
        st.info("还没有习惯配置，可在上方填写模型参数后保存。")
        return

    st.dataframe(
        [
            {
                "名称": item["name"],
                "模型": item["model"],
                "Provider URL": item["provider_url"],
                "输入价格": item["input_price"],
                "输出价格": item["output_price"],
                "计价单位": item["price_unit"],
                "最大输出 Token": item.get("max_output_tokens", 12_000),
                "状态": "当前使用" if item.get("selected") else "",
            }
            for item in habits
        ],
        hide_index=True,
        use_container_width=True,
    )
    by_id = {item["config_id"]: item for item in habits}
    default_index = next(
        (index for index, item in enumerate(habits) if item.get("selected")), 0
    )
    config_id = st.selectbox(
        "选择习惯配置",
        list(by_id),
        index=default_index,
        format_func=lambda value: (
            f"{by_id[value]['name']} · {by_id[value]['model']}"
            + ("（当前）" if by_id[value].get("selected") else "")
        ),
        key="ai_habit_config_selection",
    )
    habit = by_id[config_id]
    st.caption(
        f"{habit['provider_url']} · 输入 ${habit['input_price']} / "
        f"输出 ${habit['output_price']} · 每 {habit['price_unit']} Token"
    )
    if st.button(
        "选用此习惯配置",
        type="primary",
        use_container_width=True,
        key=f"select_habit_{config_id}",
    ):
        try:
            client.put(f"/api/ai/habit-configs/{config_id}/select")
        except APIClientError as exc:
            show_error(exc)
        else:
            st.session_state.flash = f"已选用习惯配置“{habit['name']}”"
            st.rerun()

    with st.expander("修改所选习惯配置"):
        with st.form(f"edit_habit_{config_id}"):
            name = st.text_input("配置名称", value=habit["name"], max_chars=100)
            provider_url = st.text_input("Provider URL", value=habit["provider_url"])
            model = st.text_input("模型名称", value=habit["model"])
            api_key = st.text_input(
                "新 API Key（选填）",
                type="password",
                help="留空会继续使用该习惯配置原有的密钥。",
            )
            first, second, third, fourth = st.columns(4)
            input_price = first.number_input(
                "输入价格（USD）",
                min_value=0.0,
                value=float(habit["input_price"]),
                format="%.6f",
            )
            output_price = second.number_input(
                "输出价格（USD）",
                min_value=0.0,
                value=float(habit["output_price"]),
                format="%.6f",
            )
            price_unit = third.number_input(
                "计价 Token 单位",
                min_value=1,
                value=int(habit["price_unit"]),
            )
            max_output_tokens = fourth.number_input(
                "单次最大输出 Token",
                min_value=256,
                max_value=128_000,
                value=int(habit.get("max_output_tokens", 12_000)),
                step=256,
            )
            updated = st.form_submit_button(
                "保存习惯配置修改", type="primary", use_container_width=True
            )
        if updated:
            payload = {
                "name": name.strip(),
                "provider_url": provider_url,
                "model": model,
                "input_price": input_price,
                "output_price": output_price,
                "price_unit": int(price_unit),
                "max_output_tokens": int(max_output_tokens),
            }
            if api_key.strip():
                payload["api_key"] = api_key
            try:
                client.put(f"/api/ai/habit-configs/{config_id}", json=payload)
            except APIClientError as exc:
                show_error(exc)
            else:
                st.session_state.flash = f"习惯配置“{name.strip()}”已更新"
                st.rerun()

        delete_confirmed = st.checkbox(
            f"确认删除习惯配置“{habit['name']}”",
            key=f"delete_habit_confirm_{config_id}",
        )
        if st.button(
            "删除习惯配置",
            disabled=not delete_confirmed,
            key=f"delete_habit_{config_id}",
        ):
            try:
                client.delete(f"/api/ai/habit-configs/{config_id}")
            except APIClientError as exc:
                show_error(exc)
            else:
                st.session_state.pop("ai_habit_config_selection", None)
                st.session_state.flash = f"习惯配置“{habit['name']}”已删除"
                st.rerun()


def _create_task(
    client: OJAPIClient,
    problems: list[dict[str, str]],
    show_error: Callable[[APIClientError], None],
    configured: bool,
    running_task_id: str | None,
) -> None:
    st.subheader("命题需求")
    if running_task_id:
        st.info(
            f"已有智能命题任务正在运行：{running_task_id}。"
            "请在当前任务完成或中断后再创建新任务。"
        )
    problem_options = [""] + [item["id"] for item in problems]
    with st.form("ai_problem_task"):
        knowledge = st.text_input(
            "知识点",
            placeholder="例如：前缀和、二分答案、动态规划",
        )
        first, second = st.columns(2)
        difficulty = first.selectbox("期望难度", ["入门", "简单", "中等", "困难"])
        reference_id = second.selectbox(
            "参考或改进已有题目（可选）",
            problem_options,
            format_func=lambda value: "创建全新题目" if not value else value,
        )
        details = st.text_area(
            "多维要求",
            height=180,
            placeholder=(
                "说明题目背景、输入规模、希望考查的边界、禁止采用的套路，"
                "以及其他命题偏好。"
            ),
        )
        submitted = st.form_submit_button(
            "开始智能命题",
            type="primary",
            disabled=not configured or running_task_id is not None,
        )
    if submitted:
        if not knowledge.strip() or not details.strip():
            st.error("请完整填写知识点和多维要求。")
            return
        requirement = (
            f"知识点：{knowledge.strip()}\n"
            f"期望难度：{difficulty}\n"
            f"多维要求：{details.strip()}"
        )
        payload: dict[str, Any] = {"requirement": requirement}
        if reference_id:
            payload["problem_id"] = reference_id
        try:
            response = client.post("/api/ai/problem-tasks/", json=payload)
        except APIClientError as exc:
            conflict_task_id = _conflicting_task_id(exc)
            if conflict_task_id:
                st.session_state.active_ai_task_id = conflict_task_id
                st.warning("已有任务正在运行，已自动恢复该任务的进度页面。")
            else:
                show_error(exc)
        else:
            st.session_state.active_ai_task_id = response.data["task_id"]
            st.session_state.flash = "智能命题任务已创建"
            st.rerun()


def _conflicting_task_id(error: APIClientError) -> str | None:
    if error.status_code != 409 or not isinstance(error.data, dict):
        return None
    task_id = error.data.get("task_id")
    return task_id if isinstance(task_id, str) and task_id else None


def _render_usage(usage: dict | None) -> None:
    if not usage:
        return
    first, second, third, fourth = st.columns(4)
    first.metric("输入 Token", usage.get("input_tokens", 0))
    second.metric("输出 Token", usage.get("output_tokens", 0))
    third.metric("总 Token", usage.get("total_tokens", 0))
    fourth.metric("费用（USD）", f"{usage.get('cost', 0):.8f}")
    if usage.get("estimated"):
        st.caption("模型服务未返回完整用量；当前 Token 与费用按字符数近似估算。")
    else:
        st.caption(f"费用按照模型返回的 Token 用量和每 {usage.get('price_unit')} Token 单价计算。")


def _render_partial_output(partial_output: str | None) -> None:
    if not partial_output:
        return
    with st.expander("查看已收到的部分模型输出", expanded=False):
        st.code(partial_output, language="json")


def _render_result(
    client: OJAPIClient,
    result: dict,
    problems: list[dict[str, str]],
    *,
    show_error: Callable[[APIClientError], None],
    artifact_key: str,
) -> None:
    problem = result["problem"]
    validation = result["validation"]
    st.success("题目已生成，并已使用标准解重新计算全部样例和测试点输出。")
    st.subheader(f"{problem['id']} · {problem['title']}")
    st.markdown(problem["description"])
    first, second = st.columns(2)
    with first:
        st.markdown("#### 输入格式")
        st.markdown(problem["input_description"])
    with second:
        st.markdown("#### 输出格式")
        st.markdown(problem["output_description"])
    st.markdown("#### 数据范围")
    st.markdown(problem["constraints"])

    metrics = st.columns(2)
    metrics[0].metric("样例", validation["sample_count"])
    metrics[1].metric("测试点", validation["testcase_count"])

    quality = validation.get("quality_gate", {})
    if quality.get("passed"):
        st.success("测试点质量门槛已通过：输入独立、规模分层，且覆盖目的具体。")
        quality_metrics = st.columns(4)
        quality_metrics[0].metric("边界场景", quality["boundary_case_count"])
        quality_metrics[1].metric("大规模/性能场景", quality["stress_case_count"])
        quality_metrics[2].metric(
            "有效测试点",
            f"{quality['effective_testcase_count']}/{validation['testcase_count']}",
        )
        quality_metrics[3].metric("不同输入长度", quality["distinct_input_sizes"])

    purposes = [
        {
            "测试点": index,
            "覆盖目的": purpose,
            "输入预览": problem["testcases"][index - 1]["input"][:120],
        }
        for index, purpose in enumerate(validation["testcase_purposes"], start=1)
    ]
    st.dataframe(purposes, hide_index=True, use_container_width=True)
    for warning in validation.get("warnings", []):
        st.warning(warning)


    with st.expander("查看样例与测试点 JSON"):
        st.json({"samples": problem["samples"], "testcases": problem["testcases"]})
    with st.expander("查看 Python 3 标准解"):
        st.code(result["reference_solution"], language="python")

    existing_ids = {item["id"] for item in problems}
    operation = "编辑题目" if problem["id"] in existing_ids else "新建题目"
    accepted_key = f"accepted_ai_{artifact_key}"
    if st.session_state.get(accepted_key):
        st.success(f"题目 {problem['id']} 已接受并加入题库。")
    elif st.button(
        "接受并更新题目" if operation == "编辑题目" else "接受新题",
        type="primary",
        key=f"accept_ai_{artifact_key}",
        help="直接将当前已通过自动校验的版本写入题库。",
    ):
        payload = dict(problem)
        payload.pop("public_cases", None)
        try:
            (
                client.put(f"/api/problems/{problem['id']}", json=payload)
                if operation == "编辑题目"
                else client.post("/api/problems/", json=payload)
            )
        except APIClientError as exc:
            show_error(exc)
        else:
            st.session_state[accepted_key] = True
            st.session_state.flash = f"题目 {problem['id']} 已加入题库"
            st.rerun()

    if st.button(
        f"导入到“{operation}”页面",
        type="primary",
        key=f"import_ai_revision_{artifact_key}",
    ):
        st.session_state.ai_generated_problem = problem
        st.session_state.ai_generated_form_key = st.session_state.get(
            "active_ai_task_id", problem["id"]
        )
        st.session_state.pending_problem_operation = operation
        # The navigation radio already exists in this Streamlit run, so changing
        # its keyed state here raises StreamlitAPIException. Apply it before the
        # widget is created on the next rerun instead.
        st.session_state.pending_navigation = "题目"
        st.rerun()
    st.download_button(
        "下载生成结果 JSON",
        data=json.dumps(result, ensure_ascii=False, indent=2),
        file_name=f"{problem['id']}-ai-result.json",
        mime="application/json",
        key=f"download_ai_revision_{artifact_key}",
    )


def _render_revision_workspace(
    client: OJAPIClient,
    task: dict,
    problems: list[dict[str, str]],
    show_error: Callable[[APIClientError], None],
    configured: bool,
) -> None:
    task_id = task["task_id"]
    try:
        revisions = client.get(f"/api/ai/problem-tasks/{task_id}/revisions/").data
    except APIClientError as exc:
        show_error(exc)
        return
    if not revisions:
        return

    st.subheader("多轮改题")
    with st.expander("查看完整对话与版本链"):
        for item in revisions:
            source = (
                "初稿"
                if item["base_revision"] is None
                else f"基于第 {item['base_revision']} 版"
            )
            with st.chat_message("user"):
                st.markdown(f"**第 {item['revision']} 轮要求（{source}）**")
                st.write(item["feedback"])
            with st.chat_message("assistant"):
                st.write(f"已生成并通过验证：{item['title']}")

    revision_numbers = [item["revision"] for item in revisions]
    latest_revision = max(revision_numbers)
    selector_key = f"ai_revision_selection_{task_id}"
    seen_key = f"ai_revision_seen_{task_id}"
    if st.session_state.get(seen_key) != latest_revision:
        st.session_state[selector_key] = latest_revision
        st.session_state[seen_key] = latest_revision
    selected_revision = st.selectbox(
        "查看或作为下一轮基础的版本",
        revision_numbers,
        format_func=lambda value: f"第 {value} 版",
        key=selector_key,
    )
    try:
        selected = client.get(
            f"/api/ai/problem-tasks/{task_id}/revisions/{selected_revision}"
        ).data
    except APIClientError as exc:
        show_error(exc)
        return

    source_label = (
        "初始生成"
        if selected["base_revision"] is None
        else f"由第 {selected['base_revision']} 版修改而来"
    )
    st.caption(f"当前查看第 {selected_revision} 版 · {source_label}")
    _render_result(
        client,
        selected["result"],
        problems,
        show_error=show_error,
        artifact_key=f"{task_id}_{selected_revision}",
    )

    if task["status"] in {"pending", "running"}:
        st.info("新版本正在生成；期间仍可查看和导出已验证的历史版本。")
        return
    if not task.get("can_refine", False):
        st.caption("只有任务创建者可以追加修改要求。")
        return

    with st.form(f"ai_refinement_{task_id}_{selected_revision}"):
        feedback = st.text_area(
            f"继续修改第 {selected_revision} 版",
            height=140,
            placeholder=(
                "例如：保留核心算法，换成校园背景；加强负数与上界测试；"
                "将难度调整为中等。"
            ),
        )
        refine = st.form_submit_button(
            "生成下一版",
            type="primary",
            disabled=not configured,
        )
    if refine:
        if not feedback.strip():
            st.error("请输入本轮修改要求。")
            return
        try:
            response = client.post(
                f"/api/ai/problem-tasks/{task_id}/refinements/",
                json={
                    "feedback": feedback.strip(),
                    "base_revision": selected_revision,
                },
            )
        except APIClientError as exc:
            show_error(exc)
        else:
            st.session_state.flash = (
                f"已基于第 {selected_revision} 版开始第 "
                f"{response.data['revision']} 轮改题"
            )
            st.rerun()


def _render_active_task(
    client: OJAPIClient,
    problems: list[dict[str, str]],
    show_error: Callable[[APIClientError], None],
    configured: bool,
) -> None:
    task_id = st.session_state.get("active_ai_task_id", "")
    if not task_id:
        return
    st.divider()
    st.subheader("当前任务")
    st.caption(f"任务 ID：{task_id}")
    try:
        task = client.get(f"/api/ai/problem-tasks/{task_id}").data
    except APIClientError as exc:
        show_error(exc)
        return

    status = task["status"]
    first, second = st.columns([1, 3])
    first.metric("状态", TASK_STATUS_LABELS.get(status, status))
    second.info(task.get("progress") or "等待进度")
    _render_usage(task.get("usage"))

    if status in {"pending", "running"}:
        left, right = st.columns(2)
        if left.button("立即刷新", use_container_width=True):
            st.rerun()
        if right.button("中断任务", use_container_width=True):
            try:
                client.put(f"/api/ai/problem-tasks/{task_id}/cancel")
            except APIClientError as exc:
                show_error(exc)
            else:
                st.session_state.flash = "智能命题任务已中断"
                st.rerun()
    elif status == "failed":
        st.error(task.get("error_info") or "智能命题失败")
        _render_partial_output(task.get("partial_output"))
    elif status == "cancelled":
        st.warning("任务已被中断，后端不会继续调用模型或执行验证。")
        _render_partial_output(task.get("partial_output"))
    if task.get("result"):
        _render_revision_workspace(client, task, problems, show_error, configured)

    if status in {"pending", "running"}:
        auto_refresh = st.checkbox("每秒自动刷新进度", value=True)
        if auto_refresh:
            time.sleep(1)
            st.rerun()


def _render_history(
    tasks: list[dict[str, Any]],
) -> None:
    with st.expander("最近的命题任务"):
        if not tasks:
            st.caption("还没有历史任务。")
            return
        rows = [
            {
                "任务 ID": task["task_id"],
                "状态": TASK_STATUS_LABELS.get(task["status"], task["status"]),
                "参考题目": task.get("problem_id") or "新题目",
                "进度": task.get("progress") or "",
                "版本": task.get("latest_revision") or "—",
                "创建时间": task["created_at"],
            }
            for task in tasks
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        selected = st.selectbox(
            "恢复查看任务",
            [task["task_id"] for task in tasks],
            key="ai_history_selection",
        )
        if st.button("查看所选任务"):
            st.session_state.active_ai_task_id = selected
            st.rerun()


def render_ai_page(
    client: OJAPIClient,
    problems: list[dict[str, str]],
    show_error: Callable[[APIClientError], None],
    current_user_id: str,
) -> None:
    st.header("AI 智能命题")
    st.caption(
        "根据知识点和难度生成完整题目；后端会运行标准解并重算样例与测试点输出。"
    )
    configured = _render_config(client, show_error)
    _render_habit_configs(client, show_error)
    try:
        tasks = client.get("/api/ai/problem-tasks/").data
    except APIClientError as exc:
        show_error(exc)
        tasks = []
    running_task = next(
        (
            task
            for task in tasks
            if task.get("user_id") == current_user_id
            and task.get("status") in {"pending", "running"}
        ),
        None,
    )
    running_task_id = running_task["task_id"] if running_task else None
    if running_task_id:
        st.session_state.active_ai_task_id = running_task_id
    _create_task(client, problems, show_error, configured, running_task_id)
    _render_history(tasks)
    _render_active_task(client, problems, show_error, configured)
