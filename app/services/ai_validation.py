from __future__ import annotations

import ast
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

from app.core.config import Settings
from app.models.ai import GeneratedProblemDraft
from app.models.problems import InputOutputPair, ProblemInput
from app.services.judge import normalize_output, run_process


class DraftValidationError(RuntimeError):
    """The generated reference solution cannot produce a usable problem."""


@dataclass(frozen=True, slots=True)
class ValidatedDraft:
    problem: ProblemInput
    validation: dict


BLOCKED_MODULES = {
    "asyncio",
    "ctypes",
    "http",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "tempfile",
    "threading",
    "urllib",
}
BLOCKED_CALLS = {"breakpoint", "compile", "eval", "exec", "open", "__import__"}
BLOCKED_ATTRIBUTES = {
    "connect",
    "fork",
    "popen",
    "remove",
    "request",
    "rmdir",
    "spawn",
    "system",
    "unlink",
    "write_bytes",
    "write_text",
}


BOUNDARY_PURPOSE_KEYWORDS = {
    "边界",
    "最小",
    "最大",
    "上界",
    "下界",
    "上限",
    "下限",
    "极端",
    "临界",
    "零",
    "空",
    "单个",
    "boundary",
    "upper bound",
    "lower bound",
    "min",
    "max",
    "minimum",
    "maximum",
    "limit",
    "zero",
    "empty",
    "single",
    "edge",
}
STRESS_PURPOSE_KEYWORDS = {
    "大规模",
    "性能",
    "复杂度",
    "压力",
    "超时",
    "上界",
    "上限",
    "最大",
    "极大数据",
    "极限",
    "large",
    "stress",
    "performance",
    "complexity",
    "timeout",
    "maximum",
    "max",
    "upper bound",
    "limit",
}


def _validate_python_source(code: str, label: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise DraftValidationError(f"{label} is not valid Python 3") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name.split(".", 1)[0] in BLOCKED_MODULES for name in names):
                raise DraftValidationError(f"{label} imports a blocked module")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALLS:
                raise DraftValidationError(f"{label} uses a blocked operation")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in BLOCKED_ATTRIBUTES:
                raise DraftValidationError(f"{label} uses a blocked operation")
    return ast.dump(tree, include_attributes=False)


