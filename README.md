# HyperProbe v2 PoC

A runtime instrumentation proof-of-concept. The **target** (`target/`) is a plain HTTP calculator - handler, service, and engine layers, no observability code. The **agent** (`agent/`) attaches from outside at process start, registers breakpoints, and writes JSON snapshots when those breakpoints fire.

Python 3.12 · calculator on `:8080` · agent control API on `:9090`

---

## Quick start

You only need Docker. No local Python install required. Install Docker Desktop Signin/Signup. Keep the Docker Engine running in Docker Desktop.

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

That's it. The seed breakpoints in `breakpoints.yaml` fire immediately on that request. Snapshot JSON files land in `./snapshots/` on your host (bind-mounted from the container), and each snapshot also prints as a JSON line in the compose logs because `EMIT_STDOUT=1` is set, which can be seen in Docker Deskto app and on terminal 1 to wehere we ran `docker compose up --build`.

To stop testing/running: `Ctrl+C` in the first terminal, then `docker compose down`.

---

## What the calculator does

`GET /calculate?op=<op>&a=<a>&b=<b>` supports `add`, `sub`, `mul`, and `div`.

```bash
curl "http://localhost:8080/calculate?op=add&a=10&b=20"
# {"op": "add", "a": 10.0, "b": 20.0, "result": 30.0}

curl "http://localhost:8080/calculate?op=div&a=10&b=0"
# 400 - division by zero

curl "http://localhost:8080/calculate?op=pow&a=2&b=3"
# 400 - unsupported operation
```

Every request goes through 3 application layers:

do_GET (target/server.py) → handle_calculate (target/handlers.py) → MathService.compute (target/services/math_service.py) → engine e.g. AdditionEngine.add (target/engines/addition.py)
An `add` request hits all three seed breakpoints. div,mul,sub / unsupported ops only hit the function breakpoint on compute as set in the breakpoint.yaml essentially.

---

## Viewing snapshots

After a calculate request, simply open the `/snapshots` folder and view teh JSON Snapshots easily.

or check `./snapshots/` on your host:

```bash
ls ./snapshots/
cat ./snapshots/<uuid>.json
```

Or watch compose logs in the first terminal and Docker Desktop logs.

**One `add` curl often produces three files** - one per seed breakpoint in `breakpoints.yaml` (`seed-function-compute`, `seed-method-add`, `seed-line-addition-return`). Other operations (e.g. `div`) usually produce one file (function `compute` only).

### Real Example - method breakpoint (ENTRY)

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

Index 0 is the innermost frame (where the breakpoint hits). Indices 0–2 are the three calculator layers; index 3 is the HTTP handler in `server.py`. We can clearly see the local variables and lnes of code of execution count captures.

### Example - file+line breakpoint (RETURN)

Same request, third snapshot - RETURN capture on `addition.py:5` with the computed result:

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

Three breakpoint types are supported:
`function` matches on -> any function named `value` -> like `"value": "compute"`
`method`matches on -> exact class.method qualname -> like `"value": "AdditionEngine.add"` 
`file_line` matches on -> specific file + line number -> like `"file": "target/engines/addition.py", "line": 5` 

Capture modes: `ENTRY` (on call), `RETURN` (on return, includes return value), `BOTH`.

The POST returns 201 with an assigned ID. 400 on bad input or input whihc returns bad output.

---

## How the instrumentation works

The calculator never imports the agent. There is no logging or tracing code in `target/` (the HTTP calculator). Snapshots happen because we launch through the agent and Python gives us runtime execution hooks. We copy stack state during execution, hand it off through a bounded queue, and a background worker serializes snapshots into JSON files.

The container entrypoint is `python -m agent.bootstrap`, not `python -m target.server`.
Bootstrap starts everything in order:
1. It loads `breakpoints.yaml` into an in-memory registry (`BreakpointRegistry`)
2. Starts a background worker thread (`SnapshotWorker`) backed by a bounded queue
3. Chooses an instrumentation backend using `HYPERPROBE_BACKEND`
   * `monitoring` (Docker default): installs `MonitoringTracer`, calls `install_monitoring()`, and activates global events
   * `settrace`: installs the legacy trace backend through `install_trace(global_trace)`
4. Starts the agent control server on port 9090
5. Only after all of that does it import and start the calculator server (`create_server().serve_forever()`) on port 8080
The target never imports the agent, the bootstrap is the only place both sides are wired together. Only one backend is installed at a time because running both would generate duplicate events.

