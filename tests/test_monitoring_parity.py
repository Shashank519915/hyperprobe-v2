"""Parity: settrace vs monitoring backends produce equivalent snapshot fields."""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from agent.bootstrap import (
    BACKEND_MONITORING,
    BACKEND_SETTRACE,
    DEFAULT_BREAKPOINTS_PATH,
    start_agent,
)
from target.server import create_server

_CALCULATE_PATH = "/calculate?op=add&a=10&b=20"
_SEED_METHOD_ADD = "seed-method-add"
_SEED_LINE_RETURN = "seed-line-addition-return"


def _wait_for_control(runtime, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and runtime.control_server._server is None:  # noqa: SLF001
        time.sleep(0.01)
    assert runtime.control_server._server is not None  # noqa: SLF001


def _http_get(port: int, path: str) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    return response.status, payload


def _normalize_local_value(value: object) -> object:
    if isinstance(value, str) and " at 0x" in value:
        return value.rsplit(" at 0x", 1)[0] + ">"
    return value


def _target_frames_view(payload: dict) -> list[dict]:
    """Stack frames under target/ only — ignore ephemeral stdlib/socket/thread locals."""
    frames: list[dict] = []
    for frame in payload["stack_frames"]:
        if "target" not in frame["file"].replace("\\", "/"):
            continue
        frames.append(
            {
                "function": frame["function"],
                "qualname": frame.get("qualname"),
                "line": frame["line"],
                "locals": {
                    key: _normalize_local_value(val)
                    for key, val in frame["locals"].items()
                },
            }
        )
    return frames


def _parity_view(payload: dict) -> dict:
    """Comparable snapshot fields — exclude volatile ids, timestamps, stdlib noise."""
    return {
        "breakpoint_id": payload["breakpoint_id"],
        "breakpoint": payload["breakpoint"],
        "capture_mode": payload["capture_mode"],
        "event": payload["event"],
        "return_value": payload.get("return_value"),
        "target_stack": _target_frames_view(payload),
    }


def _collect_add_request_snapshots(
    backend: str,
    snapshots_dir: Path,
) -> dict[str, dict]:
    runtime = start_agent(
        breakpoints_path=DEFAULT_BREAKPOINTS_PATH,
        snapshots_dir=snapshots_dir,
        control_host="127.0.0.1",
        control_port=0,
        backend=backend,
    )
    _wait_for_control(runtime)
    server = create_server("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _http_get(port, _CALCULATE_PATH)
        assert status == 200
        assert body["result"] == 30.0

        deadline = time.time() + 3.0
        files: list[Path] = []
        while time.time() < deadline:
            runtime.capture_queue.join()
            files = list(snapshots_dir.glob("*.json"))
            if files:
                break
            time.sleep(0.05)
        assert files, f"expected snapshots for backend={backend!r}"

        by_id: dict[str, dict] = {}
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            by_id[payload["breakpoint_id"]] = payload
        return by_id
    finally:
        server.shutdown()
        thread.join(timeout=2)
        runtime.shutdown()


def test_calculate_seed_method_add_snapshot_parity_settrace_vs_monitoring(
    tmp_path,
):
    settrace_snaps = _collect_add_request_snapshots(
        BACKEND_SETTRACE,
        tmp_path / "settrace",
    )
    monitoring_snaps = _collect_add_request_snapshots(
        BACKEND_MONITORING,
        tmp_path / "monitoring",
    )

    assert _SEED_METHOD_ADD in settrace_snaps
    assert _SEED_METHOD_ADD in monitoring_snaps
    assert _parity_view(settrace_snaps[_SEED_METHOD_ADD]) == _parity_view(
        monitoring_snaps[_SEED_METHOD_ADD]
    )


def test_calculate_seed_line_return_snapshot_parity_settrace_vs_monitoring(
    tmp_path,
):
    settrace_snaps = _collect_add_request_snapshots(
        BACKEND_SETTRACE,
        tmp_path / "settrace",
    )
    monitoring_snaps = _collect_add_request_snapshots(
        BACKEND_MONITORING,
        tmp_path / "monitoring",
    )

    assert _SEED_LINE_RETURN in settrace_snaps
    assert _SEED_LINE_RETURN in monitoring_snaps
    assert _parity_view(settrace_snaps[_SEED_LINE_RETURN]) == _parity_view(
        monitoring_snaps[_SEED_LINE_RETURN]
    )


def test_calculate_matching_breakpoint_ids_settrace_vs_monitoring(tmp_path):
    settrace_snaps = _collect_add_request_snapshots(
        BACKEND_SETTRACE,
        tmp_path / "settrace",
    )
    monitoring_snaps = _collect_add_request_snapshots(
        BACKEND_MONITORING,
        tmp_path / "monitoring",
    )

    assert set(settrace_snaps) == set(monitoring_snaps)
    assert _SEED_METHOD_ADD in settrace_snaps
    assert _SEED_LINE_RETURN in settrace_snaps
