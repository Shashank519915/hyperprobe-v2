"""MonitoringTracer capture via PEP 669 PY_START, PY_RETURN, and LINE."""

from __future__ import annotations

import json
import queue
from pathlib import Path

from agent.breakpoints import normalize_path
from agent.models import Breakpoint, BreakpointType, CaptureMode, TraceEvent
from agent.monitoring_installer import (
    HYPERPROBE_TOOL_ID,
    install_monitoring,
    remove_monitoring,
)
from agent.monitoring_tracer import MonitoringTracer
from agent.registry import BreakpointRegistry
from agent.worker import SnapshotWorker, create_capture_queue
from target.engines.addition import AdditionEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDITION_ENGINE_FILE = REPO_ROOT / "target" / "engines" / "addition.py"
ADDITION_RETURN_LINE = 5


class _AdditionEngine:
    def add(self, a: int, b: int) -> int:
        return a + b


def _run_with_monitoring_tracer(tracer: MonitoringTracer, target) -> None:
    installer = install_monitoring(tracer.callbacks())
    tracer.activate_global_events(HYPERPROBE_TOOL_ID)
    try:
        target()
    finally:
        tracer.deactivate_global_events(HYPERPROBE_TOOL_ID)
        remove_monitoring(installer)


def _drain_queue(capture_queue: queue.Queue) -> list:
    items = []
    while not capture_queue.empty():
        items.append(capture_queue.get_nowait())
        capture_queue.task_done()
    return items


def test_monitoring_tracer_fast_reject_when_no_breakpoints():
    registry = BreakpointRegistry()
    capture_queue = create_capture_queue()
    tracer = MonitoringTracer(registry, capture_queue)

    def unrelated() -> int:
        return 1

    _run_with_monitoring_tracer(tracer, unrelated)
    assert capture_queue.empty()


def test_monitoring_tracer_entry_enqueues_raw_capture():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-add",
            type=BreakpointType.FUNCTION,
            value="add",
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    def add(a: int, b: int) -> int:
        return a + b

    _run_with_monitoring_tracer(tracer, lambda: add(10, 20))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 1
    assert captured[0].breakpoint_id == "bp-add"
    assert captured[0].event == TraceEvent.CALL
    assert captured[0].frames[0].function == "add"
    assert captured[0].frames[0].locals["a"] == 10


def test_monitoring_tracer_entry_multiple_breakpoints_same_name():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-1",
            type=BreakpointType.FUNCTION,
            value="compute",
            capture_mode=CaptureMode.ENTRY,
        )
    )
    registry.register(
        Breakpoint(
            id="bp-2",
            type=BreakpointType.FUNCTION,
            value="compute",
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    def compute(x: int) -> int:
        return x + 1

    _run_with_monitoring_tracer(tracer, lambda: compute(5))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 2
    assert {item.breakpoint_id for item in captured} == {"bp-1", "bp-2"}


def test_monitoring_tracer_method_breakpoint_matches_qualname():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-method",
            type=BreakpointType.METHOD,
            value="_AdditionEngine.add",
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: _AdditionEngine().add(3, 4))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 1
    assert captured[0].breakpoint_id == "bp-method"
    assert captured[0].frames[0].qualname == "_AdditionEngine.add"


def test_monitoring_tracer_target_addition_engine_add_entry():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-target-add",
            type=BreakpointType.METHOD,
            value="AdditionEngine.add",
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: AdditionEngine().add(10.0, 20.0))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 1
    assert captured[0].breakpoint_id == "bp-target-add"
    assert captured[0].frames[0].qualname == "AdditionEngine.add"
    assert captured[0].frames[0].locals["a"] == 10.0


def test_monitoring_tracer_both_mode_captures_call_and_return():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-both",
            type=BreakpointType.FUNCTION,
            value="tracked",
            capture_mode=CaptureMode.BOTH,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    def tracked(value: int) -> int:
        return value * 2

    _run_with_monitoring_tracer(tracer, lambda: tracked(7))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 2
    assert {item.event for item in captured} == {TraceEvent.CALL, TraceEvent.RETURN}
    assert all(item.breakpoint_id == "bp-both" for item in captured)
    return_capture = next(item for item in captured if item.event == TraceEvent.RETURN)
    assert return_capture.return_value == 14


def test_monitoring_tracer_return_mode_captures_on_py_return():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-return",
            type=BreakpointType.FUNCTION,
            value="later",
            capture_mode=CaptureMode.RETURN,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    def later(x: int) -> int:
        result = x + 1
        return result

    _run_with_monitoring_tracer(tracer, lambda: later(4))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 1
    assert captured[0].breakpoint_id == "bp-return"
    assert captured[0].event == TraceEvent.RETURN
    assert captured[0].return_value == 5
    assert captured[0].frames[0].locals["result"] == 5