When the calculator handles a request, the interpreter fires execution events on every function call.

Inside Global Hook:

For the monitoring backend, Python emits a `PY_START` event when a function begins execution. `MonitoringTracer.on_py_start()` checks the function name and qualname against the `BreakpointRegistry` in O(1) using indexed sets. If there's no breakpoint match, it returns immediately, costing almost nothing. On a match it copies the frame's locals with `dict(f_locals)` and walks the `f_back` chain to capture every caller's locals too - all of this happens synchronously inside the callback, before the callback returns, because frame objects are only valid during the execution event. That copied data goes into a `RawCapture` dataclass and is put onto a bounded queue with `put_nowait`. A separate `SnapshotWorker` thread picks it up, runs it through snapshot building and JSON serialization, and writes the file in `/snapshots`. The request thread never touches I/O.

The settrace backend follows the same flow after a match. The only difference is the entry event source: it uses `global_trace(frame, "call")` instead of `on_py_start()`. Parity tests in `tests/test_monitoring_parity.py` verify that both backends produce equivalent snapshot fields for the same request.

For RETURN and file+line breakpoints, a scoped local event handler is installed only on matched frames. The global hook only handles function entry events - it never processes line or return events across the entire application, which is the main thing that keeps overhead reasonable. In the monitoring backend this is done through `set_local_events()`, while the settrace backend installs a local trace function on the matched frame.

Frame objects never cross thread boundaries. Live execution happens on the HTTP request thread, `capture_raw()` copies the required state into a `RawCapture`, and only that copied data is transferred through the queue. The worker thread then converts it into a JSON snapshot and writes it to disk. Copy in the callback, write in the worker. This is what keeps instrumentation non-halting and prevents request execution from blocking on I/O.


---

## Architecture decisions


1) The assignment says instrumentation must happen via runtime or native tooling APIs without modifying target source. Launching the target under an external entrypoint is the same pattern as `python -m pdb -m myapp`. The target modules are never edited; `target/` can be run standalone with `python -m target.server` and works fine. The monitoring backend follows the same bootstrap attach model.

2) The monitoring and settrace backends use the same capture pipeline as we have in (https://github.com/Shashank519915/hyperprobe). Only the instrumentation layer differs (`MonitoringTracer` vs `Tracer` and their installers). Capture, queueing, snapshot generation, and file writing are shared code paths.

3) Sync capture, async write - locals must be copied inside the instrumentation callback. basically copy in callback; serialize/write in worker.

4) The queue has a hard cap of 1000 items. Under sustained load, snapshots are dropped silently (with a rate-limited warning to agent stderr). The calculator always keeps serving.

5) Monitoring and settrace are parity-tested. The repository currently has 197 passing tests, including monitoring backend, concurrency, bootstrap, and parity verification suites.

---

## Limitations

The control API on port 9090 has no authentication. Fine for localhost; would need TLS and auth before production.

---

## Running without Docker

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

pytest tests/ -q                  # 197 tests
python scripts/target_purity_check.py

python -m agent.bootstrap         # settrace if env unset; set HYPERPROBE_BACKEND for monitoring
```


Docker Compose in this repo defaults to `HYPERPROBE_BACKEND=monitoring`. Set `settrace` in `docker-compose.yml` to use the original `sys.settrace` path.

---

## Repository layout

```
agent/             tracer, monitoring_tracer, monitoring_installer, capture, worker, bootstrap
target/            pristine calculator (unchanged from submission)
tests/             197 pytest cases (includes monitoring parity + concurrency)
docker-compose.yml monitoring backend by default
breakpoints.yaml   seed breakpoints
COMPLIANCE_CHECKLIST.md   R1–R34 evidence + v2 extensions
scripts/           target purity check
```

CI runs pytest, target purity scan, and `docker compose build` on every PR.



## Difference in this repo vs previous


DIFFERENT (hook only) :                                         
  settrace                  │  sys.monitoring                    
  sys.settrace(fn)          │  use_tool_id + register_callback   
  threading.settrace(fn)    │  set_events(PY_START|PY_RETURN)    
  global_trace(frame,event) │  on_py_start(code) → get frame     
  local trace fn for lines  │  set_local_events on __code__      
  disable: settrace(None)   │  disable: thread-local wrapper    

IDENTICAL (everything after a hit)  :                           
  breakpoints.yaml → BreakpointRegistry → match → capture_raw → queue.put_nowait → SnapshotWorker → snapshots/*.json         
