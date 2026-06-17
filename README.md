# HyperProbe PoC

A runtime instrumentation proof-of-concept. The **target** (`target/`) is a plain HTTP calculator — handler, service, and engine layers, no observability code. The **agent** (`agent/`) attaches from outside at process start, registers breakpoints, and writes JSON snapshots when those breakpoints fire.

Python 3.12 · calculator on `:8080` · agent control API on `:9090`

---

## Quick start

You only need Docker. No local Python install required.

```bash
git clone https://github.com/Shashank519915/hyperprobe.git
cd hyperprobe
docker compose up --build
```

In a second terminal:

```bash
curl "http://localhost:8080/calculate?op=add&a=10&b=20"
```

On Windows PowerShell use `curl.exe` instead of `curl`.

That's it. The seed breakpoints in `breakpoints.yaml` fire immediately. Snapshot JSON files land in `./snapshots/` on your host (bind-mounted from the container), and each snapshot also prints as a JSON line in the compose logs because `EMIT_STDOUT=1` is set.

To stop: `Ctrl+C` in the first terminal, then `docker compose down`.

---

## What the calculator does

`GET /calculate?op=<op>&a=<a>&b=<b>` supports `add`, `sub`, `mul`, and `div`.

```bash
curl "http://localhost:8080/calculate?op=add&a=10&b=20"
# {"op": "add", "a": 10.0, "b": 20.0, "result": 30.0}

curl "http://localhost:8080/calculate?op=div&a=10&b=0"
# 400 — division by zero

curl "http://localhost:8080/calculate?op=pow&a=2&b=3"
# 400 — unsupported operation
```

Every request walks three application layers: `RouteHandler.handle_calculate → MathService.compute → engine`. Which seed breakpoints fire depends on the operation — an `add` request hits all three seed configs; `div` or `pow` only hit the function breakpoint on `compute` (see Viewing snapshots).

---

## Viewing snapshots

After a calculate request, check `./snapshots/` on your host:

```bash
ls ./snapshots/
cat ./snapshots/<uuid>.json
```

Or watch compose logs in the first terminal — each snapshot prints as one JSON line.

**One `add` curl often produces three files** — one per seed breakpoint in `breakpoints.yaml` (`seed-function-compute`, `seed-method-add`, `seed-line-addition-return`). Other operations (e.g. `div`) usually produce one file (function `compute` only).

### Example — method breakpoint (ENTRY)

From `curl ".../calculate?op=add&a=10&b=20"`, file `seed-method-add` (stack trimmed; full files also include stdlib HTTP/thread frames):

```json
{
  "snapshot_id": "0dda58d2-8b3c-446d-938a-d5926bc26bc9",
  "timestamp": "2026-06-17T07:38:10.134155+00:00",
  "breakpoint_id": "seed-method-add",
  "breakpoint": {
    "type": "method",
    "value": "AdditionEngine.add"
  },
  "capture_mode": "ENTRY",
  "event": "call",
  "thread_id": 129653133207232,
  "return_value": null,
  "stack_frames": [
    {
      "index": 0,
      "function": "add",
      "qualname": "AdditionEngine.add",
      "file": "/app/target/engines/addition.py",
      "line": 4,
      "locals": { "a": 10.0, "b": 20.0 }
    },
    {
      "index": 1,
      "function": "compute",
      "qualname": "MathService.compute",
      "file": "/app/target/services/math_service.py",
      "line": 19,
      "locals": { "op": "add", "a": 10.0, "b": 20.0 }
    },
    {
      "index": 2,
      "function": "handle_calculate",
      "qualname": "RouteHandler.handle_calculate",
      "file": "/app/target/handlers.py",
      "line": 14,
      "locals": {
        "query_string": "op=add&a=10&b=20",
        "op": "add",
        "a": 10.0,
        "b": 20.0
      }
    },
    {
      "index": 3,
      "function": "do_GET",
      "qualname": "_CalculatorHTTPRequestHandler.do_GET",
      "file": "/app/target/server.py",
      "line": 30,
      "locals": { "parsed": ["", "", "/calculate", "", "op=add&a=10&b=20", ""] }
    }
  ]
}
```

Index 0 is the innermost frame (where the breakpoint hit). Indices 0–2 are the three calculator layers; index 3 is the HTTP handler in `server.py`.

### Example — file+line breakpoint (RETURN)

Same request, third snapshot — RETURN capture on `addition.py:5` with the computed result:

```json
{
  "snapshot_id": "aeebf59f-d811-4d45-b7e3-a6a142134116",
  "breakpoint_id": "seed-line-addition-return",
  "capture_mode": "RETURN",
  "event": "return",
  "return_value": 30.0,
  "stack_frames": [
    {
      "index": 0,
      "function": "add",
      "qualname": "AdditionEngine.add",
      "file": "/app/target/engines/addition.py",
      "line": 5,
      "locals": { "a": 10.0, "b": 20.0 }
    }
  ]
}
```

(`stack_frames` truncated here; the on-disk file includes the full caller chain like the ENTRY example above.)

---

## Runtime breakpoint demo

Seed breakpoints from `breakpoints.yaml` load at startup. You can register new ones without restarting:

