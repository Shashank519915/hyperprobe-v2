"""Wire agent instrumentation + target server — process entrypoint (§5.1, R4)."""

from __future__ import annotations

import os
import queue
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent.breakpoints import load_breakpoints_yaml
from agent.control_server import (
    DEFAULT_CONTROL_HOST,
    DEFAULT_CONTROL_PORT,
    AgentControlServer,
)
from agent.installer import TraceInstaller, install_trace, remove_trace
from agent.monitoring_installer import (
    MonitoringInstaller,
    install_monitoring,
    remove_monitoring,
)
from agent.registry import BreakpointRegistry
from agent.tracer import Tracer
from agent.worker import SnapshotWorker, create_capture_queue

if TYPE_CHECKING:
    from target.server import _CalculatorHTTPServer

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BREAKPOINTS_PATH = REPO_ROOT / "breakpoints.yaml"
DEFAULT_SNAPSHOTS_DIR = REPO_ROOT / "snapshots"

BACKEND_SETTRACE = "settrace"
BACKEND_MONITORING = "monitoring"
DEFAULT_BACKEND = BACKEND_SETTRACE
VALID_BACKENDS = frozenset({BACKEND_SETTRACE, BACKEND_MONITORING})


def resolve_instrumentation_backend(
    env_value: str | None = None,
) -> str:
    """Return ``settrace`` (default) or ``monitoring`` from env / explicit value."""
    if env_value is not None:
        raw = env_value.strip().lower()
    else:
        raw = os.environ.get("HYPERPROBE_BACKEND", "").strip().lower()
    if not raw:
        return DEFAULT_BACKEND
    if raw in VALID_BACKENDS:
        return raw
    raise ValueError(
        f"HYPERPROBE_BACKEND must be {BACKEND_SETTRACE!r} or "
        f"{BACKEND_MONITORING!r}, got {raw!r}",
    )


def _monitoring_stub_callbacks() -> dict[int, object]:
    """Placeholder callbacks until MonitoringTracer wires real handlers (PR-17)."""

    def _noop_py_start(code, instruction_offset: int) -> None:
        return None

    def _noop_py_return(code, instruction_offset: int, retval: object) -> None:
        return None

    return {
        sys.monitoring.events.PY_START: _noop_py_start,
        sys.monitoring.events.PY_RETURN: _noop_py_return,
    }


@dataclass
class AgentRuntime:
    """Wired agent components — worker, control API, and instrumentation hooks."""

    registry: BreakpointRegistry
    capture_queue: queue.Queue
    worker: SnapshotWorker
    tracer: Tracer
    control_server: AgentControlServer
    backend: str
    installer: TraceInstaller | MonitoringInstaller

    def shutdown(self) -> None:
        self.control_server.stop()
        self.worker.stop()
        if self.backend == BACKEND_MONITORING:
            remove_monitoring(self.installer)  # type: ignore[arg-type]
        else:
            remove_trace(self.installer)  # type: ignore[arg-type]


def start_agent(
    *,
    breakpoints_path: Path | str = DEFAULT_BREAKPOINTS_PATH,
    snapshots_dir: Path | str = DEFAULT_SNAPSHOTS_DIR,
    control_host: str = DEFAULT_CONTROL_HOST,
    control_port: int = DEFAULT_CONTROL_PORT,
    backend: str | None = None,
) -> AgentRuntime:
    """Load seed config, start worker + control API, install instrumentation hooks."""
    resolved_backend = resolve_instrumentation_backend(backend)

    registry = BreakpointRegistry()
    load_breakpoints_yaml(breakpoints_path, registry)

    capture_queue = create_capture_queue()
    worker = SnapshotWorker(
        capture_queue,
        registry,
        output_dir=snapshots_dir,
    )
    worker.start()

    tracer = Tracer(registry, capture_queue)
    if resolved_backend == BACKEND_MONITORING:
        installer = install_monitoring(_monitoring_stub_callbacks())
    else:
        installer = install_trace(tracer.global_trace)

    control_server = AgentControlServer(
        registry,
        host=control_host,
        port=control_port,
    )
    control_server.start()

    return AgentRuntime(
        registry=registry,
        capture_queue=capture_queue,
        worker=worker,
        tracer=tracer,
        control_server=control_server,
        backend=resolved_backend,
        installer=installer,
    )


def run(
    *,
    target_host: str | None = None,
    target_port: int | None = None,
    control_host: str | None = None,
    control_port: int | None = None,
    breakpoints_path: Path | str | None = None,
    snapshots_dir: Path | str | None = None,
) -> None:
    """Start agent stack, then block serving the calculator on :8080."""
    from target.server import DEFAULT_HOST, DEFAULT_PORT, create_server

    runtime = start_agent(
        breakpoints_path=breakpoints_path or DEFAULT_BREAKPOINTS_PATH,
        snapshots_dir=snapshots_dir or DEFAULT_SNAPSHOTS_DIR,
        control_host=control_host or DEFAULT_CONTROL_HOST,
        control_port=control_port if control_port is not None else DEFAULT_CONTROL_PORT,
    )
    server: _CalculatorHTTPServer = create_server(
        host=target_host or DEFAULT_HOST,
        port=target_port if target_port is not None else DEFAULT_PORT,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        runtime.shutdown()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def main() -> None:
    resolve_instrumentation_backend()
    run(
        target_host=os.environ.get("TARGET_HOST", "").strip() or None,
        target_port=_env_int("TARGET_PORT", 8080),
        control_host=os.environ.get("CONTROL_HOST", "").strip() or None,
        control_port=_env_int("CONTROL_PORT", DEFAULT_CONTROL_PORT),
        breakpoints_path=os.environ.get("BREAKPOINTS_YAML", "").strip() or None,
        snapshots_dir=os.environ.get("SNAPSHOTS_DIR", "").strip() or None,
    )


if __name__ == "__main__":
    main()
