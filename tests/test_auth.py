from __future__ import annotations

from ferry.auth import Credentials, resolve_credentials


def test_shared_password(monkeypatch) -> None:
    monkeypatch.setenv("FERRY_SSH_PASSWORD", "secret")
    credentials = resolve_credentials()
    assert credentials == Credentials("secret", "secret")
    assert credentials.has_any


def test_role_specific_passwords(monkeypatch) -> None:
    monkeypatch.setenv("FERRY_BUILD_PASSWORD", "b")
    monkeypatch.setenv("FERRY_TARGET_PASSWORD", "t")
    credentials = resolve_credentials()
    assert credentials.build_password == "b"
    assert credentials.target_password == "t"
    assert credentials.for_host("target", "build", "target") == "t"
    assert credentials.for_host("build", "build", "target") == "b"


def test_no_passwords(monkeypatch) -> None:
    monkeypatch.delenv("FERRY_SSH_PASSWORD", raising=False)
    monkeypatch.delenv("FERRY_BUILD_PASSWORD", raising=False)
    monkeypatch.delenv("FERRY_TARGET_PASSWORD", raising=False)
    credentials = resolve_credentials(ask=False)
    assert not credentials.has_any


def test_prompts_can_be_disabled_per_role(monkeypatch) -> None:
    monkeypatch.delenv("FERRY_SSH_PASSWORD", raising=False)
    monkeypatch.delenv("FERRY_BUILD_PASSWORD", raising=False)
    monkeypatch.delenv("FERRY_TARGET_PASSWORD", raising=False)

    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("should not prompt")

    monkeypatch.setattr("ferry.auth.getpass.getpass", fail)
    credentials = resolve_credentials(
        ask=True, need_build=False, need_target=False
    )
    assert not credentials.has_any
