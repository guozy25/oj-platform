from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.main import create_app
from app.services.judge import run_process
from app.services.sandbox import (
    SandboxUnavailableError,
    build_bubblewrap_command,
    verify_judge_sandbox,
)


def test_bubblewrap_command_isolates_host_and_rewrites_workspace_paths(
    test_settings, tmp_path
):
    working_directory = tmp_path / "job"
    working_directory.mkdir()
    source = working_directory / "Main.py"
    settings = replace(test_settings, sandbox_enabled=True)

    command = build_bubblewrap_command(
        settings,
        ["python3", str(source)],
        working_directory,
        executable=Path("/usr/bin/bwrap"),
    )

    separator = command.index("--")
    sandboxed_program = command[separator + 1 :]
    assert sandboxed_program == ["python3", "/workspace/Main.py"]
    for namespace_flag in (
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-cgroup",
    ):
        assert namespace_flag in command
    assert "--share-net" not in command
    assert "--clearenv" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command.count("--bind") == 1
    bind_index = command.index("--bind")
    assert command[bind_index + 1 : bind_index + 3] == [
        str(working_directory.resolve()),
        "/workspace",
    ]
    assert str(tmp_path) not in sandboxed_program


def test_missing_bubblewrap_fails_closed(test_settings, monkeypatch):
    settings = replace(
        test_settings,
        sandbox_enabled=True,
        sandbox_executable="definitely-not-a-real-bwrap-command",
    )
    monkeypatch.setattr("app.services.sandbox.sys.platform", "linux")
    monkeypatch.setattr("app.services.sandbox.shutil.which", lambda _name: None)

    with pytest.raises(SandboxUnavailableError, match="unavailable"):
        verify_judge_sandbox(settings)


def test_root_service_account_is_rejected(test_settings, monkeypatch):
    settings = replace(test_settings, sandbox_enabled=True)
    monkeypatch.setattr("app.services.sandbox.sys.platform", "linux")
    monkeypatch.setattr("app.services.sandbox.os.geteuid", lambda: 0)

    with pytest.raises(SandboxUnavailableError, match="unprivileged"):
        verify_judge_sandbox(settings)


@pytest.mark.asyncio
async def test_application_startup_refuses_missing_sandbox(test_settings, monkeypatch):
    settings = replace(
        test_settings,
        sandbox_enabled=True,
        sandbox_executable="definitely-not-a-real-bwrap-command",
    )
    monkeypatch.setattr("app.services.sandbox.sys.platform", "linux")
    monkeypatch.setattr("app.services.sandbox.shutil.which", lambda _name: None)
    application = create_app(settings)

    with pytest.raises(SandboxUnavailableError, match="unavailable"):
        async with application.router.lifespan_context(application):
            pass


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None,
    reason="a Linux bubblewrap installation is required for the isolation test",
)
async def test_real_sandbox_blocks_host_files_network_and_environment(
    test_settings, tmp_path, monkeypatch
):
    working_directory = tmp_path / "runtime" / "job"
    working_directory.mkdir(parents=True)
    secret = tmp_path / "host-secret.txt"
    secret.write_text("must-not-leak", encoding="utf-8")
    marker = tmp_path / "host-owned.txt"

    connections = 0

    async def count_connection(_reader, writer):
        nonlocal connections
        connections += 1
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(count_connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("OJ_SANDBOX_TEST_SECRET", "must-not-leak")
    source = working_directory / "Main.py"
    source.write_text(
        "\n".join(
            [
                "import os, socket",
                f"secret = {str(secret)!r}",
                f"marker = {str(marker)!r}",
                f"port = {port}",
                "try:",
                "    open(secret).read()",
                "    print('read-leaked')",
                "except OSError:",
                "    print('read-blocked')",
                "try:",
                "    open(marker, 'w').write('owned')",
                "    print('write-leaked')",
                "except OSError:",
                "    print('write-blocked')",
                "try:",
                "    socket.create_connection(('127.0.0.1', port), timeout=0.2)",
                "    print('network-leaked')",
                "except OSError:",
                "    print('network-blocked')",
                "print('env-blocked' if 'OJ_SANDBOX_TEST_SECRET' not in os.environ "
                "else 'env-leaked')",
            ]
        ),
        encoding="utf-8",
    )
    settings = replace(test_settings, sandbox_enabled=True)
    verify_judge_sandbox(settings)
    try:
        result = await run_process(
            ["python3", str(source)],
            cwd=working_directory,
            input_text="",
            timeout_seconds=2,
            memory_limit_mb=128,
            max_output_bytes=4096,
            settings=settings,
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "read-blocked",
        "write-blocked",
        "network-blocked",
        "env-blocked",
    ]
    assert not marker.exists()
    assert connections == 0
