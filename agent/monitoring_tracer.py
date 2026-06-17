"""PEP 669 monitoring callbacks — port of Tracer tier-1 ENTRY (ARCHITECTURE_V2 §5.3)."""

from __future__ import annotations

import queue
import sys
import types
from typing import Any

from agent.capture import capture_raw
from agent.models import CaptureMode, RawCapture, TraceEvent
from agent.monitoring_installer import HYPERPROBE_TOOL_ID, MonitoringCallback
from agent.registry import BreakpointRegistry
from agent.worker import DropLogger, enqueue_capture

_PY_START = sys.monitoring.events.PY_START
_NO_EVENTS = sys.monitoring.events.NO_EVENTS


class MonitoringTracer:
    """Global PY_START handler with ENTRY capture on function/method hits."""

    def __init__(
        self,
        registry: BreakpointRegistry,
        capture_queue: queue.Queue[RawCapture],
        *,
        drop_logger: DropLogger | None = None,
    ) -> None:
        self._registry = registry
        self._capture_queue = capture_queue
        self._drop_logger = drop_logger

    def callbacks(self) -> dict[int, MonitoringCallback]:
        """Event handlers for :func:`agent.monitoring_installer.install_monitoring`."""
        return {_PY_START: self.on_py_start}

    def activate_global_events(self, tool_id: int = HYPERPROBE_TOOL_ID) -> None:
        """Enable tier-1 PY_START filter (mirrors process-wide ``global_trace``)."""
        sys.monitoring.set_events(tool_id, _PY_START)

    def deactivate_global_events(self, tool_id: int = HYPERPROBE_TOOL_ID) -> None:
        """Disable global monitoring events on shutdown."""
        sys.monitoring.set_events(tool_id, _NO_EVENTS)

    def on_py_start(self, code: types.CodeType, instruction_offset: int) -> None:
        """Handle PY_START — map to CALL breakpoint matching and ENTRY capture."""
        try:
            if not self._registry.has_any_function_or_method_bp():
                return None

            frame = _frame_for_code(code)
            bp_ids = self._registry.get_matching_breakpoint_ids(
                co_name=code.co_name,
                co_qualname=code.co_qualname or code.co_name,
                co_filename=code.co_filename,
                lineno=frame.f_lineno,
                event=TraceEvent.CALL,
            )
            for bp_id in bp_ids:
                bp = self._registry.get(bp_id)
                if bp is None:
                    continue
                if bp.capture_mode in (CaptureMode.ENTRY, CaptureMode.BOTH):
                    self._enqueue(frame, bp_id, TraceEvent.CALL)
            return None
        except BaseException as exc:
            print(f"on_py_start error: {exc}", file=sys.stderr)
            return None

    def _enqueue(
        self,
        frame: types.FrameType,
        breakpoint_id: str,
        event: TraceEvent,
        *,
        return_value: Any | None = None,
    ) -> None:
        raw = capture_raw(
            frame,
            breakpoint_id=breakpoint_id,
            event=event,
            return_value=return_value,
        )
        enqueue_capture(
            self._capture_queue,
            raw,
            drop_logger=self._drop_logger,
        )


def _frame_for_code(code: types.CodeType) -> types.FrameType:
    """Return the live frame for ``code`` when a PY_START callback fires."""
    depth = 1
    while True:
        frame = sys._getframe(depth)
        if frame.f_code is code:
            return frame
        depth += 1
