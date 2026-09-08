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


def _render_config(client: OJAPIClient, show_error: Callable[[APIClientError], None]) -> bool:
    try:
        current = client.get("/api/ai/model-config").data
    except APIClientError as exc:
        show_error(exc)
        return False

    configured = bool(current.get("api_key_configured"))
    if configured:
        st.success(f"已配置模型：{current['model']} · {current['provider_url']}")
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
            first, second, third = st.columns(3)
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
            saved = st.form_submit_button("保存模型配置", type="primary")
        if saved:
            if not api_key.strip():
                st.error("请输入 API Key；出于安全考虑，后端不会回传已经保存的密钥。")
            else:
                try:
                    client.put(
                        "/api/ai/model-config",
                        json={
                            "provider_url": provider_url,
                            "model": model,
                            "api_key": api_key,
                            "input_price": input_price,
                            "output_price": output_price,
                            "price_unit": int(price_unit),
                        },
                    )
                except APIClientError as exc:
                    show_error(exc)
                else:
                    st.session_state.flash = "模型配置已保存"
                    st.rerun()
    return configured


def _create_task(
    client: OJAPIClient,
    problems: list[dict[str, str]],
    show_error: Callable[[APIClientError], None],
    configured: bool,
) -> None:
    st.subheader("命题需求")
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
            disabled=not configured,
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
            show_error(exc)
        else:
            st.session_state.active_ai_task_id = response.data["task_id"]
            st.session_state.flash = "智能命题任务已创建"
            st.rerun()


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


def _render_result(result: dict, problems: list[dict[str, str]]) -> None:
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

    metrics = st.columns(4)
    metrics[0].metric("样例", validation["sample_count"])
    metrics[1].metric("测试点", validation["testcase_count"])
    metrics[2].metric("典型错误解", validation["mutants_total"])
    metrics[3].metric("已识别错误解", validation["mutants_killed"])

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
    if st.button(f"导入到“{operation}”页面", type="primary"):
        st.session_state.ai_generated_problem = problem
        st.session_state.ai_generated_form_key = st.session_state.get(
            "active_ai_task_id", problem["id"]
        )
        st.session_state.pending_problem_operation = operation
        st.session_state.navigation = "题目"
        st.rerun()
    st.download_button(
        "下载生成结果 JSON",
        data=json.dumps(result, ensure_ascii=False, indent=2),
        file_name=f"{problem['id']}-ai-result.json",
        mime="application/json",
    )


def _render_active_task(
    client: OJAPIClient,
    problems: list[dict[str, str]],
    show_error: Callable[[APIClientError], None],
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
        auto_refresh = st.checkbox("每秒自动刷新进度", value=True)
        if auto_refresh:
            time.sleep(1)
            st.rerun()
        return

    if status == "failed":
        st.error(task.get("error_info") or "智能命题失败")
    elif status == "cancelled":
        st.warning("任务已被中断，后端不会继续调用模型或执行验证。")
    elif status == "completed" and task.get("result"):
        _render_result(task["result"], problems)


def _render_history(
    client: OJAPIClient,
    show_error: Callable[[APIClientError], None],
) -> None:
    with st.expander("最近的命题任务"):
        try:
            tasks = client.get("/api/ai/problem-tasks/").data
        except APIClientError as exc:
            show_error(exc)
            return
        if not tasks:
            st.caption("还没有历史任务。")
            return
        rows = [
            {
                "任务 ID": task["task_id"],
                "状态": TASK_STATUS_LABELS.get(task["status"], task["status"]),
                "参考题目": task.get("problem_id") or "新题目",
                "进度": task.get("progress") or "",
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
) -> None:
    st.header("AI 智能命题")
    st.caption(
        "根据知识点和难度生成完整题目；后端会运行标准解、重算输出，并用典型错误解检查测试点。"
    )
    configured = _render_config(client, show_error)
    _create_task(client, problems, show_error, configured)
    _render_history(client, show_error)
    _render_active_task(client, problems, show_error)