```bash
# see what's already registered
curl http://localhost:9090/breakpoints

# register a new one
curl -X POST http://localhost:9090/breakpoints \
  -H "Content-Type: application/json" \
  -d '{"type":"method","value":"AdditionEngine.add","capture_mode":"ENTRY"}'

# trigger it
curl "http://localhost:8080/calculate?op=add&a=5&b=7"
```

A new snapshot file appears immediately. No restart. The tracer picks it up on the next matching call.

Three breakpoint types are supported:

| type | matches on | example |
|---|---|---|
| `function` | any function named `value` | `"value": "compute"` |
| `method` | exact class.method qualname | `"value": "AdditionEngine.add"` |
| `file_line` | specific file + line number | `"file": "target/engines/addition.py", "line": 5` |

Capture modes: `ENTRY` (on call), `RETURN` (on return, includes return value), `BOTH`.

The POST returns 201 with an assigned ID. 400 on bad input.

---

## How the instrumentation works

The container entrypoint is `python -m agent.bootstrap`, not `python -m target.server`. Bootstrap starts everything in order: it loads `breakpoints.yaml` into an in-memory registry, starts a background worker thread, installs `sys.settrace` on the main thread and `threading.settrace` so that calculator request threads inherit tracing, and then starts the agent control server on port 9090. Only after all of that does it import and start the calculator server on port 8080. The target never imports the agent — bootstrap is the only place both sides are wired together.

When the calculator handles a request, the interpreter fires trace events on every function call. The global trace callback checks the function name and qualname against the registry in O(1) using indexed sets. If there's no match it returns None immediately, costing almost nothing. On a match it copies the frame's locals with `dict(f_locals)` and walks the `f_back` chain to capture every caller's locals too — all of this happens synchronously inside the callback, before the callback returns, because frame objects are only valid during the trace event. That copied data goes into a `RawCapture` dataclass and is put onto a bounded queue with `put_nowait`. A separate SnapshotWorker thread picks it up, runs it through a safe JSON serializer (handles cycles, deep nesting, callables), and writes the file. The request thread never touches I/O.

For RETURN and file+line breakpoints, a scoped local trace function is installed on the matched frame at call time. The global trace only handles `'call'` events — it never processes `'line'` or `'return'` globally, which is the main thing that keeps overhead reasonable.

---

## Architecture decisions

**Same-process attachment via bootstrap** — the assignment says instrumentation must happen via runtime or native tooling APIs without modifying target source. Launching the target under an external entrypoint is the same pattern as `python -m pdb -m myapp` or `python -m coverage run myapp`. The target modules are never edited; `target/` can be run standalone with `python -m target.server` and works fine.

**Two-tier trace model** — if you enable `'line'` events globally, every single line in every traced thread fires a callback. That's roughly a 50–70% throughput hit. The fix is to only install line-level tracing on frames in files that have active file+line breakpoints. The global callback stays cheap; line overhead is proportional to code in watched files only.

**Sync capture, async write** — locals must be copied inside the trace callback. `f_locals` is a materialized dict that CPython invalidates after the frame unwinds, so you can't defer the read. What you can defer is JSON serialization and file I/O, both of which are milliseconds. The queue decouples these.

**Bounded loss-tolerant queue** — the queue has a hard cap of 1000 items. Under sustained load, snapshots are dropped silently (with a rate-limited warning to agent stderr). The calculator always keeps serving. Correctness of the target wins over completeness of observability.

---

## Limitations

`sys.settrace` fires a callback on every function call in traced threads. For this PoC that's acceptable — the two-tier design limits line-event overhead, and the async worker keeps serialization off the request path. But for a high-traffic production service you'd want to move to `sys.monitoring` (PEP 669, Python 3.12+), which lets you subscribe to specific event sets and activate monitoring per code object rather than process-wide. That would be the first production upgrade.

This PoC uses same-process attachment only. True out-of-process attach (like the Datadog agent or a ptrace-based tool) is a different architecture and out of scope here.

Shallow locals copy: `dict(f_locals)` copies names to values by reference. For the calculator's floats and strings this is fine. For deeply mutable objects in production you'd document the edge case or selectively deep-copy.

The control API on port 9090 has no authentication. Fine for localhost; would need TLS and auth before production.

---

## Running without Docker

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

pytest tests/ -q                  # 163 tests
python scripts/target_purity_check.py   # purity check

python -m agent.bootstrap         # starts both servers (default: settrace)
```

**HyperProbe v2 — monitoring backend (optional):**

```powershell
$env:HYPERPROBE_BACKEND = "monitoring"
python -m agent.bootstrap
```

Docker Compose in this repo defaults to `HYPERPROBE_BACKEND=monitoring`. Set `settrace` in `docker-compose.yml` to use the original `sys.settrace` path.

---

## Repository layout

```
agent/             tracer, capture, worker, control API, bootstrap
target/            pristine calculator — handler, service, engines
tests/             163 pytest cases
Dockerfile         python:3.12-slim, ENTRYPOINT agent.bootstrap
docker-compose.yml ports 8080 + 9090, snapshot volume, EMIT_STDOUT
breakpoints.yaml   seed breakpoints (function, method, file+line)
COMPLIANCE_CHECKLIST.md   requirement-to-test mapping (R1–R34)
scripts/           target purity check
```

CI runs pytest, target purity scan, and `docker compose build` on every PR.