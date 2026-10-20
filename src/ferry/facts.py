from __future__ import annotations

import base64

from .config import TargetCfg
from .executors import Executor
from .models import ExecRequest, ExecStep, NodeInfo
from .probe import RemoteProbe

TARGET_FACT_SCRIPT = r"""
echo "os=$(uname -s 2>/dev/null | tr 'A-Z' 'a-z')"
echo "arch=$(uname -m 2>/dev/null)"
echo "kernel=$(uname -r 2>/dev/null)"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    [ -n "${ID:-}" ] && echo "distro=$ID"
    [ -n "${VERSION_ID:-}" ] && echo "os_ver=$VERSION_ID"
fi
if [ -d /run/systemd/system ]; then
    echo "init=systemd"
else
    comm=$(cat /proc/1/comm 2>/dev/null || true)
    case "$comm" in
        systemd) echo "init=systemd" ;;
        init) echo "init=sysvinit" ;;
        *)
            if [ -x /sbin/init ]; then echo "init=sysvinit"; else echo "init=none"; fi
            ;;
    esac
fi
if command -v ldd >/dev/null 2>&1; then
    ldd_out=$(ldd --version 2>&1 | head -n 1)
    case "$ldd_out" in
        *musl*|*Musl*) ;;
        *)
            pattern='s/.* \([0-9][0-9]*\.[0-9][0-9]*\(\.[0-9][0-9]*\)\?\).*/\1/p'
            glibc=$(printf '%s\n' "$ldd_out" | sed -n "$pattern")
            [ -n "$glibc" ] && echo "glibc=$glibc"
            ;;
    esac
fi
for tool in tar gzip rsync scp; do
    if command -v "$tool" >/dev/null 2>&1; then
        echo "has_$tool=1"
    else
        echo "has_$tool=0"
    fi
done
"""

ARCH_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv7": "arm",
    "armv8l": "arm",
    "i386": "386",
    "i486": "386",
    "i586": "386",
    "i686": "386",
    "riscv64": "riscv64",
}


class FactError(Exception):
    pass


def parse_node_info(text: str) -> NodeInfo:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    machine = values.get("arch", "")
    return NodeInfo(
        os=values.get("os", "linux"),
        arch=ARCH_ALIASES.get(machine.lower(), machine),
        kernel=values.get("kernel", ""),
        distro=values.get("distro", ""),
        os_ver=values.get("os_ver", ""),
        init=values.get("init", ""),
        glibc=values.get("glibc", ""),
        has_tar=values.get("has_tar") == "1",
        has_gzip=values.get("has_gzip") == "1",
        has_rsync=values.get("has_rsync") == "1",
        has_scp=values.get("has_scp") == "1",
    )


def collect_target_info(
    executor: Executor,
    target: TargetCfg,
    probe: RemoteProbe,
    *,
    dev_reachable: bool,
) -> NodeInfo:
    errors: list[str] = []
    if dev_reachable:
        result = executor.run(
            target.deploy.node, ["sh", "-c", TARGET_FACT_SCRIPT], timeout=30
        )
        if result.returncode == 0:
            return parse_node_info(result.stdout_text)
        errors.append(f"dev probe failed: {result.stderr_text.strip() or result.returncode}")

    request = ExecRequest(
        run_id="target-facts",
        workdir=target.build.workdir,
        target_host=target.deploy.node,
        target_key=target.build.ssh_key,
        steps=[
            ExecStep(
                id="target-facts",
                where="target",
                kind="script",
                script_b64=base64.b64encode(TARGET_FACT_SCRIPT.encode("utf-8")).decode(
                    "ascii"
                ),
                cwd="/",
                timeout_ms=30000,
            )
        ],
    )
    chunks: list[bytes] = []
    code: int | None = None
    tail = ""
    try:
        for event in probe.exec(request):
            if event.event == "out" and event.stream == "stdout" and event.chunk:
                chunks.append(base64.b64decode(event.chunk))
            elif event.event == "step_end":
                code = event.code
                tail = event.tail or ""
    except Exception as exc:
        errors.append(f"build->target probe failed: {exc}")
    if code == 0:
        return parse_node_info(b"".join(chunks).decode("utf-8", errors="replace"))
    if code is not None:
        errors.append(f"build->target probe exit {code}: {tail.strip()}")
    raise FactError(
        "cannot detect target facts; set target.env in .ferry.yaml to provide "
        f"XB_TARGET_* values manually. Details: {'; '.join(errors) or 'unknown error'}"
    )
