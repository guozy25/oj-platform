from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: str, project_root: Path) -> Path:
    configured = Path(os.getenv(name, default)).expanduser()
    return configured if configured.is_absolute() else project_root / configured


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    database_path: Path
    problems_dir: Path
    runtime_dir: Path
    session_cookie_name: str = "oj_session"
    session_ttl_seconds: int = 86_400
    session_cookie_secure: bool = False
    default_time_limit: float = 3.0
    default_memory_limit: int = 128
    compile_time_limit: float = 15.0
    max_program_output_bytes: int = 1_048_576
    initial_admin_username: str = "admin"
    initial_admin_password: str = "admintestpassword"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        root = (project_root or PROJECT_ROOT).resolve()
        return cls(
            project_root=root,
            database_path=_path_from_env("OJ_DATABASE_PATH", "data/oj.db", root),
            problems_dir=_path_from_env("OJ_PROBLEMS_DIR", "problems", root),
            runtime_dir=_path_from_env("OJ_RUNTIME_DIR", "runtime", root),
            session_cookie_name=os.getenv("OJ_SESSION_COOKIE_NAME", "oj_session"),
            session_ttl_seconds=int(os.getenv("OJ_SESSION_TTL_SECONDS", "86400")),
            session_cookie_secure=_bool_from_env("OJ_SESSION_COOKIE_SECURE", False),
            default_time_limit=float(os.getenv("OJ_DEFAULT_TIME_LIMIT", "3")),
            default_memory_limit=int(os.getenv("OJ_DEFAULT_MEMORY_LIMIT", "128")),
            compile_time_limit=float(os.getenv("OJ_COMPILE_TIME_LIMIT", "15")),
            max_program_output_bytes=int(os.getenv("OJ_MAX_PROGRAM_OUTPUT_BYTES", "1048576")),
            initial_admin_username=os.getenv("OJ_INITIAL_ADMIN_USERNAME", "admin"),
            initial_admin_password=os.getenv("OJ_INITIAL_ADMIN_PASSWORD", "admintestpassword"),
            log_level=os.getenv("OJ_LOG_LEVEL", "INFO").upper(),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.problems_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
