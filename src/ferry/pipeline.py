from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .auth import Credentials
from .config import (
    LoadedConfig,
    ResolvedScript,
    TargetCfg,
    first_component,
    resolve_stage_scripts,
)
from .executors import Executor, SshExecutor
from .models import ExecEvent, ExecRequest, ExecStep, MarkerSpec, NodeInfo
from .paths import identity_path
from .probe import ProbeError, RemoteProbe, ensure_remote_probe
from .state import (
    RunState,
    StageState,
    git_info,
    load_state,
    log_path,
    new_run_id,
    save_state,
)
from .topology import Route, RoutePolicy, TopologyError, probe_edges
from .transport import (
    artifact_push_command,
    effective_ignores,
    push_stream_to_target,
    run_target_script,
    upload_tree,
)


class PipelineError(Exception):
    pass


@dataclass
class PlannedStep:
    stage: str
    where: str
    kind: str
    description: str
    script: ResolvedScript | None = None
    command: str | None = None


@dataclass
class PipelineContext:
    loaded: LoadedConfig
    target_name: str
    target: TargetCfg
    executor: Executor
    probe: RemoteProbe
    info: NodeInfo
    env: dict[str, str]
    route: Route
    run_id: str
    log_file: Path
    lock_path: str
    marker_path: str
    clean_paths: list[str]
    toolchain_version: str | None = None
    target_password: str | None = None

    @property
    def project(self) -> str:
        return self.loaded.config.project.name or self.loaded.root.name

    @property
    def remote_out(self) -> str:
        return posix_join(self.target.build.workdir, self.target.out)


def posix_join(base: str, relative: str) -> str:
    return base.rstrip("/") + "/" + relative.lstrip("/").replace("\\", "/")


def check_toolchain_version(
    probe: RemoteProbe,
    node: str,
    workdir: str,
    command: str,
    expect: str | None,
) -> str | None:
    request = ExecRequest(
        run_id="toolchain-check",
        workdir=workdir,
        steps=[
            ExecStep(
                id="toolchain",
                where="build",
                argv=["sh", "-c", command],
                cwd=workdir,
            )
        ],
    )
    chunks: list[bytes] = []
    code: int | None = None
    for event in probe.exec(request):
        if event.event == "out" and event.stream == "stdout" and event.chunk:
            chunks.append(base64.b64decode(event.chunk))
        if event.event == "step_end":
            code = event.code
    if code not in (0, None):
        raise PipelineError(f"toolchain probe failed on {node} (exit code {code}): {command}")
    value = b"".join(chunks).decode("utf-8", errors="replace").strip()
    value = value.splitlines()[-1].strip() if value else ""
    if expect is not None and value != expect:
        raise PipelineError(
            f"toolchain mismatch on {node}: expected {expect!r}, got {value!r}"
        )
    return value or None


class RunLog:
    def __init__(self, path: Path, console: Console) -> None:
        self.path = path
        self.console = console
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    def line(self, text: str = "", *, style: str | None = None) -> None:
        self._handle.write(text + "\n")
        self._handle.flush()
        self.console.print(text, style=style, highlight=False, markup=False)

    def close(self) -> None:
        self._handle.close()


