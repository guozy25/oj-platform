from __future__ import annotations

import json
import re
from typing import Any

PROBLEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def parse_io_pairs(value: str, field_name: str) -> list[dict[str, str]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name}必须是有效的 JSON：{exc.msg}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"{field_name}必须是非空数组")
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict) or set(item) != {"input", "output"}:
            raise ValueError(f"{field_name}第 {index} 项必须仅包含 input 和 output")
        if not isinstance(item["input"], str) or not isinstance(item["output"], str):
            raise ValueError(f"{field_name}第 {index} 项的 input/output 必须是字符串")
    return parsed


def parse_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def build_problem_payload(values: dict[str, Any]) -> dict[str, Any]:
    problem_id = str(values["id"]).strip()
    if PROBLEM_ID_PATTERN.fullmatch(problem_id) is None:
        raise ValueError("题目 ID 需以字母或数字开头，仅能包含字母、数字、_ 和 -")

    required = (
        ("title", "标题"),
        ("description", "题目描述"),
        ("input_description", "输入说明"),
        ("output_description", "输出说明"),
        ("constraints", "数据范围"),
    )
    for key, label in required:
        if not str(values[key]).strip():
            raise ValueError(f"{label}不能为空")

    payload = {
        "id": problem_id,
        "title": str(values["title"]).strip(),
        "description": str(values["description"]).strip(),
        "input_description": str(values["input_description"]).strip(),
        "output_description": str(values["output_description"]).strip(),
        "samples": parse_io_pairs(str(values["samples"]), "样例"),
        "constraints": str(values["constraints"]).strip(),
        "testcases": parse_io_pairs(str(values["testcases"]), "测试点"),
        "hint": str(values.get("hint", "")),
        "source": str(values.get("source", "")),
        "tags": parse_tags(str(values.get("tags", ""))),
        "author": str(values.get("author", "")).strip(),
        "difficulty": str(values.get("difficulty", "")).strip(),
    }
    resource_fields = {"code_length_limit", "time_limit", "memory_limit"}
    if resource_fields & values.keys():
        if not resource_fields <= values.keys():
            raise ValueError("请完整填写代码长度、时间和内存限制")
        code_length_limit = int(values["code_length_limit"])
        time_limit = float(values["time_limit"])
        memory_limit = int(values["memory_limit"])
        if not 0 < code_length_limit <= 10_000_000:
            raise ValueError("代码长度限制必须在 1 到 10000000 字符之间")
        if not 0 < time_limit <= 3_600:
            raise ValueError("时间限制必须在 0 到 3600 秒之间")
        if not 0 < memory_limit <= 65_536:
            raise ValueError("内存限制必须在 1 到 65536 MB 之间")
        payload.update(
            {
                "code_length_limit": code_length_limit,
                "time_limit": time_limit,
                "memory_limit": memory_limit,
            }
        )
    return payload
