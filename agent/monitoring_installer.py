"""Install and remove PEP 669 sys.monitoring hooks (v2 backend)."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Mapping
from typing import Any

# Single shared tool slot for the HyperProbe agent (PEP 669 IDs 0–5).
HYPERPROBE_TOOL_ID = sys.monitoring.DEBUGGER_ID
HYPERPROBE_TOOL_NAME = "hyperprobe"

MonitoringCallback = Callable[..., object | None]

_thread_state = threading.local()


def is_monitoring_disabled_on_current_thread() -> bool:
    """Return whether monitoring callbacks should no-op on this thread."""
    return bool(getattr(_thread_state, "disabled", False))


def disable_monitoring_on_current_thread() -> None:
    """Agent-owned threads must not run monitoring callbacks (R24 parity).

    Unlike ``sys.settrace(None)``, PEP 669 has no per-thread VM disable.
    Agent threads call this at entry; registered callbacks are wrapped to
    respect the thread-local flag without affecting calculator threads.
    """
    _thread_state.disabled = True


def _wrap_callback(callback: MonitoringCallback) -> MonitoringCallback:
    def wrapped(*args: Any) -> object | None:
        if is_monitoring_disabled_on_current_thread():
            return None
        return callback(*args)

    return wrapped


class MonitoringInstaller:
    """Manage HyperProbe's shared sys.monitoring tool_id and callbacks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._installed = False
        self._callbacks: dict[int, MonitoringCallback] = {}

    @property
    def is_installed(self) -> bool:
        with self._lock:
            return self._installed

    @property
    def tool_id(self) -> int:
        return HYPERPROBE_TOOL_ID

    @property
    def callbacks(self) -> dict[int, MonitoringCallback]:
        with self._lock:
            return dict(self._callbacks)

    def install(self, callbacks: Mapping[int, MonitoringCallback]) -> None:
        if not callbacks:
            raise ValueError("callbacks must not be empty")
        for event, callback in callbacks.items():
            if callback is None:
                raise ValueError("callback must not be None")
        with self._lock:
            if not self._installed:
                sys.monitoring.use_tool_id(HYPERPROBE_TOOL_ID, HYPERPROBE_TOOL_NAME)
            for event, callback in callbacks.items():
                wrapped = _wrap_callback(callback)
                sys.monitoring.register_callback(
                    HYPERPROBE_TOOL_ID,
                    event,
                    wrapped,
                )
                self._callbacks[event] = callback
            self._installed = True

    def remove(self) -> None:
        with self._lock:
            if not self._installed:
                return
            sys.monitoring.free_tool_id(HYPERPROBE_TOOL_ID)
            self._callbacks.clear()
            self._installed = False


def install_monitoring(
    callbacks: Mapping[int, MonitoringCallback],
) -> MonitoringInstaller:
    """Claim the HyperProbe tool_id and register monitoring callbacks."""
    installer = MonitoringInstaller()
    installer.install(callbacks)
    return installer


def remove_monitoring(installer: MonitoringInstaller) -> None:
    """Release monitoring installed by ``install_monitoring``."""
    installer.remove()