def test_monitoring_tracer_entry_produces_snapshot_json(tmp_path):
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-add",
            type=BreakpointType.FUNCTION,
            value="add",
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    worker = SnapshotWorker(
        capture_queue,
        registry,
        output_dir=tmp_path,
    )
    tracer = MonitoringTracer(registry, capture_queue)
    worker.start()

    def add(a: int, b: int) -> int:
        return a + b

    _run_with_monitoring_tracer(tracer, lambda: add(2, 3))
    capture_queue.join()
    worker.stop()

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["breakpoint_id"] == "bp-add"
    assert payload["stack_frames"][0]["locals"]["a"] == 2


def test_monitoring_tracer_file_line_entry_fires_at_exact_line():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-line-entry",
            type=BreakpointType.FILE_LINE,
            file="target/engines/addition.py",
            line=ADDITION_RETURN_LINE,
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: AdditionEngine().add(10.0, 5.0))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 1
    frame = captured[0].frames[0]
    assert captured[0].event == TraceEvent.LINE
    assert frame.line == ADDITION_RETURN_LINE
    assert frame.file == normalize_path(ADDITION_ENGINE_FILE)
    assert frame.locals["a"] == 10.0


def test_monitoring_tracer_file_line_does_not_fire_on_non_executed_line():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-class-line",
            type=BreakpointType.FILE_LINE,
            file=str(ADDITION_ENGINE_FILE),
            line=1,
            capture_mode=CaptureMode.BOTH,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: AdditionEngine().add(1.0, 2.0))
    assert capture_queue.empty()


def test_monitoring_tracer_file_line_both_captures_line_and_return():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-line-both",
            type=BreakpointType.FILE_LINE,
            file="target/engines/addition.py",
            line=ADDITION_RETURN_LINE,
            capture_mode=CaptureMode.BOTH,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: AdditionEngine().add(3.0, 9.0))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 2
    by_event = {item.event: item for item in captured}
    assert set(by_event) == {TraceEvent.LINE, TraceEvent.RETURN}
    assert all(item.frames[0].line == ADDITION_RETURN_LINE for item in captured)
    assert by_event[TraceEvent.RETURN].return_value == 12.0


def test_monitoring_tracer_combined_method_both_and_file_line_entry():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-method-both",
            type=BreakpointType.METHOD,
            value="AdditionEngine.add",
            capture_mode=CaptureMode.BOTH,
        )
    )
    registry.register(
        Breakpoint(
            id="bp-line-entry",
            type=BreakpointType.FILE_LINE,
            file=str(ADDITION_ENGINE_FILE),
            line=ADDITION_RETURN_LINE,
            capture_mode=CaptureMode.ENTRY,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: AdditionEngine().add(2.0, 3.0))
    captured = _drain_queue(capture_queue)

    events = {(item.breakpoint_id, item.event) for item in captured}
    assert ("bp-method-both", TraceEvent.CALL) in events
    assert ("bp-method-both", TraceEvent.RETURN) in events
    assert ("bp-line-entry", TraceEvent.LINE) in events
    assert len(captured) == 3


def test_monitoring_tracer_combined_method_return_and_file_line_return():
    registry = BreakpointRegistry()
    registry.register(
        Breakpoint(
            id="bp-method-return",
            type=BreakpointType.METHOD,
            value="AdditionEngine.add",
            capture_mode=CaptureMode.RETURN,
        )
    )
    registry.register(
        Breakpoint(
            id="bp-line-return",
            type=BreakpointType.FILE_LINE,
            file=str(ADDITION_ENGINE_FILE),
            line=ADDITION_RETURN_LINE,
            capture_mode=CaptureMode.RETURN,
        )
    )
    capture_queue = create_capture_queue(maxsize=10)
    tracer = MonitoringTracer(registry, capture_queue)

    _run_with_monitoring_tracer(tracer, lambda: AdditionEngine().add(4.0, 5.0))
    captured = _drain_queue(capture_queue)

    assert len(captured) == 2
    by_id = {item.breakpoint_id: item for item in captured}
    assert by_id["bp-method-return"].event == TraceEvent.RETURN
    assert by_id["bp-method-return"].return_value == 9.0
    assert by_id["bp-line-return"].event == TraceEvent.RETURN
    assert by_id["bp-line-return"].return_value == 9.0
