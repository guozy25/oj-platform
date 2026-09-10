from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.core.config import Settings

logger = logging.getLogger(__name__)

SANDBOX_WORKING_DIRECTORY = Path("/workspace")
DEFAULT_SYSTEM_PATHS = (
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/lib"),
    Path("/lib64"),
)
DEFAULT_ETC_PATHS = (
    Path("/etc/alternatives"),
    Path("/etc/ld.so.cache"),
    Path("/etc/ld.so.conf"),
    Path("/etc/ld.so.conf.d"),
    Path("/etc/localtime"),
)


class SandboxUnavailableError(RuntimeError):
    """Raised when secure judge isolation cannot be established."""


def resolve_sandbox_executable(configured: str) -> Path:
    candidate = Path(configured)
    if candidate.is_absolute():
        resolved = candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    else:
        located = shutil.which(configured)
        resolved = Path(located) if located is not None else None
    if resolved is None:
        raise SandboxUnavailableError(
            f"judge sandbox executable is unavailable: {configured}"
        )
    return resolved.resolve()


def _mount_arguments(source: Path) -> list[str]:
    return ["--ro-bind", str(source), str(source)]


def _rewrite_workspace_argument(argument: str, working_directory: Path) -> str:
    host_directory = str(working_directory.resolve())
    if argument == host_directory:
        return str(SANDBOX_WORKING_DIRECTORY)
    prefix = f"{host_directory}{os.sep}"
    if argument.startswith(prefix):
        relative = argument[len(prefix) :]
        return str(SANDBOX_WORKING_DIRECTORY / relative)
    return argument


def build_bubblewrap_command(
    settings: Settings,
    command: list[str],
    working_directory: Path,
    *,
    executable: Path | None = None,
) -> list[str]:
    working_directory = working_directory.resolve()
    sandbox_executable = executable or resolve_sandbox_executable(
        settings.sandbox_executable
    )
    wrapped = [
        str(sandbox_executable),
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--hostname",
        "oj-sandbox",
    ]
    for path in (*DEFAULT_SYSTEM_PATHS, *settings.sandbox_read_only_paths):
        if path.exists():
            wrapped.extend(_mount_arguments(path))
    wrapped.extend(["--dir", "/etc"])
    for path in DEFAULT_ETC_PATHS:
        if path.exists():
            wrapped.extend(_mount_arguments(path))
    wrapped.extend(
        [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--size",
            str(settings.sandbox_tmpfs_size_mb * 1024 * 1024),
            "--tmpfs",
            "/tmp",
            "--dir",
            str(SANDBOX_WORKING_DIRECTORY),
            "--bind",
            str(working_directory),
            str(SANDBOX_WORKING_DIRECTORY),
            "--chdir",
            str(SANDBOX_WORKING_DIRECTORY),
            "--",
        ]
    )
    wrapped.extend(
        _rewrite_workspace_argument(argument, working_directory) for argument in command
    )
    return wrapped


def verify_judge_sandbox(settings: Settings) -> None:
    if not settings.sandbox_enabled:
        logger.critical(
            "Judge sandbox is disabled; only trusted local submissions may be run"
        )
        return
    if sys.platform != "linux":
        raise SandboxUnavailableError(
            "the secure judge sandbox requires Linux; set OJ_SANDBOX_ENABLED=false "
            "only for trusted local development"
        )
    if os.geteuid() == 0:
        raise SandboxUnavailableError(
            "the judge service must run as an unprivileged operating-system account"
        )
    executable = resolve_sandbox_executable(settings.sandbox_executable)
    probe = build_bubblewrap_command(
        settings,
        ["/usr/bin/true"],
        settings.runtime_dir,
        executable=executable,
    )
    try:
        result = subprocess.run(
            probe,
            cwd=settings.runtime_dir,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailableError("judge sandbox self-test failed") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise SandboxUnavailableError(
            f"judge sandbox self-test failed: {detail or 'unknown bubblewrap error'}"
        )
