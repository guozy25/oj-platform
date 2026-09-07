from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PROBLEM_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
ProblemId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=PROBLEM_ID_PATTERN,
    ),
]


class InputOutputPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    output: str


class ProblemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ProblemId
    title: NonEmptyText
    description: NonEmptyText
    input_description: NonEmptyText
    output_description: NonEmptyText
    samples: list[InputOutputPair] = Field(min_length=1)
    constraints: NonEmptyText
    testcases: list[InputOutputPair] = Field(min_length=1)
    hint: str = ""
    source: str = ""
    tags: list[str] = Field(default_factory=list)
    time_limit: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    memory_limit: int = Field(default=128, gt=0)
    author: str = ""
    difficulty: str = ""
    public_cases: bool = False

    def to_api_dict(self) -> dict:
        # public_cases is managed by the Step-5 visibility endpoint, not Step 1.
        return self.model_dump(mode="json", exclude={"public_cases"})
