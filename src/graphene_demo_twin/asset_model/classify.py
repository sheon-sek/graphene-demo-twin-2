"""Support scope and source class of each Point.

Both are judgements about the export, not facts in it, so they live here as reviewable rules
rather than being scattered through the engine.
"""

import re
from enum import StrEnum


class SourceClass(StrEnum):
    """What kind of evidence a Point carries, and so what must drive its value."""

    COMMAND = "command"
    """Controller output, setpoint or operator-set control parameter."""
    FEEDBACK = "feedback"
    """Equipment response to a command: run status, valve position, drive speed."""
    PROCESS_VALUE = "process_value"
    """Measured or computed physical quantity."""
    EQUIPMENT_STATE = "equipment_state"
    """Mode, sequence or availability state of equipment or a control loop."""
    FAULT_ALARM = "fault_alarm"
    """Fault, trip, alarm bit or alarm summary, raised by device-side logic."""
    ENERGY_INTEGRAL = "energy_integral"
    """Value accumulated over time (energy, run hours, totalizers); stateful across steps."""
    NETWORK_STATE = "network_state"
    """Control-network link, port, device health or communication status."""
    STATIC_METADATA = "static_metadata"
    """Names, descriptions, identifiers and configuration constants."""
    SUPPORT = "support"
    """Any point of a Support Asset."""


SUPPORT_SCOPES: tuple[str, ...] = (
    "Smart Alarm Logic",
    "Testing",
    "Meter/decoder1",
    "Breaker",
    "Line",
    "Temperature_Controls",
    "Level_Monitoring",
    "Pressure System",
    "MQTT Tags",
    "PredictionCache",
    "Device Card Abbreviation",
)
"""Export paths (anchored at the root) whose Assets and points are Support Assets.

Root `Breaker` and `Line` are not the Demo Rack's `DemoRack/Breaker*` instances.
"""

SUPPORT_TYPES: tuple[str, ...] = ("Dashboard",)
"""UDT types whose root instances are Support Assets.

Only the exact type: `CDU` inherits from `Dashboard` but is equipment.
"""


def in_support_scope(path: str) -> bool:
    return any(path == scope or path.startswith(f"{scope}/") for scope in SUPPORT_SCOPES)


_NETWORK_TYPES = frozenset({"Network Device", "Network Switch", "Network Switch Port"})


def _names(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_STATIC_METADATA = _names(
    r"^(Equipment Name|Description|Rack ID|_Name|Outgoing No|Cable Length)$",
    r"_RackID$",
    r"^GEM630CTL_",
    r"^Maintenance Schedule$",
)
_ENERGY_INTEGRAL = _names(
    r"Wh_Im",
    r"Energy",
    r"Totalizer",
    r"Run Hours|Operating Hours|Engine Run Time",
    r"^System Start Times$",
    r"^Rotation Count$",
)
_NETWORK_STATE = _names(r"^Comm$", r"^Gateway \d+ Status$")
_FAULT_ALARM = _names(
    r"(?i:alarm|fault|fail|trip|shutdown|warning|problem)",
    r"^(EF|Emergency Stop|Low Coolant Level)$",
    r"^(No CT|Short CT|PE Connection|Transformer Temp)$",
)
_FAULT_ALARM_SCOPES = ("Fire Protection System/", "Chiller System Control/Alarms/")
_EQUIPMENT_STATE_FIRST = _names(r"^Staging Command Pending$", r"Mode Status$")
_COMMAND = _names(
    r"Auto_Manual",
    r"Command",
    r"Setpoint|\(SP\)| SP$",
    r"^(Enabled|Loop Enable)$",
    r"^(Manual Output|Control Variable)$",
    r"Speed Control$|Valve Control$",
    r"^Run_Stop",
    r"^Engine Start$",
    r"^(Chiller Load Limit|Pump Minimum Speed|Demo Cooling Demand)$",
    r"^(Minimum|Maximum) Chillers$",
    r"^Minimum (DP|Flow Rate)$",
    r"^Stage (Up|Down)( Inhibit)? Wait Time$",
    r"^(Staging Strategy|Selected Lead)$",
)
_FEEDBACK = _names(
    r"Feedback",
    r"(Open|Close) Status$",
    r"On_?Off",
    r"Run(ning)? Status$",
    r"^Position$",
    r"Fan Speed$",
    r"Compressor( 2)? Capacity$",
    r"^Output Frequency$",
)
_DRIVE_FEEDBACK_SCOPES = ("Chiller System Control/Pumps/", "Chiller_System/Chillers/")
_EQUIPMENT_STATE = _names(
    r"Status|State|Mode$",
    r"^Ready To ",
    r"^(Idling|Operation|System Normal|Maintenance Due)$",
    r"^Chiller Buffer Tank Recharge$",
    r"^(Running|Required) Chillers$",
    r"^(Running Available|Next To Start|Next To Stop)$",
    r"^(Current Lead|Last Rotation|Last Scheduled Key)$",
    r"^(Direction|Lift Level|Moving Until)$",
)


def classify(
    *,
    path: str,
    name: str,
    type_id: str | None,
    data_type: str,
    value_source: str,
    eng_unit: str | None,
    support: bool,
) -> SourceClass:
    """First matching rule wins; anything unmatched is a process value."""
    if support:
        return SourceClass.SUPPORT
    if data_type in ("DataSet", "Document") or _STATIC_METADATA.search(name):
        return SourceClass.STATIC_METADATA
    if path.startswith("Chiller System Control/Rotation Schedule/") and name == "Time":
        return SourceClass.STATIC_METADATA
    if (type_id in _NETWORK_TYPES and name != "Temperature") or _NETWORK_STATE.search(name):
        return SourceClass.NETWORK_STATE
    if eng_unit == "kWh" or _ENERGY_INTEGRAL.search(name):
        return SourceClass.ENERGY_INTEGRAL
    if path.startswith(_FAULT_ALARM_SCOPES) or _FAULT_ALARM.search(name):
        return SourceClass.FAULT_ALARM
    if type_id == "Water Leak Cable Sensor" and name == "Status":
        return SourceClass.FAULT_ALARM
    if _EQUIPMENT_STATE_FIRST.search(name):
        return SourceClass.EQUIPMENT_STATE
    if _COMMAND.search(name) or "/Commands/" in path:
        return SourceClass.COMMAND
    if name.endswith("Mode") and value_source != "expr":
        return SourceClass.COMMAND
    if _FEEDBACK.search(name):
        return SourceClass.FEEDBACK
    if name in ("Speed", "Frequency") and path.startswith(_DRIVE_FEEDBACK_SCOPES):
        return SourceClass.FEEDBACK
    if type_id == "Cooling Tower" and name == "Frequency":
        return SourceClass.FEEDBACK
    if _EQUIPMENT_STATE.search(name):
        return SourceClass.EQUIPMENT_STATE
    return SourceClass.PROCESS_VALUE
