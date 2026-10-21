from __future__ import annotations

import base64
import posixpath
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console

from .auth import Credentials
from .config import LoadedConfig, NodeConfigMissing, TargetCfg
from .envfile import load_env_file, masked_env
from .executors import (
    Executor,
    LocalExecutor,
    SshExecutor,
    resolve_remote_ferry_key,
)
from .local_probe import LocalProbe
from .models import ExecRequest, ExecStep, MarkerSpec
from .nodes import NodeSpec, ResolvedTarget, resolve_target
from .paths import identity_path
from .private_config import ensure_gitignore, regenerate_public
from .probe import Probe, ProbeError, ensure_remote_probe
from .state import (
    RunState,
    StageState,
    git_info,
    log_path,
    new_run_id,
    save_state,
)
from .topology import Route, RoutePolicy, TopologyError, probe_edges
from .transport import (
    artifact_copy_command,
    artifact_transfer_command,
    classify_artifacts_command,
    copy_artifact_local,
    copy_tree_local,
    effective_ignores,
    first_component,
    push_stream_to_target,
    run_target_script,
    upload_tree,
)

Phase = Literal[
    "before_build",
    "sync",
    "run_build",
    "after_build",
    "before_deploy",
    "transfer",
    "run_deploy",
    "after_deploy",
]
StepKind = Literal["command", "script", "sync", "transfer"]


class PipelineError(Exception):
    pass


@dataclass
class PlannedStep:
    phase: Phase
    kind: StepKind
    where: Literal["build", "deploy"]
    description: str
    command: str | None = None
    script: str | None = None
    artifact: str | None = None


@dataclass
class Artifact:
    relative: str
    absolute: str
    is_dir: bool


@dataclass
class PipelineContext:
    loaded: LoadedConfig
    target_name: str
    target: TargetCfg
    resolved: ResolvedTarget
    executor: Executor
    probe: Probe
    env: dict[str, str]
    route: Route
    run_id: str
    log_file: Path
    lock_path: str
    marker_path: str
    clean_paths: list[str]
    build_is_local: bool = False
    build_workdir: str = ""
    deploy_workdir: str = ""
    build_key: str | None = None

    @property
    def project(self) -> str:
        return self.loaded.config.project.name or self.loaded.root.name


