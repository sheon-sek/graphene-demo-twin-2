# Decisions

- Do not copy old OPC item paths or `now()` simulator expressions.
- Preserve conflicting explicit metadata and flag it in normalization review instead of guessing.
- Generate thousands of point contracts and deterministic sources; do not hand-code tags.
- Keep runtime state in one process and bound logs/events to avoid unbounded growth.
- Use path-derived stable signal keys with SHA-1 suffixes; physical noise uses SHA-256(seed, signalKey, UTC bucket).
- Model physical equipment as shared asset state and network balances before Graphene projection. A tag is an observation of world state, not an independent source of physics.
- Translate physical fault recipes into deterministic solver constraints before solving the world. Post-hoc point mutation is retained only as backward-compatible fallback outside the runtime path.
- Use generated topology relations as the propagation graph. Do not infer new runtime connectivity from UI placement or nearest-neighbor distance.
- Keep raw overrides after physics/fault solving so developer overrides cannot silently redefine ground truth.
- Migrate domains incrementally. The cooling-plant slice is the first asset-state/network-balance implementation; deterministic fallback remains explicit for domains not yet migrated.
