from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.config import Settings
from app.models.ai import GeneratedProblemDraft
from app.models.problems import ProblemInput


class DraftValidationError(RuntimeError):
    """The generated problem draft does not satisfy quality requirements."""


@dataclass(frozen=True, slots=True)
class ValidatedDraft:
    problem: ProblemInput
    validation: dict


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

# Large inputs are useful for stress testing, but a generated testcase made
# almost entirely from one character is usually padding rather than coverage.
REPETITIVE_INPUT_MIN_BYTES = 256
REPETITIVE_INPUT_RATIO = 0.9
MAX_GENERATED_INPUT_BYTES = 16_384


def _is_low_information_input(value: str) -> bool:
    encoded = value.encode("utf-8")
    if len(encoded) < REPETITIVE_INPUT_MIN_BYTES:
        return False
    characters = value.replace("\n", "").replace("\r", "")
    if not characters:
        return False
    most_common_count = Counter(characters).most_common(1)[0][1]
    if most_common_count / len(characters) >= REPETITIVE_INPUT_RATIO:
        return True

    # Also catch repeated short sequences such as "1 2 3 ... 10 " that have
    # varied characters but provide no additional coverage when copied many
    # times. Check a bounded prefix to keep validation cheap.
    probe = value[:4096]
    for unit_size in range(1, min(256, len(probe) // 4) + 1):
        unit = probe[:unit_size]
        repeated = (unit * ((len(probe) + unit_size - 1) // unit_size))[: len(probe)]
        if repeated == probe:
            return True
    return False


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
    *,
    allow_sample_testcase_overlap: bool = False,
) -> dict:
    sample_inputs = [_canonical_input(item.input) for item in draft.problem.samples]
    testcase_inputs = [_canonical_input(item.input) for item in draft.problem.testcases]
    if len(set(sample_inputs)) != len(sample_inputs):
        raise DraftValidationError("样例输入存在重复；每个样例必须覆盖不同场景")
    if len(set(testcase_inputs)) != len(testcase_inputs):
        raise DraftValidationError("测试点输入存在重复；请替换重复输入并保留测试点数量")
    overlap = set(sample_inputs) & set(testcase_inputs)
    if overlap and not allow_sample_testcase_overlap:
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
    if max(input_sizes, default=0) > MAX_GENERATED_INPUT_BYTES:
        raise DraftValidationError(
            "单个测试点 input 超过 16384 字节；请缩小输入并保留有代表性的测试数据"
        )
    repetitive_inputs = [
        index
        for index, item in enumerate([*sample_inputs, *testcase_inputs], start=1)
        if _is_low_information_input(item)
    ]
    if repetitive_inputs:
        raise DraftValidationError(
            "存在超长且内容高度重复的输入；请改用信息量更高、能覆盖不同边界的测试数据"
        )
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

    return {
        "unique_sample_inputs": True,
        "unique_testcase_inputs": True,
        "sample_testcase_overlap": len(overlap),
        "unique_testcase_purposes": True,
        "boundary_case_count": boundary_count,
        "stress_case_count": stress_count,
        "distinct_input_sizes": distinct_input_sizes,
        "min_input_bytes": min_input_size,
        "max_input_bytes": max_input_size,
    }


async def validate_generated_draft(
    draft: GeneratedProblemDraft,
    _settings: Settings,
    *,
    allow_sample_testcase_overlap: bool = False,
) -> ValidatedDraft:
    quality_gate = _validate_quality_shape(
        draft,
        allow_sample_testcase_overlap=allow_sample_testcase_overlap,
    )
    effective_testcases = set(range(1, len(draft.problem.testcases) + 1))
    quality_gate.update(
        {
            "passed": True,
            "effective_testcase_count": len(effective_testcases),
            "effective_testcase_ratio": 1.0,
        }
    )
    return ValidatedDraft(
        problem=draft.problem,
        validation={
            "reference_outputs_verified": False,
            "source_safety_checked": False,
            "sample_count": len(draft.problem.samples),
            "testcase_count": len(draft.problem.testcases),
            "testcase_purposes": draft.testcase_purposes,
            "quality_gate": quality_gate,
            "warnings": ["样例与测试点输出由模型提供，尚未通过标准解自动核对。"],
        },
    )