def posix_join(base: str, relative: str) -> str:
    return posixpath.normpath(posixpath.join(base, relative))


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
                passwords["build"] = self.credentials.build_password
            if self.credentials.target_password:
                passwords["deploy"] = self.credentials.target_password
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
        try:
            resolved = resolve_target(target, self.loaded.root)
        except NodeConfigMissing as exc:
            raise PipelineError(str(exc)) from exc

        for warning in resolved.warnings:
            self.console.print(f"[yellow]warning:[/yellow] {warning}")

        build_key: str | None = None
        if resolved.build_is_local:
            if resolved.deploy_role == "dev":
                route = Route(
                    name="dev->dev",
                    description="build and deploy run locally",
                )
            elif resolved.deploy_role == "external":
                if not self.executor.check(resolved.deploy):
                    raise PipelineError(
                        f"cannot reach deploy node "
                        f"{resolved.deploy.display()} over SSH"
                    )
                route = Route(
                    name="dev->target",
                    description="artifacts are pushed from dev; deploy runs remotely",
                    uses_dev_relay=True,
                )
            else:
                raise PipelineError("local build cannot deploy on a remote build node")
        else:
            if not self.executor.check(resolved.build):
                raise PipelineError(
                    f"cannot reach build node {resolved.build.display()} over SSH"
                )
            build_key = resolve_remote_ferry_key(self.executor, resolved.build)
            prefer_via_dev = (
                bool(self.credentials.target_password)
                or resolved.transfer_from == "dev"
            )
            if resolved.deploy_role == "external":
                try:
                    edges = probe_edges(
                        self.executor,
                        resolved.build,
                        resolved.deploy,
                        project=self.project,
                        target_name=self.target_name,
                        refresh=refresh_topology,
                        skip_build_target=prefer_via_dev,
                        build_key=build_key,
                    )
                    if resolved.transfer_from == "build" and not edges.build_target:
                        raise TopologyError(
                            "transfer_from=build.node but build cannot reach deploy"
                        )
                    if resolved.transfer_from == "dev" and not edges.dev_target:
                        raise TopologyError(
                            "transfer_from=dev.node but dev cannot reach deploy"
                        )
                    route = RoutePolicy().decide(
                        edges, prefer_via_dev=prefer_via_dev
                    )
                except TopologyError as exc:
                    raise PipelineError(str(exc)) from exc
            elif resolved.deploy_role == "dev":
                route = Route(
                    name="build->dev",
                    description="artifacts are pulled to dev; deploy phases run locally",
                    uses_dev_relay=True,
                )
            else:
                route = Route(
                    name="build->build",
                    description="artifacts stay on build; deploy phases run there",
                )

        self._warn_containment(resolved)
        if self.loaded.private_path is not None:
            added = ensure_gitignore(self.loaded.root)
            if added:
                self.console.print(
                    f"[yellow]notice:[/yellow] added to .gitignore: {', '.join(added)}"
                )
        probe: Probe
        if resolved.build_is_local:
            probe = LocalProbe()
        else:
            probe = ensure_remote_probe(
                self.executor,
                resolved.build,
                resolved.build_workdir,
                reset=reset_probe,
            )
        env = load_env_file(self.loaded.root)
        run_id = new_run_id()
        workdir = resolved.build_workdir.rstrip("/")
        clean = self._clean_paths(target, workdir)
        return PipelineContext(
            loaded=self.loaded,
            target_name=self.target_name,
            target=target,
            resolved=resolved,
            executor=self.executor,
            probe=probe,
            env=env,
            route=route,
            run_id=run_id,
            log_file=log_path(self.project, self.target_name, run_id),
            lock_path=f"{workdir}/.ferry/{self.target_name}.lock",
            marker_path=f"{workdir}/.ferry/{self.target_name}.marker",
            clean_paths=clean,
            build_is_local=resolved.build_is_local,
            build_workdir=resolved.build_workdir,
            deploy_workdir=resolved.deploy_workdir,
            build_key=build_key,
        )

    def _clean_paths(self, target: TargetCfg, workdir: str) -> list[str]:
        entries = target.build.out if isinstance(target.build.out, list) else [target.build.out]
        clean = sorted({first_component(entry) for entry in entries})
        return [posix_join(workdir, item) for item in clean]

    def _warn_containment(self, resolved: ResolvedTarget) -> None:
        root = self.loaded.root.as_posix().rstrip("/")
        build_dir = posixpath.normpath(resolved.build_workdir)
        deploy_dir = posixpath.normpath(resolved.deploy_workdir)

        def overlaps(first: str, second: str) -> bool:
            return (
                first == second
                or first.startswith(second + "/")
                or second.startswith(first + "/")
            )

        if (
            resolved.build_is_local
            and build_dir != root
            and root.startswith(build_dir + "/")
        ):
            self.console.print(
                "[yellow]warning:[/yellow] build.workdir contains the project "
                "root; local sync copies may include unintended files"
            )
        if resolved.deploy_role == "build":
            if overlaps(build_dir, deploy_dir):
                self.console.print(
                    "[yellow]warning:[/yellow] build.workdir and deploy.workdir "
                    "overlap; deploy may overwrite build inputs"
                )
        elif resolved.deploy_role == "dev":
            if deploy_dir == root or root.startswith(deploy_dir + "/"):
                self.console.print(
                    "[yellow]warning:[/yellow] deploy.workdir overlaps the project "
                    "root; deploy may overwrite source files"
                )
            if resolved.build_is_local and (
                deploy_dir == build_dir or build_dir.startswith(deploy_dir + "/")
            ):
                self.console.print(
                    "[yellow]warning:[/yellow] build.workdir and deploy.workdir "
                    "overlap; deploy may overwrite build inputs"
                )

    def build_steps(self, ctx: PipelineContext | None = None) -> list[PlannedStep]:
        target = self.target
        steps: list[PlannedStep] = []
        steps.extend(self._expand(target.build.before_build, "before_build", "build"))
        in_place = (
            ctx is not None
            and ctx.build_is_local
            and Path(ctx.build_workdir).resolve() == self.loaded.root.resolve()
        )
        if not in_place:
            steps.append(
                PlannedStep(
                    phase="sync",
                    kind="sync",
                    where="build",
                    description="sync workspace to build",
                )
            )
        steps.extend(self._expand(target.build.run_build, "run_build", "build"))
        steps.extend(self._expand(target.build.after_build, "after_build", "build"))
        steps.extend(self._expand(target.deploy.before_deploy, "before_deploy", "deploy"))
        entries = target.build.out if isinstance(target.build.out, list) else [target.build.out]
        for entry in entries:
            steps.append(
                PlannedStep(
                    phase="transfer",
                    kind="transfer",
                    where="deploy",
                    description=f"transfer {entry}",
                    artifact=entry,
                )
            )
        steps.extend(self._expand(target.deploy.run_deploy, "run_deploy", "deploy"))
        steps.extend(self._expand(target.deploy.after_deploy, "after_deploy", "deploy"))
        return steps

    def _expand(
        self,
        spec: str | list[str] | None,
        phase: Phase,
        where: Literal["build", "deploy"],
    ) -> list[PlannedStep]:
        if spec is None:
            return []
        if isinstance(spec, str):
            return [
                PlannedStep(
                    phase=phase,
                    kind="command",
                    where=where,
                    description=spec,
                    command=spec,
                )
            ]
        return [
            PlannedStep(
                phase=phase,
                kind="script",
                where=where,
                description=path,
                script=path,
            )
            for path in spec
        ]

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
            topology=ctx.route.name,
            log=str(ctx.log_file),
            build_node=ctx.resolved.build.key,
        )
        log = RunLog(ctx.log_file, self.console)
        try:
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
            if ctx.loaded.private_path is not None:
                regenerate_public(
                    ctx.loaded.private_path,
                    ctx.loaded.root / ".ferry.yaml",
                )
                log.line("regenerated .ferry.yaml without node definitions")
            return state
        finally:
            log.close()


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
        index = 0
        while index < len(steps):
            step = steps[index]
            if step.kind == "sync":
                self._run_sync()
                index += 1
                continue
            if step.phase == "transfer":
                self._run_transfer(steps[index:])
                while index < len(steps) and steps[index].phase == "transfer":
                    index += 1
                continue
            if step.phase in ("run_build", "after_build"):
                group = [
                    item
                    for item in steps
                    if item.phase in ("run_build", "after_build")
                ]
                self._run_build_phase(group, marker=True)
                while index < len(steps) and steps[index].phase in ("run_build", "after_build"):
                    index += 1
                continue
            if step.phase == "before_build":
                group = [item for item in steps if item.phase == "before_build"]
                self._run_build_phase(group, marker=False)
                while index < len(steps) and steps[index].phase == "before_build":
                    index += 1
                continue
            group = [
                item for item in steps if item.phase == step.phase
            ]
            self._run_deploy_phase(group)
            while index < len(steps) and steps[index].phase == step.phase:
                index += 1

    def _run_sync(self) -> None:
        ctx = self.ctx
        target = ctx.target
        ignores = effective_ignores(target.sync.ignore, target.build.out)
        self._stage_started("sync")
        if ctx.build_is_local:
            destination = Path(ctx.build_workdir)
            if destination.resolve() == self.pipeline.loaded.root.resolve():
                self.log.line("[sync] skipped (building in place)")
                self._mark("sync", "ok")
                return
            self.log.line(
                f"[sync] copying {self.pipeline.loaded.root} -> {destination}"
            )
            try:
                count = copy_tree_local(
                    self.pipeline.loaded.root, destination, ignores
                )
            except Exception:
                self._mark("sync", "failed")
                raise
            self.log.line(f"[sync] {count} entries")
            self._mark("sync", "ok")
            return
        self.log.line(
            f"[sync] uploading {self.pipeline.loaded.root} -> "
            f"{ctx.resolved.build.display()}:{ctx.build_workdir}"
        )
        try:
            count, summary = upload_tree(
                ctx.executor,
                ctx.resolved.build,
                ctx.probe.path,
                ctx.build_workdir,
                self.pipeline.loaded.root,
                ignores,
            )
        except Exception:
            self._mark("sync", "failed")
            raise
        self.log.line(f"[sync] {count} entries ({summary})")
        self._mark("sync", "ok")

    def _step_content(self, step: PlannedStep) -> bytes:
        if step.kind == "command":
            assert step.command is not None
            return step.command.encode("utf-8")
        assert step.script is not None
        path = self.pipeline.loaded.root / step.script
        if not path.is_file():
            raise PipelineError(f"script not found: {step.script}")
        return path.read_bytes()

    def _exec_steps(
        self,
        steps: list[PlannedStep],
        *,
        where: Literal["build", "target"],
        cwd: str,
    ) -> list[ExecStep]:
        result: list[ExecStep] = []
        for index, step in enumerate(steps):
            content = self._step_content(step)
            result.append(
                ExecStep(
                    id=f"{step.phase}:{index}",
                    where=where,
                    kind="script",
                    script_b64=base64.b64encode(content).decode("ascii"),
                    cwd=cwd,
                    env=dict(self.ctx.env),
                    result=True,
                    target_host=(
                        self.ctx.resolved.deploy.destination
                        if where == "target"
                        else None
                    ),
                )
            )
        return result

    def _run_build_phase(self, steps: list[PlannedStep], *, marker: bool) -> None:
        if not steps:
            return
        ctx = self.ctx
        request = ExecRequest(
            run_id=ctx.run_id,
            workdir=ctx.build_workdir,
            steps=self._exec_steps(
                steps, where="build", cwd=ctx.build_workdir
            ),
        )
        if marker:
            request.marker = MarkerSpec(path=ctx.marker_path)
            request.clean_paths = ctx.clean_paths
        self._start_phases(steps)
        self._consume(request, steps)

    def _run_deploy_phase(self, steps: list[PlannedStep]) -> None:
        if not steps:
            return
        ctx = self.ctx
        self._start_phases(steps)
        deploy = ctx.resolved.deploy
        cwd = ctx.deploy_workdir
        if ctx.resolved.deploy_role == "dev":
            self._run_via_executor(LocalExecutor(), deploy, steps, cwd)
        elif ctx.resolved.deploy_role == "build":
            request = ExecRequest(
                run_id=ctx.run_id,
                workdir=ctx.build_workdir,
                steps=self._exec_steps(steps, where="build", cwd=cwd),
            )
            try:
                self._consume(request, steps)
            except Exception:
                self._mark_failed_phases(steps)
                raise
        elif ctx.route.uses_dev_relay:
            self._run_via_executor(ctx.executor, deploy, steps, cwd)
        else:
            request = ExecRequest(
                run_id=ctx.run_id,
                workdir=ctx.build_workdir,
                target_host=deploy.destination,
                target_options=deploy.ssh_options(),
                target_key=ctx.build_key,
                steps=self._exec_steps(steps, where="target", cwd=cwd),
            )
            try:
                self._consume(request, steps)
            except Exception:
                self._mark_failed_phases(steps)
                raise

    def _run_via_executor(
        self,
        executor: Executor,
        node: NodeSpec,
        steps: list[PlannedStep],
        cwd: str,
    ) -> None:
        for step in steps:
            self._stage_started(step.phase)
            content = self._step_content(step)
            self.log.line(f"[{step.phase}] {step.description}")
            outcome = run_target_script(
                executor, node, content, cwd, self.ctx.env
            )
            for line in outcome.stdout.splitlines():
                self.log.line(f"  {line}")
            for line in outcome.stderr.splitlines():
                self.log.line(f"  {line}")
            if outcome.code != 0:
                self._mark(step.phase, "failed")
                raise PipelineError(
                    f"{step.phase} failed on {node.display()} "
                    f"(exit code {outcome.code})"
                )
            self._mark(step.phase, "ok")
            if outcome.result:
                self.log.line(f"[{step.phase}] result: {outcome.result}")

    def _run_transfer(self, steps: list[PlannedStep]) -> None:
        ctx = self.ctx
        self._stage_started("transfer")
        artifacts = self._classify_artifacts(steps)
        deploy = ctx.resolved.deploy
        role = ctx.resolved.deploy_role
        workdir = ctx.deploy_workdir
        try:
            for artifact in artifacts:
                self.log.line(
                    f"[transfer] {artifact.relative} -> {deploy.display()}:{workdir}"
                )
                if ctx.build_is_local:
                    if role == "dev":
                        copy_artifact_local(
                            artifact.absolute, artifact.is_dir, workdir
                        )
                    else:
                        push_stream_to_target(
                            ctx.executor,
                            deploy,
                            workdir,
                            self._probe_chunks(artifact),
                        )
                elif role == "dev":
                    chunks = self._probe_chunks(artifact)
                    push_stream_to_target(
                        LocalExecutor(), deploy, workdir, chunks
                    )
                elif role == "build":
                    self._run_build_command(
                        artifact_copy_command(artifact.absolute, artifact.is_dir, workdir)
                    )
                elif ctx.route.uses_dev_relay:
                    chunks = self._probe_chunks(artifact)
                    push_stream_to_target(ctx.executor, deploy, workdir, chunks)
                else:
                    self._run_build_command(
                        artifact_transfer_command(
                            artifact.absolute,
                            artifact.is_dir,
                            deploy,
                            workdir,
                            key=ctx.build_key,
                        )
                    )
        except Exception:
            self._mark("transfer", "failed")
            raise
        self._mark("transfer", "ok")

    def _probe_chunks(self, artifact: Artifact) -> Iterator[bytes]:
        if artifact.is_dir:
            return self.ctx.probe.get_stream(artifact.absolute)
        return self.ctx.probe.get_stream(
            posixpath.dirname(artifact.absolute),
            [posixpath.basename(artifact.absolute)],
        )

    def _run_build_command(self, command: str) -> None:
        request = ExecRequest(
            run_id=self.ctx.run_id,
            workdir=self.ctx.build_workdir,
            steps=[
                ExecStep(
                    id="transfer:0",
                    where="build",
                    kind="script",
                    script_b64=base64.b64encode(command.encode("utf-8")).decode(
                        "ascii"
                    ),
                    cwd=self.ctx.build_workdir,
                    env=dict(self.ctx.env),
                )
            ],
        )
        self._consume(request, [], mark_phases=False)

    def _classify_artifacts(self, steps: list[PlannedStep]) -> list[Artifact]:
        entries = [step.artifact for step in steps if step.artifact]
        workdir = self.ctx.build_workdir
        if self.ctx.build_is_local:
            local_artifacts: list[Artifact] = []
            for entry in entries:
                path = (Path(workdir) / entry).resolve()
                if path.is_dir():
                    is_dir = True
                elif path.is_file():
                    is_dir = False
                else:
                    raise PipelineError(f"artifact not found on build: {entry}")
                local_artifacts.append(
                    Artifact(
                        relative=entry,
                        absolute=path.as_posix(),
                        is_dir=is_dir,
                    )
                )
            return local_artifacts
        command = classify_artifacts_command(workdir, entries)
        result = self.ctx.executor.run(
            self.ctx.resolved.build, ["sh", "-c", command], timeout=30
        )
        if result.returncode != 0:
            raise PipelineError(
                f"cannot inspect artifacts on build: {result.stderr_text.strip()}"
            )
        types: dict[str, bool] = {}
        for line in result.stdout_text.splitlines():
            if line.startswith("dir:"):
                types[line[4:]] = True
            elif line.startswith("file:"):
                types[line[5:]] = False
        artifacts: list[Artifact] = []
        for entry in entries:
            if entry not in types:
                raise PipelineError(f"artifact not found on build: {entry}")
            absolute = posix_join(workdir, entry)
            artifacts.append(
                Artifact(relative=entry, absolute=absolute, is_dir=types[entry])
            )
        return artifacts

    def _consume(
        self,
        request: ExecRequest,
        steps: list[PlannedStep],
        *,
        mark_phases: bool = True,
    ) -> None:
        by_id = {
            f"{step.phase}:{index}": step for index, step in enumerate(steps)
        }
        executed: set[str] = set()
        for event in self.ctx.probe.exec(request):
            if event.event == "start":
                if event.pid:
                    self.state.probe_pid = event.pid
                    save_state(self.state)
            elif event.event == "recovery":
                cleaned = event.cleaned or []
                self.log.line(
                    f"[recovery] previous build was interrupted; cleaned: "
                    f"{', '.join(cleaned) or 'nothing'}",
                    style="yellow",
                )
            elif event.event == "error":
                raise PipelineError(
                    f"probe error: {event.message or 'unknown error'}"
                )
            elif event.event == "step_start" and event.id:
                step = by_id.get(event.id)
                if step is not None:
                    self._stage_started(step.phase)
                    self.log.line(f"[{step.phase}] {step.description}")
            elif event.event == "out" and event.chunk:
                text = base64.b64decode(event.chunk).decode("utf-8", errors="replace")
                self.log.line(f"  {text}")
            elif event.event == "step_end" and event.id:
                step = by_id.get(event.id)
                executed.add(event.id)
                duration = (event.duration_ms or 0) / 1000
                if event.code not in (0, None):
                    detail = f"exit code {event.code}"
                    if event.result and event.result.state == "value":
                        detail += f", result={event.result.text}"
                    if event.tail:
                        detail += f"\nlast output:\n{event.tail}"
                    if step is not None and mark_phases:
                        self._mark(step.phase, "failed")
                    self.log.line(f"[{event.id}] FAILED: {detail}", style="red")
                    raise PipelineError(
                        f"step failed: {event.id} (exit code {event.code})"
                    )
                if step is not None and mark_phases:
                    self._mark(step.phase, "ok")
                if event.result and event.result.state != "unset":
                    label: str = event.result.state
                    if event.result.state == "value":
                        label = event.result.text
                    self.log.line(f"[{event.id}] result: {label} ({duration:.1f}s)")
                else:
                    self.log.line(f"[{event.id}] ok ({duration:.1f}s)")
            elif event.event == "end":
                self.end_code = event.code
                if event.code == 3:
                    self.interrupted = True
        if self.interrupted or self.end_code == 3:
            self.mark_interrupted()
            raise PipelineError("run interrupted; build marker retained for the next run")
        if self.end_code not in (0, None):
            raise PipelineError(f"probe exited with code {self.end_code}")
        if steps and len(executed) < len(steps):
            raise PipelineError("probe stopped before all steps completed")

    def _start_phases(self, steps: list[PlannedStep]) -> None:
        for phase in dict.fromkeys(step.phase for step in steps):
            self._stage_started(phase)

    def _mark_failed_phases(self, steps: list[PlannedStep]) -> None:
        for phase in dict.fromkeys(step.phase for step in steps):
            current = self.state.stages.get(phase)
            if current is None or current.state == "running":
                self._mark(phase, "failed")

    def _stage_started(self, phase: str) -> None:
        self.state.stages[phase] = StageState(state="running", since=None)
        save_state(self.state)

    def _mark(self, phase: str, status: str) -> None:
        self.state.stages[phase] = StageState(state=status)  # type: ignore[arg-type]
        save_state(self.state)

    def mark_interrupted(self) -> None:
        for phase, stage_state in list(self.state.stages.items()):
            if stage_state.state == "running":
                self.state.stages[phase] = StageState(state="interrupted")
        save_state(self.state)


def render_plan(console: Console, ctx: PipelineContext, steps: list[PlannedStep]) -> None:
    console.print(f"target: {ctx.target_name}")
    console.print(f"route:  {ctx.route.name} ({ctx.route.description})")
    console.print(
        f"nodes:  dev -> build={ctx.resolved.build.display()} "
        f"-> deploy={ctx.resolved.deploy.display()}"
    )
    console.print(f"build workdir:  {ctx.build_workdir}")
    console.print(f"deploy workdir: {ctx.deploy_workdir}")
    if ctx.build_is_local and not any(step.kind == "sync" for step in steps):
        console.print("  [sync] skipped (build in place at project root)", markup=False)
    if ctx.env:
        console.print("env:")
        for key, value in sorted(masked_env(ctx.env).items()):
            console.print(f"  {key}={value}")
    console.print("steps:")
    for index, step in enumerate(steps, start=1):
        console.print(
            f"  {index:2d}. [{step.phase}] {step.description}", markup=False
        )


__all__ = [
    "Pipeline",
    "PipelineContext",
    "PipelineError",
    "PlannedStep",
    "posix_join",
    "render_plan",
]
