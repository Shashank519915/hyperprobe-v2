"""Spike: prove PEP 669 sys.monitoring callbacks fire on the calculator path."""

from __future__ import annotations

import sys

import pytest

from target.engines.addition import AdditionEngine

# HyperProbe spike tool slot — reserved IDs 0–5; PROFILER_ID used until installer lands.
_SPIKE_TOOL_ID = sys.monitoring.PROFILER_ID
_SPIKE_TOOL_NAME = "hyperprobe-spike"

_PY_START = sys.monitoring.events.PY_START
_PY_RETURN = sys.monitoring.events.PY_RETURN
_ADD_EVENTS = _PY_START | _PY_RETURN


@pytest.fixture
def monitoring_spike_tool():
    """Register spike callbacks; always free tool_id after the test."""
    sys.monitoring.use_tool_id(_SPIKE_TOOL_ID, _SPIKE_TOOL_NAME)
    try:
        yield _SPIKE_TOOL_ID
    finally:
        sys.monitoring.free_tool_id(_SPIKE_TOOL_ID)


def _install_add_local_monitoring(
    tool_id: int,
    *,
    on_start,
    on_return,
) -> None:
    add_code = AdditionEngine.add.__code__
    sys.monitoring.set_local_events(tool_id, add_code, _ADD_EVENTS)
    sys.monitoring.register_callback(tool_id, _PY_START, on_start)
    sys.monitoring.register_callback(tool_id, _PY_RETURN, on_return)


def test_monitoring_spike_fires_py_start_and_py_return_on_addition_engine_add(
    monitoring_spike_tool,
):
    events: list[tuple[str, str, object | None]] = []

    def on_start(code, instruction_offset: int) -> None:
        events.append(("PY_START", code.co_qualname, instruction_offset))

    def on_return(code, instruction_offset: int, retval: object) -> None:
        events.append(("PY_RETURN", code.co_qualname, retval))

    _install_add_local_monitoring(
        monitoring_spike_tool,
        on_start=on_start,
        on_return=on_return,
    )

    result = AdditionEngine().add(10.0, 20.0)

    assert result == 30.0
    assert [event[0] for event in events] == ["PY_START", "PY_RETURN"]
    assert events[0][1] == "AdditionEngine.add"
    assert events[1][1] == "AdditionEngine.add"
    assert events[1][2] == 30.0


def test_monitoring_spike_local_events_ignore_unscoped_code(monitoring_spike_tool):
    events: list[str] = []

    def on_start(code, instruction_offset: int) -> None:
        events.append(code.co_qualname)

    def on_return(code, instruction_offset: int, retval: object) -> None:
        events.append(code.co_qualname)

    _install_add_local_monitoring(
        monitoring_spike_tool,
        on_start=on_start,
        on_return=on_return,
    )

    def unrelated() -> int:
        return 1 + 1

    assert unrelated() == 2
    assert events == []

    AdditionEngine().add(3.0, 4.0)
    assert events == ["AdditionEngine.add", "AdditionEngine.add"]
