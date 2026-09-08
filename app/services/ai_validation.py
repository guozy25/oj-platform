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


def _validate_python_source(code: str, label: str) -> None:
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
    _validate_python_source(draft.reference_solution, "reference solution")
    for index, solution in enumerate(draft.incorrect_solutions, start=1):
        _validate_python_source(solution, f"incorrect solution {index}")
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
        mutant_timeout = min(reference_timeout, 1.0)
        for mutant_index, code in enumerate(draft.incorrect_solutions, start=1):
            mutant_path = workdir / f"incorrect_{mutant_index}.py"
            await _write_source(mutant_path, code)
            is_killed = False
            for testcase in verified_testcases:
                result = await _run_python(mutant_path, testcase.input, settings, mutant_timeout)
                failure = _execution_failure(result)
                if failure is not None or normalize_output(result.stdout) != testcase.output:
                    is_killed = True
                    break
            (killed if is_killed else survived).append(mutant_index)

        duplicate_inputs = len({case.input for case in verified_testcases}) != len(
            verified_testcases
        )
        warnings = []
        if survived:
            warnings.append(
                "部分典型错误解法未被测试点识别：" + ", ".join(map(str, survived))
            )
        if duplicate_inputs:
            warnings.append("测试点中存在重复输入")

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
                "warnings": warnings,
            },
        )
    finally:
        await asyncio.to_thread(rmtree, workdir, True)
