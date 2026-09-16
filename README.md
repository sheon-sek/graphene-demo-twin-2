# Graphene Virtual World Engine

Deterministic Graphene-compatible data-centre virtual world engine built from the supplied real Graphene exports.

## What is included

- Full parser and inventory for the authoritative UDT and instance exports.
- Generated schema, topology, coverage manifest, normalization review and Signal Registry.
- Deterministic UTC world model with Singapore demo profile and open-world support.
- Shared scripted/runtime fault engine and developer-only raw point overrides.
- FastAPI REST/SSE admin surface.
- Read-only `asyncua` OPC UA projection with stable `point:<exportPath>` NodeIds.
- React + TypeScript + Three.js/R3F industrial operations console source.
- Pytest verification for coverage, determinism, runtime coverage, fault causality and Admin API.

## Quick start

```bash
uv sync
uv run graphene-twin generate-schema
uv run graphene-twin serve --host 127.0.0.1 --port 8080
```

Frontend development:

```bash
cd apps/web
npm install
npm run dev
```

Build the frontend and let FastAPI serve it:

```bash
cd apps/web && npm run build
cd ../..
uv run graphene-twin serve
```

OPC UA:

```bash
uv run graphene-twin opc --endpoint opc.tcp://127.0.0.1:4840/graphene/twin
```

## Important boundaries

The real export hierarchy is not renamed/reparented. Old Gateway OPC connection details and runtime/test values are not reused. `Twin Extensions/` are additive diagnostic points and are explicitly identified as extensions. Production OPC is read-only; all mutation uses Admin API commands.

See `docs/graphene-schema-audit.md` and `config/generated/graphene-coverage-manifest.json` for exact coverage evidence.