class Pipeline:
    def __init__(
        self,
        loaded: LoadedConfig,
        target_name: str,
        *,
        executor: Executor | None = None,
        console: Console | None = None,
        credentials: Credentials | None = None,
    ) -> None:
        self.loaded = loaded
        self.target_name = target_name
        self.console = console or Console()
        self.target = self._target(target_name)
        self.project = loaded.config.project.name or loaded.root.name
        self.credentials = credentials or Credentials()
        if executor is not None:
            self.executor = executor
        else:
            passwords: dict[str, str] = {}
            if self.credentials.build_password:
                passwords[self.target.build.node] = self.credentials.build_password
            if self.credentials.target_password:
                passwords[self.target.deploy.node] = self.credentials.target_password
            identity = str(identity_path()) if identity_path().is_file() else None
            self.executor = SshExecutor(passwords=passwords, identity=identity)

    def _target(self, name: str) -> TargetCfg:
        try:
            return self.loaded.config.targets[name]
        except KeyError:
            known = ", ".join(sorted(self.loaded.config.targets)) or "(none)"
            raise PipelineError(
                f"unknown target {name!r}; configured targets: {known}"
            ) from None

    def prepare(
        self, *, refresh_topology: bool = False, reset_probe: bool = False
    ) -> PipelineContext:
        target = self.target
        prefer_via_dev = bool(self.credentials.target_password)
        try:
            edges = probe_edges(
                self.executor,
                target.build.node,
                target.deploy.node,
                project=self.project,
                target=self.target_name,
                refresh=refresh_topology,
                skip_build_target=prefer_via_dev,
                build_key=target.build.ssh_key,
            )
        except Exception as exc:
            raise PipelineError(f"topology probing failed: {exc}") from exc
        try:
            route = RoutePolicy().decide(edges, prefer_via_dev=prefer_via_dev)
        except TopologyError as exc:
            raise PipelineError(str(exc)) from exc

        probe = ensure_remote_probe(
            self.executor,
            target.build.node,
            target.build.workdir,
            reset=reset_probe,
        )
        info = probe.info()
        toolchain_version = self._check_toolchain(probe)
        env = build_environment(self.project, self.target_name, info, toolchain_version)
        run_id = new_run_id()
        workdir = target.build.workdir.rstrip("/")
        clean = target.build.clean or [first_component(target.out)]
        return PipelineContext(
            loaded=self.loaded,
            target_name=self.target_name,
            target=target,
            executor=self.executor,
            probe=probe,
            info=info,
            env=env,
            route=route,
            run_id=run_id,
            log_file=log_path(self.project, self.target_name, run_id),
            lock_path=f"{workdir}/.ferry/{self.target_name}.lock",
            marker_path=f"{workdir}/.ferry/{self.target_name}.marker",
            clean_paths=[posix_join(workdir, item) for item in clean],
            toolchain_version=toolchain_version,
            target_password=self.credentials.target_password,
        )

    def _check_toolchain(self, probe: RemoteProbe) -> str | None:
        toolchain = self.target.toolchain
        if not toolchain.probe:
            return None
        return check_toolchain_version(
            probe,
            self.target.build.node,
            self.target.build.workdir,
            toolchain.probe,
            toolchain.expect,
        )

    def build_steps(self, *, via_dev: bool = False) -> list[PlannedStep]:
        target = self.target
        steps: list[PlannedStep] = []

        def add_scripts(stage: str) -> None:
            for script in resolve_stage_scripts(target, stage):
                steps.append(script_step(stage, script))

        add_scripts("pre_sync")
        steps.append(
            PlannedStep(
                stage="sync",
                where="build",
                kind="sync",
                description="sync workspace to build",
            )
        )
        add_scripts("post_sync")
        add_scripts("pre_build")
        steps.append(
            PlannedStep(
                stage="build",
                where="build",
                kind="build",
                description=target.build_cmd,
                command=target.build_cmd,
            )
        )
        add_scripts("post_build")
        add_scripts("pre_deploy")
        if via_dev:
            steps.append(
                PlannedStep(
                    stage="deploy",
                    where="dev",
                    kind="deploy_via_dev",
                    description=(
                        f"pull {target.out} from build and push to "
                        f"{target.deploy.node}:{target.deploy.path}"
                    ),
                )
            )
        else:
            steps.append(
                PlannedStep(
                    stage="deploy",
                    where="build",
                    kind="transfer",
                    description=(
                        f"push {target.out} to {target.deploy.node}:{target.deploy.path}"
                    ),
                    command=artifact_push_command(
                        posix_join(target.build.workdir, target.out),
                        target.deploy.node,
                        target.deploy.path,
                        key=target.build.ssh_key,
                    ),
                )
            )
        add_scripts("post_deploy")
        return steps

    def execute(
        self, ctx: PipelineContext, steps: list[PlannedStep], *, dry_run: bool = False
    ) -> RunState:
        commit, dirty = git_info(self.loaded.root)
        state = RunState(
            project=self.project,
            target=self.target_name,
            run_id=ctx.run_id,
            commit=commit,
            dirty=dirty,
            toolchain=ctx.toolchain_version,
            topology=ctx.route.name,
            log=str(ctx.log_file),
            build_node=self.target.build.node,
        )
        previous = load_state(self.project, self.target_name)
        log = RunLog(ctx.log_file, self.console)
        try:
            self._warn_drift(previous, ctx, log)
            if dry_run:
                render_plan(self.console, ctx, steps)
                return state
            log.line(f"run {ctx.run_id}  target={self.target_name}  route={ctx.route.name}")
            save_state(state)
            try:
                lock_pid = ctx.probe.acquire_lock(ctx.lock_path)
            except ProbeError as exc:
                raise PipelineError(str(exc)) from exc
            state.lock_pid = lock_pid
            save_state(state)
            handler = StepExecutor(self, ctx, state, log)
            try:
                try:
                    handler.run(steps)
                except KeyboardInterrupt:
                    ctx.probe.terminate()
                    handler.mark_interrupted()
                    log.line(
                        "interrupted by user; build marker retained so the next run "
                        "rebuilds clean",
                        style="red",
                    )
                    raise PipelineError("interrupted by user") from None
            finally:
                ctx.probe.release_lock()
                state.lock_pid = None
                state.probe_pid = None
                save_state(state)
            return state
        finally:
            log.close()

    def _warn_drift(self, previous: RunState | None, ctx: PipelineContext, log: RunLog) -> None:
        if previous is None:
            return
        if (
            previous.toolchain
            and ctx.toolchain_version
            and previous.toolchain != ctx.toolchain_version
        ):
            log.line(
                f"warning: toolchain changed since last run "
                f"({previous.toolchain} -> {ctx.toolchain_version}); consider 'ferry up'",
                style="yellow",
            )


