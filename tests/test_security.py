import pytest

from local_worker.security import SecurityError, SecurityPolicy
from tests.fakes import make_service, make_settings


def test_path_traversal_blocked(tmp_path):
    inside = tmp_path / "ok.txt"
    inside.write_text("safe", encoding="utf-8")
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    service = make_service(tmp_path)
    denied = service.delegate_file(str(outside), "read")
    assert denied["status"] == "error"
    assert "ALLOWED_PATHS" in denied["reason"] or "outside" in denied["reason"]


def test_empty_allowed_paths(tmp_path):
    settings = make_settings(tmp_path, allowed_paths=[])
    policy = SecurityPolicy(settings)
    with pytest.raises(SecurityError, match="ALLOWED_PATHS"):
        policy.resolve(tmp_path / "a.txt")


def test_write_and_shell_disabled_in_read_only(tmp_path):
    settings = make_settings(tmp_path, security_mode="READ_ONLY", enable_shell=True)
    policy = SecurityPolicy(settings)
    with pytest.raises(SecurityError, match="write disabled"):
        policy.resolve(tmp_path / "a.txt", write=True)
    with pytest.raises(SecurityError, match="shell is disabled"):
        policy.assert_command("echo hi")


def test_destructive_and_allowlist(tmp_path):
    settings = make_settings(
        tmp_path,
        security_mode="WORKSPACE_WRITE",
        enable_shell=True,
        allowed_commands=["echo"],
    )
    policy = SecurityPolicy(settings)
    with pytest.raises(SecurityError, match="destructive"):
        policy.assert_command("rm -rf /")
    with pytest.raises(SecurityError, match="ALLOWED_COMMANDS"):
        policy.assert_command("python -c print(1)")
    assert policy.assert_command("echo hi")[0] == "echo"


def test_full_local_without_roots(tmp_path):
    settings = make_settings(tmp_path, security_mode="FULL_LOCAL", allowed_paths=[])
    policy = SecurityPolicy(settings)
    assert policy.resolve(tmp_path / "x.txt")
