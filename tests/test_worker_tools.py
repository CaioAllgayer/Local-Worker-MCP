import sys

import pytest

from local_worker.security import SecurityError
from tests.fakes import make_service, make_settings
from local_worker.security import SecurityPolicy
from local_worker.workers.fs_tools import WorkerFS


def test_search_and_list(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    service = make_service(tmp_path)
    listed = service.fs.list_directory(str(tmp_path))
    assert listed["count"] >= 1
    found = service.fs.search_files(str(tmp_path), "def foo")
    assert found["matches"]
    assert found["matches"][0]["line"] == 1


def test_shell_disabled(tmp_path):
    service = make_service(tmp_path, enable_shell=False)
    with pytest.raises(SecurityError):
        service.fs.run_command("echo hi")


def test_command_timeout_and_output_limit(tmp_path):
    settings = make_settings(
        tmp_path,
        security_mode="WORKSPACE_WRITE",
        enable_shell=True,
        allowed_commands=["python", sys.executable],
        command_timeout_seconds=0.2,
        command_output_limit=20,
    )
    fs = WorkerFS(settings, SecurityPolicy(settings))
    with pytest.raises(TimeoutError):
        fs.run_command(f'"{sys.executable}" -c "import time; time.sleep(2)"', cwd=str(tmp_path))

    settings.command_timeout_seconds = 10
    result = fs.run_command(f'"{sys.executable}" -c "print(\'x\' * 100)"', cwd=str(tmp_path))
    assert result["truncated"] is True
    assert len(result["stdout"]) <= 20