def script_step(stage: str, script: ResolvedScript) -> PlannedStep:
    location = script.where
    return PlannedStep(
        stage=stage,
        where=location,
        kind=f"{location}_script",
        description=f"{script.path} on {location}",
        script=script,
    )


class StepExecutor:
    def __init__(
        self, pipeline: Pipeline, ctx: PipelineContext, state: RunState, log: RunLog
    ) -> None:
        self.pipeline = pipeline
        self.ctx = ctx
        self.state = state
        self.log = log
        self.end_code: int | None = None
        self.interrupted = False

    def run(self, steps: list[PlannedStep]) -> None:
        for group_name, group in self._group(steps):
            if group[0].kind == "sync":
                self._run_sync(group[0])
                continue
            if group[0].kind == "deploy_via_dev":
                self._deploy_via_dev(group[0])
                continue
            batch: list[PlannedStep] = []
            for step in group:
                if step.where == "dev":
                    if batch:
                        self._run_remote(group_name, batch)
                        batch = []
                    self._run_local(step)
                elif step.where == "target" and self.ctx.route.uses_dev_relay:
                    if batch:
                        self._run_remote(group_name, batch)
                        batch = []
                    self._run_target_via_dev(step)
                else:
                    batch.append(step)
            if batch:
                self._run_remote(group_name, batch)

    def _group(self, steps: list[PlannedStep]) -> list[tuple[str, list[PlannedStep]]]:
        build_phase = {"pre_build", "build", "post_build"}
        groups: list[tuple[str, list[PlannedStep]]] = []
        key: str | None = None
        current: list[PlannedStep] = []
        for step in steps:
            step_key = "build-phase" if step.stage in build_phase else step.stage
            if current and step_key != key:
                groups.append((key or step.stage, current))
                current = []
            key = step_key
            current.append(step)
        if current:
            groups.append((key or current[0].stage, current))
        return groups

    def _run_sync(self, step: PlannedStep) -> None:
        ctx = self.ctx
        ignores = effective_ignores(ctx.target.sync.ignore, ctx.target.out)
        self._stage_started(step.stage)
        self.log.line(
            f"[sync] uploading {self.pipeline.loaded.root} -> "
            f"{ctx.target.build.node}:{ctx.target.build.workdir}"
        )
        count, summary = upload_tree(
            ctx.executor,
            ctx.target.build.node,
            ctx.probe.path,
            ctx.target.build.workdir,
            self.pipeline.loaded.root,
            ignores,
        )
        self.log.line(f"[sync] {count} entries ({summary})")
        self._mark("sync", "ok")

    def _run_local(self, step: PlannedStep) -> None:
        script = step.script
        assert script is not None
        path = self.pipeline.loaded.root / script.path
        if not path.is_file():
            raise PipelineError(f"script not found: {script.path}")
        self._stage_started(step.stage)
        self.log.line(f"[{step.stage}] dev: sh {script.path}")
        env = dict(os.environ)
        env.update(self.ctx.env)
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            result_path = handle.name
        env["FERRY_RESULT_FILE"] = result_path
        try:
            completed = subprocess.run(
                ["sh", str(path)],
                cwd=self.pipeline.loaded.root,
                env=env,
                capture_output=True,
                text=True,
            )
            for line in completed.stdout.splitlines():
                self.log.line(line)
            for line in completed.stderr.splitlines():
                self.log.line(line)
            result = read_local_script_result(result_path)
            if completed.returncode != 0:
                self._fail(step, completed.returncode, completed.stderr[-2000:], result)
            if result:
                self.log.line(f"[{step.stage}] result: {result}")
            self._mark(step.stage, "ok")
        finally:
            try:
                os.unlink(result_path)
            except OSError:
                pass

    def _run_remote(self, group_name: str, steps: list[PlannedStep]) -> None:
        ctx = self.ctx
        target = ctx.target
        is_build_phase = group_name == "build-phase"
        exec_steps = [self._exec_step(step) for step in steps]
        request = ExecRequest(
            run_id=ctx.run_id,
            workdir=target.build.workdir,
            target_host=target.deploy.node,
            target_key=target.build.ssh_key,
            steps=exec_steps,
        )
        if is_build_phase:
            request.marker = MarkerSpec(path=ctx.marker_path)
            request.clean_paths = ctx.clean_paths

        self._stage_started(steps[0].stage)
        executed: set[str] = set()
        for event in ctx.probe.exec(request):
            self._handle_event(event, steps, executed)
        if self.interrupted or self.end_code == 3:
            self.mark_interrupted()
            raise PipelineError("run interrupted; build marker retained for the next run")
        if self.end_code not in (0, None):
            raise PipelineError(f"probe exited with code {self.end_code}")
        if len(executed) < len(steps):
            raise PipelineError("probe stopped before all steps completed")

    def _run_target_via_dev(self, step: PlannedStep) -> None:
        script = step.script
        assert script is not None
        path = self.pipeline.loaded.root / script.path
        if not path.is_file():
            raise PipelineError(f"script not found: {script.path}")
        self._stage_started(step.stage)
        target = self.ctx.target
        self.log.line(f"[{step.stage}] target via dev: sh {script.path}")
        outcome = run_target_script(
            self.ctx.executor,
            target.deploy.node,
            path.read_bytes(),
            target.deploy.path,
            self.ctx.env,
        )
        for line in outcome.stdout.splitlines():
            self.log.line(f"  {line}")
        for line in outcome.stderr.splitlines():
            self.log.line(f"  {line}")
        if outcome.result:
            self.log.line(f"[{step.stage}] result: {outcome.result}")
        if outcome.code != 0:
            self._mark(step.stage, "failed")
            raise PipelineError(
                f"step failed: {script.path} on target (exit code {outcome.code})"
            )
        self._mark(step.stage, "ok")
        self.log.line(f"[{step.stage}] ok")

    def _deploy_via_dev(self, step: PlannedStep) -> None:
        target = self.ctx.target
        self._stage_started("deploy")
        self.log.line(
            f"[deploy] relaying {self.ctx.remote_out} from build through dev "
            f"-> {target.deploy.node}:{target.deploy.path}"
        )
        try:
            chunks = self.ctx.probe.get_stream(self.ctx.remote_out)
            push_stream_to_target(
                self.ctx.executor,
                target.deploy.node,
                target.deploy.path,
                chunks,
            )
        except Exception as exc:
            self._mark("deploy", "failed")
            raise PipelineError(f"artifact relay failed: {exc}") from exc
        self._mark("deploy", "ok")
        self.log.line("[deploy] ok")

    def _exec_step(self, step: PlannedStep) -> ExecStep:
        ctx = self.ctx
        target = ctx.target
        if step.where == "build":
            env = dict(ctx.env)
            result = False
            if step.kind == "build_script":
                assert step.script is not None
                argv = ["sh", posix_join(target.build.workdir, step.script.path)]
                result = True
            elif step.kind in ("build", "transfer"):
                assert step.command is not None
                argv = ["sh", "-c", step.command]
            else:
                raise PipelineError(f"unsupported build step kind: {step.kind}")
            return ExecStep(
                id=self._exec_step_id(step),
                where="build",
                kind="shell",
                argv=argv,
                cwd=target.build.workdir,
                env=env,
                result=result,
            )
        script = step.script
        assert script is not None
        script_path = self.pipeline.loaded.root / script.path
        if not script_path.is_file():
            raise PipelineError(f"script not found: {script.path}")
        return ExecStep(
            id=self._exec_step_id(step),
            where="target",
            kind="script",
            script_b64=base64.b64encode(script_path.read_bytes()).decode("ascii"),
            cwd=target.deploy.path,
            env=dict(ctx.env),
            target_host=target.deploy.node,
            result=True,
        )

    def _handle_event(
        self, event: ExecEvent, steps: list[PlannedStep], executed: set[str]
    ) -> None:
        if event.event == "start":
            if event.pid:
                self.state.probe_pid = event.pid
                save_state(self.state)
        elif event.event == "end":
            self.end_code = event.code
            if event.code == 3:
                self.interrupted = True
        elif event.event == "lock" and event.code == 5:
            raise PipelineError(
                "another ferry run holds the build lock for this target; "
                "wait for it to finish or cancel it explicitly"
            )
        elif event.event == "error":
            raise PipelineError(f"probe error: {event.message or 'unknown error'}")
        elif event.event == "recovery":
            cleaned = event.cleaned or []
            self.log.line(
                f"[recovery] previous build was interrupted; cleaned: "
                f"{', '.join(cleaned) or 'nothing'}",
                style="yellow",
            )
        elif event.event == "step_start" and event.id:
            step = self._lookup(event.id, steps)
            self._stage_started(step.stage)
            self.log.line(f"[{step.stage}] {step.where}: {step.description}")
        elif event.event == "out" and event.chunk:
            text = base64.b64decode(event.chunk).decode("utf-8", errors="replace")
            self.log.line(f"  {text}")
        elif event.event == "step_end" and event.id:
            step = self._lookup(event.id, steps)
            executed.add(event.id)
            duration = (event.duration_ms or 0) / 1000
            if event.code not in (0, None):
                detail = f"exit code {event.code}"
                if event.result and event.result.state == "value":
                    detail += f", result={event.result.text}"
                if event.tail:
                    detail += f"\nlast output:\n{event.tail}"
                status = "interrupted" if self.interrupted or event.code == 130 else "failed"
                self._mark(step.stage, status)
                self.log.line(f"[{step.stage}] FAILED: {detail}", style="red")
                raise PipelineError(
                    f"step failed: {step.description} on {step.where} "
                    f"(exit code {event.code})"
                )
            self._mark(step.stage, "ok")
            if event.result and event.result.state != "unset":
                label: str = event.result.state
                if event.result.state == "value":
                    label = event.result.text
                self.log.line(f"[{step.stage}] result: {label} ({duration:.1f}s)")
            else:
                self.log.line(f"[{step.stage}] ok ({duration:.1f}s)")

    def _lookup(self, step_id: str, steps: list[PlannedStep]) -> PlannedStep:
        for step in steps:
            if self._exec_step_id(step) == step_id:
                return step
        return steps[0]

    def _exec_step_id(self, step: PlannedStep) -> str:
        if step.where == "build":
            return f"{step.stage}:{step.kind}:{step.description}"
        assert step.script is not None
        return f"{step.stage}:{step.script.path}"

    def _stage_started(self, stage: str) -> None:
        self.state.stages[stage] = StageState(state="running", since=None)
        save_state(self.state)

    def _mark(self, stage: str, status: str) -> None:
        self.state.stages[stage] = StageState(state=status)  # type: ignore[arg-type]
        save_state(self.state)

    def mark_interrupted(self) -> None:
        for stage, stage_state in list(self.state.stages.items()):
            if stage_state.state == "running":
                self.state.stages[stage] = StageState(state="interrupted")
        save_state(self.state)

    def _fail(self, step: PlannedStep, code: int, tail: str, result: str | None) -> None:
        self._mark(step.stage, "failed")
        detail = f"exit code {code}"
        if result:
            detail += f", result={result}"
        if tail:
            detail += f"\nlast output:\n{tail}"
        self.log.line(f"[{step.stage}] FAILED: {detail}", style="red")
        raise PipelineError(f"step failed: {step.description} on {step.where} (exit code {code})")


