import sys
import threading

import pytest

from agent.monitoring_installer import (
    HYPERPROBE_TOOL_ID,
    HYPERPROBE_TOOL_NAME,
    MonitoringInstaller,
    disable_monitoring_on_current_thread,
    install_monitoring,
    remove_monitoring,
)
from target.engines.addition import AdditionEngine

_PY_START = sys.monitoring.events.PY_START
_PY_RETURN = sys.monitoring.events.PY_RETURN
_ADD_EVENTS = _PY_START | _PY_RETURN


def _enable_add_local_events() -> None:
    add_code = AdditionEngine.add.__code__
    sys.monitoring.set_local_events(HYPERPROBE_TOOL_ID, add_code, _ADD_EVENTS)


def test_install_claims_tool_id_and_registers_callbacks():
    events: list[str] = []

    def on_start(code, instruction_offset: int) -> None:
        events.append("PY_START")

    installer = install_monitoring({_PY_START: on_start})
    try:
        assert installer.is_installed
        assert sys.monitoring.get_tool(HYPERPROBE_TOOL_ID) == HYPERPROBE_TOOL_NAME
        assert installer.callbacks == {_PY_START: on_start}

        _enable_add_local_events()
        AdditionEngine().add(1.0, 2.0)
        assert events == ["PY_START"]
    finally:
        remove_monitoring(installer)


def test_remove_frees_tool_id():
    installer = install_monitoring(
        {_PY_START: lambda code, instruction_offset: None},
    )
    remove_monitoring(installer)

    assert not installer.is_installed
    assert installer.callbacks == {}
    assert sys.monitoring.get_tool(HYPERPROBE_TOOL_ID) is None


def test_remove_is_idempotent():
    installer = MonitoringInstaller()
    installer.install({_PY_START: lambda code, instruction_offset: None})
    installer.remove()
    installer.remove()
    assert not installer.is_installed
    assert sys.monitoring.get_tool(HYPERPROBE_TOOL_ID) is None


def test_install_rejects_empty_callbacks():
    installer = MonitoringInstaller()
    with pytest.raises(ValueError, match="empty"):
        installer.install({})
    assert not installer.is_installed


def test_install_rejects_none_callback():
    installer = MonitoringInstaller()
    with pytest.raises(ValueError, match="None"):
        installer.install({_PY_START: None})  # type: ignore[dict-item]
    assert not installer.is_installed


def test_disable_monitoring_on_current_thread_suppresses_callbacks():
    events: list[str] = []
    lock = threading.Lock()

    def on_start(code, instruction_offset: int) -> None:
        with lock:
            events.append(threading.current_thread().name)

    installer = install_monitoring({_PY_START: on_start})
    try:
        _enable_add_local_events()

        def agent_thread_entry() -> None:
            disable_monitoring_on_current_thread()
            AdditionEngine().add(5.0, 6.0)

        thread = threading.Thread(target=agent_thread_entry, name="agent-thread")
        thread.start()
        thread.join()

        assert events == []

        AdditionEngine().add(1.0, 1.0)
        assert events == ["MainThread"]
    finally:
        remove_monitoring(installer)


def test_agent_thread_disable_does_not_affect_other_threads():
    events: list[str] = []
    lock = threading.Lock()
    ready = threading.Event()

    def on_start(code, instruction_offset: int) -> None:
        with lock:
            events.append(threading.current_thread().name)

    installer = install_monitoring({_PY_START: on_start})
    try:
        _enable_add_local_events()

        def agent_thread_entry() -> None:
            disable_monitoring_on_current_thread()
            ready.set()

        def calc_thread_entry() -> None:
            ready.wait(timeout=2.0)
            AdditionEngine().add(2.0, 3.0)

        agent = threading.Thread(target=agent_thread_entry, name="agent-thread")
        calc = threading.Thread(target=calc_thread_entry, name="calc-thread")
        agent.start()
        calc.start()
        agent.join()
        calc.join()

        assert events == ["calc-thread"]
    finally:
        remove_monitoring(installer)


def test_reinstall_replaces_callbacks():
    first_calls: list[str] = []
    second_calls: list[str] = []

    def first(code, instruction_offset: int) -> None:
        first_calls.append("first")

    def second(code, instruction_offset: int) -> None:
        second_calls.append("second")

    installer = MonitoringInstaller()
    installer.install({_PY_START: first})
    _enable_add_local_events()
    AdditionEngine().add(1.0, 1.0)
    assert first_calls == ["first"]
    assert not second_calls

    installer.install({_PY_START: second})
    AdditionEngine().add(2.0, 2.0)
    assert first_calls == ["first"]
    assert second_calls == ["second"]
    installer.remove()
