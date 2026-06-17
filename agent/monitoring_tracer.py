"""PEP 669 monitoring callbacks — port of Tracer two-tier logic (ARCHITECTURE_V2 §5.3)."""

from __future__ import annotations

import queue
import sys
import types
from typing import Any

from agent.breakpoints import normalize_path
from agent.capture import capture_raw
from agent.models import CaptureMode, RawCapture, TraceEvent
from agent.monitoring_installer import HYPERPROBE_TOOL_ID, MonitoringCallback
from agent.registry import BreakpointRegistry
from agent.worker import DropLogger, enqueue_capture

_PY_START = sys.monitoring.events.PY_START
_PY_RETURN = sys.monitoring.events.PY_RETURN
_LINE = sys.monitoring.events.LINE
_NO_EVENTS = sys.monitoring.events.NO_EVENTS
_LOCAL_WATCH_EVENTS = _LINE | _PY_RETURN
_GLOBAL_EVENTS = _PY_START | _PY_RETURN


class MonitoringTracer:
    """PY_START tier-1 filter with scoped LINE/PY_RETURN on watched code objects."""

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
        self._frame_return_bps: dict[int, list[str]] = {}
        self._local_events_enabled: set[int] = set()

    def callbacks(self) -> dict[int, MonitoringCallback]:
        """Event handlers for :func:`agent.monitoring_installer.install_monitoring`."""
        return {
            _PY_START: self.on_py_start,
            _PY_RETURN: self.on_py_return,
            _LINE: self.on_line,
        }

    def activate_global_events(self, tool_id: int = HYPERPROBE_TOOL_ID) -> None:
        """Enable tier-1 PY_START/PY_RETURN (mirrors process-wide ``global_trace``)."""
        sys.monitoring.set_events(tool_id, _GLOBAL_EVENTS)

    def deactivate_global_events(self, tool_id: int = HYPERPROBE_TOOL_ID) -> None:
        """Disable global monitoring events on shutdown."""
        sys.monitoring.set_events(tool_id, _NO_EVENTS)
        self._frame_return_bps.clear()
        self._local_events_enabled.clear()

    def on_py_start(self, code: types.CodeType, instruction_offset: int) -> None:
        """Handle PY_START — ENTRY capture and scoped local watch setup."""
        try:
            if (
                not self._registry.has_any_function_or_method_bp()
                and not self._registry.watched_files()
            ):
                return None

            frame = _frame_for_code(code)
            path = normalize_path(code.co_filename)
            bp_ids = self._registry.get_matching_breakpoint_ids(
                co_name=code.co_name,
                co_qualname=code.co_qualname or code.co_name,
                co_filename=code.co_filename,
                lineno=frame.f_lineno,
                event=TraceEvent.CALL,
            )

            local_watch_needed = path in self._registry.watched_files()
            for bp_id in bp_ids:
                bp = self._registry.get(bp_id)
                if bp is None:
                    continue
                if bp.capture_mode in (CaptureMode.ENTRY, CaptureMode.BOTH):
                    self._enqueue(frame, bp_id, TraceEvent.CALL)
                if bp.capture_mode in (CaptureMode.RETURN, CaptureMode.BOTH):
                    local_watch_needed = True

            if local_watch_needed:
                return_bps = self._return_breakpoint_ids(bp_ids)
                if return_bps:
                    self._frame_return_bps[id(frame)] = return_bps
                self._ensure_local_watch_events(code)
            return None
        except BaseException as exc:
            print(f"on_py_start error: {exc}", file=sys.stderr)
            return None

    def on_py_return(
        self,
        code: types.CodeType,
        instruction_offset: int,
        retval: object,
    ) -> None:
        """Handle PY_RETURN — function/method RETURN/BOTH and file_line RETURN/BOTH."""
        try:
            frame = _frame_for_code(code)
            bp_ids = self._frame_return_bps.pop(id(frame), [])
            for bp_id in bp_ids:
                self._enqueue(
                    frame,
                    bp_id,
                    TraceEvent.RETURN,
                    return_value=retval,
                )
            self._capture_file_line_hits(
                frame,
                TraceEvent.RETURN,
                return_value=retval,
            )
            return None
        except BaseException as exc:
            print(f"on_py_return error: {exc}", file=sys.stderr)
            return None

    def on_line(self, code: types.CodeType, line_number: int) -> None:
        """Handle LINE — file_line ENTRY/BOTH at exact line."""
        try:
            frame = _frame_for_code(code)
            self._capture_file_line_hits(
                frame,
                TraceEvent.LINE,
                line_number=line_number,
            )
            return None
        except BaseException as exc:
            print(f"on_line error: {exc}", file=sys.stderr)
            return None

    def _ensure_local_watch_events(self, code: types.CodeType) -> None:
        code_id = id(code)
        if code_id in self._local_events_enabled:
            return
        sys.monitoring.set_local_events(
            HYPERPROBE_TOOL_ID,
            code,
            _LOCAL_WATCH_EVENTS,
        )
        self._local_events_enabled.add(code_id)

    def _return_breakpoint_ids(self, bp_ids: list[str]) -> list[str]:
        matched: list[str] = []
        for bp_id in bp_ids:
            bp = self._registry.get(bp_id)
            if bp is None:
                continue
            if bp.capture_mode in (CaptureMode.RETURN, CaptureMode.BOTH):
                matched.append(bp_id)
        return matched

    def _capture_file_line_hits(
        self,
        frame: types.FrameType,
        event: TraceEvent,
        *,
        return_value: Any | None = None,
        line_number: int | None = None,
    ) -> None:
        lineno = line_number if line_number is not None else frame.f_lineno
        bp_ids = self._registry.get_line_breakpoint_ids(
            frame.f_code.co_filename,
            lineno,
        )
        for bp_id in bp_ids:
            bp = self._registry.get(bp_id)
            if bp is None:
                continue
            if event == TraceEvent.LINE and bp.capture_mode in (
                CaptureMode.ENTRY,
                CaptureMode.BOTH,
            ):
                self._enqueue(frame, bp_id, TraceEvent.LINE)
            elif event == TraceEvent.RETURN and bp.capture_mode in (
                CaptureMode.RETURN,
                CaptureMode.BOTH,
            ):
                self._enqueue(
                    frame,
                    bp_id,
                    TraceEvent.RETURN,
                    return_value=return_value,
                )

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
    """Return the live frame for ``code`` when a monitoring callback fires."""
    depth = 1
    while True:
        frame = sys._getframe(depth)
        if frame.f_code is code:
            return frame
        depth += 1