def read_local_script_result(path: str) -> str | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data:
        return "(empty)"
    text = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    if len(text) > 512:
        text = text[:512] + " (truncated)"
    return text


def build_environment(
    project: str,
    target_name: str,
    info: NodeInfo,
    toolchain_version: str | None,
) -> dict[str, str]:
    env = {
        "XB_PROJECT": project,
        "XB_TARGET": target_name,
        "XB_TARGET_ARCH": info.arch,
        "XB_TARGET_OS": info.distro or info.os,
        "XB_TARGET_OS_VER": info.os_ver,
        "XB_TARGET_INIT": info.init,
        "XB_TARGET_GLIBC": info.glibc,
        "XB_TARGET_HAS_RSYNC": "1" if info.has_rsync else "0",
        "XB_TARGET_HAS_TAR": "1" if info.has_tar else "0",
        "XB_TARGET_HAS_SCP": "1" if info.has_scp else "0",
    }
    if toolchain_version:
        env["XB_BUILD_TOOLCHAIN_VER"] = toolchain_version
    return {key: value for key, value in env.items() if value}


def render_plan(console: Console, ctx: PipelineContext, steps: list[PlannedStep]) -> None:
    console.print(f"target: {ctx.target_name}")
    console.print(f"route:  {ctx.route.name} ({ctx.route.description})")
    console.print(
        f"nodes:  dev -> build={ctx.target.build.node} -> target={ctx.target.deploy.node}"
    )
    console.print("env:")
    for key, value in sorted(ctx.env.items()):
        console.print(f"  {key}={value}")
    console.print("steps:")
    for index, step in enumerate(steps, start=1):
        console.print(f"  {index:2d}. [{step.stage}] {step.where}: {step.description}")


__all__ = [
    "Pipeline",
    "PipelineContext",
    "PipelineError",
    "PlannedStep",
    "build_environment",
    "posix_join",
    "render_plan",
]
