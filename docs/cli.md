# Command line and serving contract

The twin is one deployable process. `graphene-twin serve` (or `python -m graphene_demo_twin serve`) starts the REST/SSE API, the built Operator Console and the OPC UA server together. They all run from one Live World, which starts at the current wall-clock second from its steady-state initial state. There is no separate `opc` command, and no flag to turn a surface off. Both ports are always bound. `tests/test_cli.py` checks this page against the code: the command, the options table, the OPC UA contract and the HTTP table.

```sh
graphene-twin serve                      # http://127.0.0.1:8080 and opc.tcp://127.0.0.1:4840/graphene/twin
graphene-twin serve --host 0.0.0.0 --http-port 9000 --opc-port 4841 --seed 3
```

The process runs until it receives SIGINT or SIGTERM, then stops every surface and exits with status 0. If a port cannot be bound, it exits with status 1 and prints `graphene-twin: cannot serve: <reason>` on stderr.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Interface that both HTTP and OPC UA listen on. Use `0.0.0.0` to reach the twin from an Ignition gateway on the LAN. |
| `--http-port` | `8080` | Port for the REST/SSE API and the Operator Console. |
| `--opc-port` | `4840` | Port for OPC UA. |
| `--seed` | `0` | Seed of the Live World. The same seed and Event Log always replay the same trajectory. |
| `--console-dir` | `apps/web/dist` | Built Operator Console served at `/`. The default is relative to the checkout. When the directory has no `index.html`, `/` serves a placeholder page instead. |

## OPC UA

- Endpoint: `opc.tcp://<host>:<opc-port>/graphene/twin`, with security policy None and anonymous access.
- Namespace URI: `urn:eetarp:graphene:demo:twin`.
- Every Asset Model point is a variable with NodeId `ns=<index>;s=point:<exportPath>` (string identifier `point:<exportPath>`). Its BrowseName is the last path segment, under folders that mirror the export path from `Objects`. Folder NodeIds are `folder:<path>`.
- Data types: Float4 → Float, Float8 → Double, Int4 → Int32, Int8 → Int64, Boolean → Boolean, String → String, DateTime → DateTime. DataSet and Document are carried as JSON text in a String.
- Every variable is read-only, and writes are rejected. Every point is rewritten once per Live World step. Its SourceTimestamp (and ServerTimestamp) is that step's sim time. Its StatusCode comes from the point's quality: Good, Uncertain or Bad.

## HTTP