def _canonical_input(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def _normalized_purpose(value: str) -> str:
    return " ".join(value.casefold().split())


def _purpose_matches(purpose: str, keywords: set[str]) -> bool:
    normalized = _normalized_purpose(purpose)
    return any(keyword in normalized for keyword in keywords)


def _validate_quality_shape(
    draft: GeneratedProblemDraft,
    reference_signature: str,
    mutant_signatures: list[str],
) -> dict:
    sample_inputs = [_canonical_input(item.input) for item in draft.problem.samples]
    testcase_inputs = [_canonical_input(item.input) for item in draft.problem.testcases]
    if len(set(sample_inputs)) != len(sample_inputs):
        raise DraftValidationError("样例输入存在重复；每个样例必须覆盖不同场景")
    if len(set(testcase_inputs)) != len(testcase_inputs):
        raise DraftValidationError("测试点输入存在重复；请替换重复输入并保留测试点数量")
    overlap = set(sample_inputs) & set(testcase_inputs)
    if overlap:
        raise DraftValidationError("测试点不得直接复用样例输入；请补充独立的隐藏测试点")

    purposes = [_normalized_purpose(item) for item in draft.testcase_purposes]
    if len(set(purposes)) != len(purposes):
        raise DraftValidationError("测试点覆盖目的存在重复；每个测试点必须说明独立的验证目标")
    if any(len(item) < 4 for item in purposes):
        raise DraftValidationError("测试点覆盖目的过于笼统；请具体说明该输入要验证的边界或缺陷")

    boundary_count = sum(
        _purpose_matches(item, BOUNDARY_PURPOSE_KEYWORDS)
        for item in draft.testcase_purposes
    )
    stress_count = sum(
        _purpose_matches(item, STRESS_PURPOSE_KEYWORDS)
        for item in draft.testcase_purposes
    )
    if boundary_count < 2:
        raise DraftValidationError("测试点必须至少包含 2 个明确标注的边界场景")
    if stress_count < 1:
        raise DraftValidationError("测试点必须包含至少 1 个大规模、性能或复杂度场景")

    input_sizes = [len(item.encode("utf-8")) for item in testcase_inputs]
    distinct_input_sizes = len(set(input_sizes))
    min_input_size = min(input_sizes)
    max_input_size = max(input_sizes)
    if distinct_input_sizes < 3 or (
        max_input_size < max(1, min_input_size) * 2
        and max_input_size - min_input_size < 4
    ):
        raise DraftValidationError(
            "测试输入规模缺少层次；请同时提供小规模、中间规模和边界/大规模输入"
        )

    if len(set(mutant_signatures)) != len(mutant_signatures):
        raise DraftValidationError("典型错误解之间存在重复；每个错误解必须代表不同缺陷")
    if reference_signature in mutant_signatures:
        raise DraftValidationError("典型错误解不得与标准解相同")

    return {
        "unique_sample_inputs": True,
        "unique_testcase_inputs": True,
        "sample_testcase_overlap": 0,
        "unique_testcase_purposes": True,
        "boundary_case_count": boundary_count,
        "stress_case_count": stress_count,
        "distinct_input_sizes": distinct_input_sizes,
        "min_input_bytes": min_input_size,
        "max_input_bytes": max_input_size,
        "independent_mutants": True,
    }


async def _write_source(path: Path, code: str) -> None:
    await asyncio.to_thread(path.write_text, code, encoding="utf-8")


async def _run_python(
    source_path: Path,
    input_text: str,
    settings: Settings,
    timeout_seconds: float,
):
    return await run_process(
        [sys.executable, str(source_path)],
        cwd=source_path.parent,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        memory_limit_mb=256,
        max_output_bytes=min(settings.max_program_output_bytes, 256_000),
    )


def _execution_failure(result) -> str | None:
    if result.timed_out:
        return "time limit exceeded"
    if result.memory_exceeded:
        return "memory limit exceeded"
    if result.output_exceeded:
        return "output limit exceeded"
    if result.returncode != 0:
        return "runtime error"
    return None


async def validate_generated_draft(
    draft: GeneratedProblemDraft,
    settings: Settings,
) -> ValidatedDraft:
    reference_signature = _validate_python_source(
        draft.reference_solution, "reference solution"
    )
    mutant_signatures = [
        _validate_python_source(solution, f"incorrect solution {index}")
        for index, solution in enumerate(draft.incorrect_solutions, start=1)
    ]
    quality_gate = _validate_quality_shape(
        draft, reference_signature, mutant_signatures
    )
    workdir = Path(
        await asyncio.to_thread(mkdtemp, prefix="ai-validation-", dir=settings.runtime_dir)
    )
    try:
        reference_path = workdir / "reference.py"
        await _write_source(reference_path, draft.reference_solution)
        reference_timeout = min(max(draft.problem.time_limit or 3.0, 0.1), 5.0)

        verified_samples: list[InputOutputPair] = []
        verified_testcases: list[InputOutputPair] = []
        combined = [
            ("sample", index, item)
            for index, item in enumerate(draft.problem.samples, start=1)
        ] + [
            ("test case", index, item)
            for index, item in enumerate(draft.problem.testcases, start=1)
        ]
        for kind, index, item in combined:
            result = await _run_python(reference_path, item.input, settings, reference_timeout)
            failure = _execution_failure(result)
            if failure is not None:
                raise DraftValidationError(f"reference solution {failure} on {kind} {index}")
            verified = InputOutputPair(input=item.input, output=normalize_output(result.stdout))
            if kind == "sample":
                verified_samples.append(verified)
            else:
                verified_testcases.append(verified)

        killed: list[int] = []
        survived: list[int] = []
        mutant_kill_cases: list[dict] = []
        effective_testcases: set[int] = set()
        mutant_timeout = min(reference_timeout, 1.0)
        for mutant_index, code in enumerate(draft.incorrect_solutions, start=1):
            mutant_path = workdir / f"incorrect_{mutant_index}.py"
            await _write_source(mutant_path, code)
            killed_by: list[int] = []
            for testcase_index, testcase in enumerate(verified_testcases, start=1):
                result = await _run_python(mutant_path, testcase.input, settings, mutant_timeout)
                failure = _execution_failure(result)
                if failure is not None or normalize_output(result.stdout) != testcase.output:
                    killed_by.append(testcase_index)
                    effective_testcases.add(testcase_index)
            mutant_kill_cases.append(
                {"mutant": mutant_index, "killed_by": killed_by}
            )
            (killed if killed_by else survived).append(mutant_index)

        if survived:
            raise DraftValidationError(
                "典型错误解未被任何测试点识别，编号："
                + ", ".join(map(str, survived))
                + "；请针对这些缺陷增加或替换边界及大规模测试点"
            )
        minimum_effective = min(2, len(verified_testcases))
        if len(effective_testcases) < minimum_effective:
            raise DraftValidationError(
                f"只有 {len(effective_testcases)} 个测试点能够识别典型错误解；"
                f"至少需要 {minimum_effective} 个彼此独立的有效测试点"
            )

        quality_gate.update(
            {
                "passed": True,
                "effective_testcase_count": len(effective_testcases),
                "effective_testcase_ratio": round(
                    len(effective_testcases) / len(verified_testcases), 4
                ),
                "mutant_kill_rate": round(len(killed) / len(mutant_signatures), 4),
            }
        )

        finalized_problem = draft.problem.model_copy(
            update={"samples": verified_samples, "testcases": verified_testcases}
        )
        return ValidatedDraft(
            problem=finalized_problem,
            validation={
                "reference_outputs_verified": True,
                "source_safety_checked": True,
                "sample_count": len(verified_samples),
                "testcase_count": len(verified_testcases),
                "testcase_purposes": draft.testcase_purposes,
                "mutants_total": len(draft.incorrect_solutions),
                "mutants_killed": len(killed),
                "surviving_mutants": survived,
                "mutant_kill_cases": mutant_kill_cases,
                "quality_gate": quality_gate,
                "warnings": [],
            },
        )
    finally:
        await asyncio.to_thread(rmtree, workdir, True)
