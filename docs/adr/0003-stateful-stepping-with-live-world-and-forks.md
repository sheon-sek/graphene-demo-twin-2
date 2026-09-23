# Stateful fixed-step simulation, one wall-clock Live World, What-if Forks

v1 computed world state as a pure function of (timestamp, seed, active faults) so any instant was random-access. That made thermal inertia, tank levels, battery and fuel state, and energy integrals expensive: each one had to be re-integrated from scratch, which caused unbounded energy timelines (#8). It also let reset diverge from a restart (#6). v2 advances a stateful world in fixed 1 s steps from a steady-state initial state. The trajectory is fully determined by the seed plus an Event Log of operator actions. Exactly one Live World runs, locked to wall-clock time at 1x, and it is the only world exposed over OPC UA, so SourceTimestamps stay monotonic and sane for the Ignition historian. Pause, acceleration, replay and Fault Preview happen only in What-if Forks: copies of the Live World's state advanced independently and visible only in the Operator Console.

## Consequences

- Reset rebuilds from the initial state and is indistinguishable from a restart.
- The Event Log is not persisted across restarts; a Golden Demo is a pre-authored Event Log.
- Fault Preview cannot disagree with reality, because it runs the same simulation.
