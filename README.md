# Graphene Demo Twin

A deterministic virtual datacenter. It simulates equipment, physical networks, control and
faults as one causal world. It then projects that world onto the exact tag structure of our
Ignition/Graphene estate: the frozen Asset Model of 8,741 points. Operators observe and drive
it through a 3D Operator Console, and Ignition and Graphene consume it over OPC UA.

Every fault forms an inspectable chain: **chosen asset + fault → physical or control change →
upstream and downstream response → points, quality and alarm bits change coherently.** An
engineer or an AI can then explain root cause and impact from the evidence alone. The same
seed and Event Log always replay the same trajectory.

## Quick start

```sh
uv sync --extra dev                  # creates .venv and installs the graphene-twin CLI
(cd apps/web && pnpm install && pnpm build)      # optional: the Operator Console

# POSIX (Linux/macOS)
source .venv/bin/activate
graphene-twin serve                  # http://127.0.0.1:8080, opc.tcp://127.0.0.1:4840/graphene/twin

# Windows (PowerShell)
.venv\Scripts\activate
graphene-twin serve                  # same endpoints
```

Open http://127.0.0.1:8080. To see a multi-domain incident, press **Play Golden Demo…** in the
bottom bar. It plays a humid afternoon, a side-A grid failure with genset start, one UPS left
on battery, and a tower fan failure under a high wet bulb. From the API:

```sh
curl -X POST localhost:8080/api/golden-demo/play -H 'content-type: application/json' -d '{"confirm": true}'
curl localhost:8080/api/events/export > story.json          # the Event Log as a document
curl -X POST localhost:8080/api/events/import -H 'content-type: application/json' \
     -d "{\"log\": $(cat story.json), \"confirm\": true}"   # replay it from a fresh start
```

## Documentation

| Read | For |
|---|---|
| `CONTEXT.md` | The domain glossary: Asset Model, Plant Design, Live World, What-if Fork, Fault, Causal Dead End, Golden Demo… |
| `docs/architecture.md` | How the pieces fit: simulation domains, projection, surfaces, the Golden Demo and the zero-fallback audit. |
| `docs/adr/` | The decisions: the frozen Asset Model, the authored Plant Design, stateful stepping with one Live World and forks, and one chiller plant seen through three views. |
| `docs/cli.md` | The `serve` command, the OPC UA contract and every HTTP route. |
| `docs/fault-catalog.md` | Every fault: asset type, category, mechanism, variable, targets and causal path. It is generated from the catalog. |
| `docs/performance.md` | The non-functional targets as measured, and the deferred performance debt. |
| `docs/v1-acceptance.md` | How each v1 issue (#2–#10) maps to a passing v2 acceptance test. |
| `plant-design/README.md` | The authored Plant Design and how to review it. |
| `apps/web/README.md` | The Operator Console. |

## Development

```sh
source .venv/bin/activate               # Windows PowerShell: .venv\Scripts\activate

python -m pytest                        # fast suite (slow tests excluded; CI runs them too)
python -m pytest -m 'slow or not slow'
ruff check . && ruff format --check .
python tools/benchmark.py               # measure the performance targets
python -m graphene_demo_twin.faults.reference > docs/fault-catalog.md
(cd apps/web && pnpm test && pnpm build && pnpm e2e)
```

`tests/test_audit.py` is the zero-fallback gate. CI fails if any production point is a
Compatibility Fallback or a Causal Dead End: a constant, or a setpoint mirrored onto a
measured point.

Issues live in GitHub Issues for `sheon-sek/graphene-demo-twin-2`; see `AGENTS.md`.
