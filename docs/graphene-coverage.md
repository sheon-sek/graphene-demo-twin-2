# Graphene Coverage

`config/generated/graphene-coverage-manifest.json` is generated from the two read-only reference exports. The CI test recomputes the raw AtomicTag count and asserts the manifest contains every exported path exactly once. Extensions live under `Twin Extensions/` and are explicitly marked `origin=twin-extension`.
