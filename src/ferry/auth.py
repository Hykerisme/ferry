from __future__ import annotations

import getpass
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Credentials:
    build_password: str | None = None
    target_password: str | None = None

    @property
    def has_any(self) -> bool:
        return bool(self.build_password or self.target_password)

    def for_host(self, host: str, build_host: str, target_host: str) -> str | None:
        if host == build_host:
            return self.build_password
        if host == target_host:
            return self.target_password
        return None


def resolve_credentials(
    *,
    ask: bool = False,
    need_build: bool = True,
    need_target: bool = True,
    interactive: bool = True,
) -> Credentials:
    """Resolve passwords from env vars, optionally prompting.

    Passwords exist only in memory for the duration of the command. They are
    never written to config, state or logs.
    """
    shared = os.environ.get("FERRY_SSH_PASSWORD")
    build_password = os.environ.get("FERRY_BUILD_PASSWORD") or shared
    target_password = os.environ.get("FERRY_TARGET_PASSWORD") or shared
    if ask and interactive:
        if need_build and not build_password:
            entered = getpass.getpass("Build node password (empty for key auth): ")
            build_password = entered or None
        if need_target and not target_password:
            entered = getpass.getpass("Target node password (empty for key auth): ")
            target_password = entered or None
    return Credentials(
        build_password=build_password or None,
        target_password=target_password or None,
    )
