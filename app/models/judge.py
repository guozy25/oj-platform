from __future__ import annotations

import shlex
from string import Formatter
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.problems import ProblemId

LanguageName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z][A-Za-z0-9_+.-]*$",
    ),
]
FileExtension = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=16,
        pattern=r"^\.[A-Za-z0-9]+$",
    ),
]


def command_placeholders(command: str) -> set[str]:
    try:
        tokens = shlex.split(command)
        parsed = list(Formatter().parse(command))
    except ValueError as exc:
        raise ValueError("invalid command template") from exc
    if not tokens or "\x00" in command or "\n" in command or "\r" in command:
        raise ValueError("invalid command template")

    placeholders: set[str] = set()
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in {"src", "exe"} or format_spec or conversion:
            raise ValueError("only {src} and {exe} placeholders are allowed")
        placeholders.add(field_name)
    return placeholders


class LanguageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: LanguageName
    file_ext: FileExtension
    compile_cmd: str | None = Field(default=None, max_length=500)
    run_cmd: str = Field(min_length=1, max_length=500)
    time_limit: float | None = Field(default=None, gt=0, le=3_600, allow_inf_nan=False)
    memory_limit: int | None = Field(default=None, gt=0, le=65_536)

    @field_validator("name", mode="after")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.lower()

    @field_validator("compile_cmd", mode="before")
    @classmethod
    def normalize_compile_command(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("run_cmd", mode="before")
    @classmethod
    def normalize_run_command(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_command_templates(self) -> LanguageCreate:
        run_fields = command_placeholders(self.run_cmd)
        if not run_fields:
            raise ValueError("run_cmd must reference {src} or {exe}")
        if self.compile_cmd is not None:
            compile_fields = command_placeholders(self.compile_cmd)
            if "src" not in compile_fields:
                raise ValueError("compile_cmd must reference {src}")
            if not ({"src", "exe"} & run_fields):
                raise ValueError("run_cmd must reference {src} or {exe}")
        elif "src" not in run_fields:
            raise ValueError("interpreted language run_cmd must reference {src}")
        return self


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_id: ProblemId
    language: LanguageName
    code: str = Field(min_length=1, max_length=200_000)

    @field_validator("language", mode="after")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.lower()
