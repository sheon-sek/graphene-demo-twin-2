# Decisions

- Do not copy old OPC item paths or `now()` simulator expressions.
- Preserve conflicting explicit metadata and flag it in normalization review instead of guessing.
- Generate thousands of point contracts and deterministic sources; do not hand-code tags.
- Keep runtime state in one process and bound logs/events to avoid unbounded growth.
- Use path-derived stable signal keys with SHA-1 suffixes; physical noise uses SHA-256(seed, signalKey, UTC bucket).