Every read comes from the latest published frame, which is one Live World step. REST, SSE and OPC UA therefore always show the same sim second. Times are given as `time` (whole epoch seconds) and `timestamp` (ISO 8601 UTC). Point paths in URLs are export paths with each segment percent-encoded.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | Sim time, frame `seq` and `epoch` (the number of Resets), seed, Event Log length, fork count and coverage counts. |
| `GET` | `/api/assets` | Every Asset: typeId, support flag, room, floor, system, role, position and point count. |
| `GET` | `/api/assets/{path}` | One Asset, with its point readings and its direct upstream and downstream nodes by connection kind. |
| `GET` | `/api/points` | Point readings (`value`, `quality`, `timestamp`, `dataType`, `source`). Filter with repeated `path=`, `asset=` or `prefix=`. |
| `GET` | `/api/points/{path}` | One point reading plus its Asset Model metadata and coverage. |
| `GET` | `/api/state` | AssetState for every Plant Design node in the frame, and the active faults. |
| `GET` | `/api/state/{node}` | AssetState for one node: an asset path, `~<name>` or a room id. |
| `GET` | `/api/plant-design` | The Plant Design graph: floors, rooms, service shafts, placed assets, Unexported Assets and connections. |
| `GET` | `/api/events` | The Event Log. |
| `POST` | `/api/events` | Log an operator action `{kind, target, params}` at the current sim time. Returns 201, or 422 if no domain accepts it. |
| `GET` | `/api/faults/catalog` | The fault catalog: id, name, asset type, category, mechanism, driven variable, span, unit, description and default severity. Filter with `type=`. |
| `GET` | `/api/faults` | The active faults in injection order: target, fault, severity, current level, `since`, ramp and auto-clear time (`until`). |
| `POST` | `/api/faults` | Inject `{target, fault, params}` on exactly `target`. `params` is `{severity?, ramp_min?, auto_clear_min?}`: severity in (0, 1], default 1; `ramp_min` 0 (default) is a step onset; `auto_clear_min` null (default) acts until cleared. Logs `fault.inject` and returns 201, 409 if the fault is already active there, or 422 if it does not apply to that asset. |
| `POST` | `/api/faults/clear` | Clear `{target, fault}`. Logs `fault.clear` and returns 201, or 409 if the fault is not active. |
| `POST` | `/api/faults/preview` | Fault Preview of `{target, fault, params, minutes}` with `minutes` 15, 30 or 60. The Live World is untouched. Returns the affected nodes in propagation order, the point diffs at the end, and the alarm bits that change. |
| `GET` | `/api/commands/{path}` | The Operator Commands an asset takes, with their current values. |
| `POST` | `/api/commands` | Give an Operator Command `{target, command, value}`. Logs `command` and returns 201, or 422 if the asset cannot take it. |
| `POST` | `/api/reset` | Reset. Requires `{"confirm": true}`. It discards the Event Log and every fork, and starts a new epoch. |
| `GET` | `/api/coverage` | Coverage counts: `total`, `counts` by source (`physics`, `plant_view`, `fallback`) and `debt` (fallbacks outside Support Assets and static metadata). |
| `GET` | `/api/coverage/points` | The coverage report, one entry per point, with its source class and whether it is an alarm bit (`alarmBit`). Filter with `source=` or `debt=`. |
| `GET` | `/api/forks` | The What-if Forks held, oldest first (at most 8; creating a ninth drops the oldest). |
| `POST` | `/api/forks` | Fork the Live World as it is now. Returns 201. |
| `GET` | `/api/forks/{fork_id}` | A fork's sim time, fork time and Event Log. |
| `DELETE` | `/api/forks/{fork_id}` | Drop a fork. Returns 204. |
| `POST` | `/api/forks/{fork_id}/advance` | Run a fork ahead `{seconds}`, from 1 to 86,400 per call. |
| `POST` | `/api/forks/{fork_id}/events` | Add a hypothetical event `{kind, target, params, at?}` to a fork. `at` defaults to the fork's time. |
| `GET` | `/api/forks/{fork_id}/points` | A fork's projected point readings, with the same filters as `/api/points`. |
| `GET` | `/api/forks/{fork_id}/state` | A fork's AssetState. |
| `GET` | `/api/stream` | Server-Sent Events, one per Live World step. See below. |
| `GET` | `/` | The Operator Console, or a placeholder page when it is not built. |

Interactive API docs are served at `/docs`.

### `/api/stream`

The first event is `snapshot`. After that, each Live World step sends a `delta`. After a Reset, the next event is a fresh `snapshot`. Each event's `id` is the frame `seq`, and its `data` is JSON:

```json
{
  "seq": 42, "epoch": 0, "time": 1790000042, "timestamp": "2026-09-21T14:14:02Z", "events": 1,
  "points": {"<exportPath>": {"value": 24.1, "quality": "good"}},
  "state": {"<node>": {"<variable>": 24.1}},
  "faults": [{"key": "crac.compressor_trip@CRAC/L1_CRAC3", "fault": "crac.compressor_trip", "target": "CRAC/L1_CRAC3", "level": 1.0}]
}
```

A `snapshot` carries every point and every AssetState variable. A `delta` carries only the points and variables that changed since the previous event on the same stream; a variable that no longer exists is sent as `null`. `faults` is always the full list of active faults, as `/api/faults` gives it. A slow client therefore skips intermediate steps but never misses a change. `events` is the Event Log length, so a client can refetch `/api/events` when it changes.
