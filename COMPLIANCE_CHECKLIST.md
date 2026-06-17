# Compliance Checklist — HyperProbe PoC

Maps assignment requirements **R1–R34** to automated tests, CI jobs, or manual verification steps.  
Design reference: `notes/ARCHITECTURE_V2.md` · Implementation: `notes/IMPLEMENTATION_PLAN.md`

| Repo | Remote | This doc applies to |
|------|--------|---------------------|
| **hyperprobev2** (this folder) | `origin` → hyperprobe-v2.git | v1 requirements + v2 monitoring extensions |
| **hyperprobe** (submission) | `submission` → hyperprobe.git | R1–R34 for assignment submit — README (R33) still pending there |

**Last verified (hyperprobev2):** 2026-06-18 · **pytest:** 197 passed · **Branch:** `test/monitoring-parity` (PR-18)

---
## Quick verification

```powershell
# Full automated suite + target purity (matches CI)
pytest tests/ -q
bash scripts/check_target_purity.sh

# PR-12 compliance test files
pytest tests/test_capture_lifetime.py tests/test_tracer_tiers.py `
  tests/test_multiple_matching_breakpoints.py tests/test_queue_overflow.py `
  tests/test_file_line_bp.py -q

# Integration + concurrency (R1, R13, R25)
pytest tests/test_integration.py tests/test_concurrency.py -q

# v2 monitoring (PR-16–18) — optional on submission repo
pytest tests/test_monitoring_spike.py tests/test_monitoring_installer.py `
  tests/test_monitoring_tracer.py tests/test_monitoring_parity.py -q
```

**Manual demos** (see `notes/DEMO_COMMANDS.md`):

- Docker one-command startup (R32): `docker compose up --build`
- Calculator HTTP (R1): `curl http://localhost:8080/calculate?op=add&a=10&b=20`
- Runtime breakpoint (R25): `curl -X POST http://localhost:9090/breakpoints ...`

---

## Requirement matrix

| ID | Requirement | Evidence | Status |
|----|-------------|----------|--------|
| **R1** | HTTP `GET /calculate?op=add&a=10&b=20` returns JSON result | `tests/test_target_http.py`; `tests/test_bootstrap.py`; `tests/test_integration.py` | ✅ |
| **R2** | ≥3 nested layers (handler → service → engine) visible in stack | `tests/test_bootstrap.py` — snapshot `stack_frames` includes `AdditionEngine.add`; `tests/test_capture.py::test_capture_includes_caller_locals` | ✅ |
| **R3** | Target code: zero logging/tracing/agent imports | CI: `scripts/check_target_purity.sh` → `target_purity_check.py`; `tests/test_target_purity_script.py`; `tests/test_target_http.py::test_target_tree_has_no_agent_imports` | ✅ |
| **R4** | Agent attaches externally (bootstrap + settrace, no target edits) | `agent/bootstrap.py`; `tests/test_bootstrap.py`; v2: `HYPERPROBE_BACKEND=monitoring` same attach model | ✅ |
| **R5** | Dynamic breakpoint: function name (`co_name`) | `tests/test_tracer_global.py`; `tests/test_control_api.py`; `tests/test_control_server.py::test_post_function_breakpoint_returns_201` | ✅ |
| **R6** | Dynamic breakpoint: method (`co_qualname` exact) | `tests/test_tracer_global.py::test_global_trace_method_breakpoint_matches_qualname`; `tests/test_control_server.py::test_post_method_and_file_line_breakpoints` | ✅ |
| **R7** | Dynamic breakpoint: file + line | `tests/test_file_line_bp.py` | ✅ |
| **R8** | Capture current frame locals at hit time | `tests/test_capture.py`; `tests/test_tracer_global.py::test_global_trace_entry_enqueues_raw_capture` | ✅ |
| **R9** | Capture caller locals + full stack walk | `tests/test_capture.py::test_capture_includes_caller_locals`; `tests/test_capture.py::test_capture_stack_innermost_first` | ✅ |
| **R10** | Frame metadata (function, file, line, qualname) | `tests/test_worker.py::test_build_snapshot_includes_breakpoint_and_stack`; snapshot JSON schema in worker tests | ✅ |
| **R11** | Structured JSON snapshot files | `tests/test_worker.py::test_worker_writes_json_file`; `tests/test_bootstrap.py` | ✅ |
| **R12** | Optional stdout via `EMIT_STDOUT` | `tests/test_worker.py::test_worker_emit_stdout`; Docker compose sets `EMIT_STDOUT=1` | ✅ |
| **R13** | Non-halting instrumentation (no debugger pause) | `tests/test_concurrency.py` (settrace + monitoring); tracer/monitoring callbacks return immediately | ✅ |
| **R14** | No modification of target source for observability | `target/` has no agent imports; purity script; agent wired only via bootstrap | ✅ |
| **R15** | Runtime API: `sys.settrace` + `threading.settrace` | `agent/installer.py`; `tests/test_installer.py`; v2: `agent/monitoring_installer.py` (PEP 669, opt-in via env) | ✅ |
| **R16** | Capture modes: ENTRY / RETURN / BOTH | `tests/test_capture_lifetime.py` | ✅ |
| **R17** | Two-tier trace (no global `'line'`/`'return'` events) | `tests/test_tracer_tiers.py` | ✅ |
| **R18** | Combined local trace (function RETURN/BOTH + file_line overlap) | `tests/test_tracer_combined.py` | ✅ |
| **R19** | RawCapture sync copy — never queue live frames | `tests/test_capture_lifetime.py::test_queued_raw_capture_has_no_frame_references`; `tests/test_capture.py::test_raw_capture_contains_only_copied_data` | ✅ |
| **R20** | Multiple BPs on same target → distinct snapshots | `tests/test_multiple_matching_breakpoints.py` | ✅ |
| **R21** | O(1) registry indexes (function/method/file_line) | `tests/test_registry.py` | ✅ |
| **R22** | Path normalization for file_line matching | `tests/test_breakpoints.py`; `tests/test_file_line_bp.py` | ✅ |
| **R23** | Bounded queue, non-blocking enqueue, drop on full | `tests/test_worker.py` (unit); `tests/test_queue_overflow.py` (target safety) | ✅ |
| **R24** | Agent threads disable tracing (`sys.settrace(None)`) | `tests/test_agent_thread_isolation.py`; `tests/test_worker.py`; v2: `disable_monitoring_on_current_thread()` in `tests/test_monitoring_installer.py` | ✅ |
| **R25** | Runtime `POST /breakpoints` without restart | `tests/test_control_api.py`; `tests/test_integration.py::test_runtime_breakpoint_over_http_then_calculate_produces_snapshot` | ✅ |
| **R26** | Runtime `GET /breakpoints` | `tests/test_control_server.py::test_get_breakpoints_lists_registered_items`; `tests/test_bootstrap.py::test_bootstrap_control_api_lists_seed_breakpoints` | ✅ |
| **R27** | POST validation (400 on bad payload) | `tests/test_control_server.py::test_post_missing_required_fields_returns_400`; `test_post_invalid_type_or_capture_mode_returns_400`; `test_post_malformed_json_returns_400` | ✅ |
| **R28** | Optional `id` in POST → server assigns uuid4 | `tests/test_breakpoints_yaml.py::test_breakpoint_from_dict_assigns_uuid_when_id_missing` (same `breakpoint_from_dict` as control API) | ✅ |
| **R29** | YAML seed loads function, method, file_line | `breakpoints.yaml`; `tests/test_breakpoints_yaml.py::test_load_repo_breakpoints_yaml_registers_all_seed_types` | ✅ |
| **R30** | Safe serialization (depth, cycles, callables) | `tests/test_serializer.py` | ✅ |
| **R31** | Error isolation in trace callback and worker | `agent/tracer.py` (BaseException handlers); `tests/test_worker.py::test_worker_continues_after_processing_error` | ✅ |
| **R32** | Docker one-command startup | `Dockerfile`, `docker-compose.yml` (v2 defaults `HYPERPROBE_BACKEND=monitoring`); CI docker job; `learning/SETUP_AND_TEST.md` §16 | ✅ |
| **R33** | Human-written README | `README.md` — **submit on `hyperprobe` repo** (PR-15); v2 README documents monitoring env | ⬜ submission repo |
| **R34** | This compliance checklist in repo | `COMPLIANCE_CHECKLIST.md` (this file) | ✅ |

