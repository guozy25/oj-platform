from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.problems import ProblemId, ProblemInput

NonEmptyLimitedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
]
HabitConfigName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_url: str = Field(min_length=1, max_length=2_000)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr
    input_price: float = Field(default=0, ge=0, le=1_000_000, allow_inf_nan=False)
    output_price: float = Field(default=0, ge=0, le=1_000_000, allow_inf_nan=False)
    price_unit: int = Field(default=1_000_000, gt=0, le=1_000_000_000)

    @field_validator("provider_url")
    @classmethod
    def validate_provider_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("provider_url must be a complete HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider_url must not contain credentials, query, or fragments")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be empty")
        return SecretStr(value.get_secret_value().strip())

    def public_dict(self) -> dict:
        return {
            "provider_url": self.provider_url,
            "model": self.model,
            "api_key_configured": True,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "price_unit": self.price_unit,
        }


class HabitConfigCreate(ModelConfigUpdate):
    name: HabitConfigName


class HabitConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: HabitConfigName
    provider_url: str = Field(min_length=1, max_length=2_000)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    input_price: float = Field(default=0, ge=0, le=1_000_000, allow_inf_nan=False)
    output_price: float = Field(default=0, ge=0, le=1_000_000, allow_inf_nan=False)
    price_unit: int = Field(default=1_000_000, gt=0, le=1_000_000_000)

    @field_validator("provider_url")
    @classmethod
    def validate_provider_url(cls, value: str) -> str:
        return ModelConfigUpdate.validate_provider_url(value)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def validate_optional_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be empty when provided")
        return SecretStr(value.get_secret_value().strip())


class ProblemTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: NonEmptyLimitedText
    problem_id: ProblemId | None = None


class ProblemTaskRefinement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: NonEmptyLimitedText
    base_revision: int | None = Field(default=None, gt=0)


class GeneratedProblemDraft(BaseModel):
    """Strict model-provider output used before runtime validation."""

    model_config = ConfigDict(extra="forbid")

    problem: ProblemInput
    reference_solution: str = Field(min_length=1, max_length=100_000)
    incorrect_solutions: list[str] = Field(min_length=2, max_length=4)
    testcase_purposes: list[str] = Field(min_length=8, max_length=20)

    @model_validator(mode="after")
    def validate_quality_shape(self) -> GeneratedProblemDraft:
        testcase_count = len(self.problem.testcases)
        if not 8 <= testcase_count <= 20:
            raise ValueError("AI problem must contain between 8 and 20 test cases")
        if len(self.problem.samples) > 3:
            raise ValueError("AI problem must contain at most 3 samples")
        if len(self.testcase_purposes) != testcase_count:
            raise ValueError("testcase_purposes must align with testcases")
        if any(not purpose.strip() for purpose in self.testcase_purposes):
            raise ValueError("testcase purposes must not be empty")
        if any(not solution.strip() for solution in self.incorrect_solutions):
            raise ValueError("incorrect solutions must not be empty")
        if self.problem.time_limit is None or self.problem.memory_limit is None:
            raise ValueError("AI problem must define time and memory limits")
        if not self.problem.difficulty.strip() or not self.problem.tags:
            raise ValueError("AI problem must define difficulty and tags")
        return self
