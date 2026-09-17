# Fault Injection and Ground-Truth Verification

Fault Injection uses exact topology assets and the same deterministic solver as normal runtime telemetry.

## API flow

1. `GET /api/faults/catalog` returns each recipe plus every exact eligible asset.
2. `POST /api/faults/preview` evaluates a hypothetical fault without mutating runtime state.
3. `POST /api/faults/inject` stores the injected fault and returns its immediate marginal impact report.
4. `GET /api/faults/{injectionId}/impact` re-evaluates that active fault's marginal impact at the current simulation timestamp.
5. `POST /api/faults/{injectionId}/reset` removes one injected fault.
6. `POST /api/faults/reset-all` removes all operator-injected faults. Scripted scenario faults are unaffected.

Impact comparison excludes raw developer overrides and reports:

- whether any authoritative value/quality/network balance changed;
- changed Graphene points with before/after/delta and quality;
- affected topology assets;
- site-level changes;
- `NetworkBalance` changes;
- solver constraints applied by physical recipes.

The comparison is marginal against the current world: scripted faults and other active injections remain in both sides of the comparison. This prevents an unrelated active scenario from being incorrectly attributed to the selected injection.

## Reset semantics

Runtime clock reset and fault reset are intentionally separate operations. Clock reset does not clear injected faults or raw overrides. Fault reset removes only the selected operator injection; resetting all injected faults does not remove scripted demo faults.