**Legend:** ✅ automated or CI · ⚠️ partial / manual demo required · ⬜ not yet delivered

---

## PR-12 compliance test index

| File | Requirements |
|------|----------------|
| `tests/test_capture_lifetime.py` | R16, R19 |
| `tests/test_tracer_tiers.py` | R17 |
| `tests/test_multiple_matching_breakpoints.py` | R20 |
| `tests/test_queue_overflow.py` | R23 |
| `tests/test_file_line_bp.py` | R7, R22 |
| `tests/test_integration.py` | R1, R25 |
| `tests/test_concurrency.py` | R13 (settrace + monitoring backends) |
| `tests/test_monitoring_parity.py` | v2 — settrace vs monitoring snapshot equivalence |
| `tests/test_monitoring_tracer.py` | v2 — PEP 669 capture parity with v1 tracer |
| `tests/test_monitoring_installer.py` | v2 — R24 thread isolation for monitoring |
| `tests/test_bootstrap.py` | R1, R4, R11, R25, R26 — both backends |

---

## v2 extensions (not required for submission)

| Area | Evidence | Status |
|------|----------|--------|
| PEP 669 monitoring backend | `agent/monitoring_tracer.py`, `tests/test_monitoring_*.py` | ✅ PR-16–18 |
| Backend env switch | `HYPERPROBE_BACKEND=settrace\|monitoring` in `agent/bootstrap.py` | ✅ |
| Formal parity + concurrency | `tests/test_monitoring_parity.py`, parametrized `test_concurrency.py` | ✅ PR-18 |

---

## Known gaps (follow-up)

| Item | Requirement | Status |
|------|-------------|--------|
| Human-written README | R33 | PR-15 on **`hyperprobe` submission repo** — not blocking v2 experiments |
| deque queue swap (PR-19) | — | **Skip** — see TASK_CHECKLIST § PR-19 analysis |
| import-hook attach (PR-20) | — | **Skip** — see TASK_CHECKLIST § PR-20 analysis |

---

## CI alignment

GitHub Actions workflow `.github/workflows/ci.yml` on every PR/push:

1. `pytest tests/ -q`
2. `bash scripts/check_target_purity.sh`
3. `docker compose build` (second CI job)

Covers automated evidence for R1–R32 on hyperprobev2 (R33 README on submission repo).
