from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "admin", "banned"]
