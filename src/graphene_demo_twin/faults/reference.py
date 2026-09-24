"""The fault catalog reference (docs/fault-catalog.md), rendered from the catalog itself.

    .venv/bin/python -m graphene_demo_twin.faults.reference > docs/fault-catalog.md

A test keeps the checked-in document equal to what this renders.
"""

from collections import defaultdict

from graphene_demo_twin.asset_model import load_asset_model
from graphene_demo_twin.faults.catalog import STANDARD_CATALOG, FaultCatalog, FaultSpec
from graphene_demo_twin.plant_design import ConnectionKind, PlantDesign, load_plant_design

PREAMBLE = """\
# Fault catalog reference

Generated from `graphene_demo_twin.faults.catalog` by
`python -m graphene_demo_twin.faults.reference`; do not edit by hand.

A **Fault** is a named failure mechanism bound to an asset type. It acts only through its
mechanism: a **Physical Constraint** the simulation consumes (equipment and external
faults), an observation corruption (sensor faults), a quality change (communication faults)
or Controller misbehaviour (control faults). No fault ever writes an alarm point. Alarm bits
come from device-side logic that reads the state the fault changed.

Every fault takes the same parameters (`POST /api/faults`):

| Parameter | Meaning |
|---|---|
| `severity` | Level reached after onset, in (0, 1]; the fault's variable is `level × span`. |
| `ramp_min` | Onset: 0 is a step, otherwise a linear ramp to `severity` over this many minutes. |
| `auto_clear_min` | Duration: absent acts until Clear, otherwise the fault clears itself. |

**Targets** is the number of Plant Design assets the fault can be injected on. A fault
targets exactly the asset chosen, never a room or another asset of the type. **Spreads
along** lists the connection kinds its effect travels downstream along, which is the path a
Fault Preview and the console's propagation highlight follow.
"""


def render(catalog: FaultCatalog, design: PlantDesign) -> str:
    by_type: dict[str, list[FaultSpec]] = defaultdict(list)
    for spec in catalog:
        by_type[spec.asset_type].append(spec)
    lines = [PREAMBLE]
    total = sum(len(v) for v in by_type.values())
    lines.append(f"{total} faults on {len(by_type)} asset types.\n")
    for type_id in sorted(by_type):
        lines.append(f"## {type_id}\n")
        for spec in by_type[type_id]:
            targets = spec.targets(design)
            spreads = ", ".join(
                k.value for k in ConnectionKind if k in spec.mechanism.spreads_along
            )
            lines.append(f"### `{spec.id}`: {spec.name}\n")
            lines.append(spec.description + "\n")
            lines.append(
                "| Category | Mechanism | Variable | At severity 1 | Default severity "
                "| Targets | Spreads along |"
            )
            lines.append("|---|---|---|---|---|---|---|")
            lines.append(
                f"| {spec.category.value} | {spec.mechanism.value} | `{spec.variable}` "
                f"| {spec.span:g} {spec.unit} | {spec.default_severity:g} | {len(targets)} "
                f"| {spreads or 'nothing (the world is unchanged)'} |\n"
            )
    return "\n".join(lines)


def main() -> None:
    asset_model = load_asset_model()
    print(render(STANDARD_CATALOG, load_plant_design(asset_model)), end="")


if __name__ == "__main__":
    main()
