"""Event Logs as documents: exported, imported and pre-authored (the Golden Demo).

An Event Log document holds each operator action at an offset in seconds from when the
world was built from its initial state. Playing one against the Live World schedules each
action at that offset from now, so an exported log replays its story from a fresh start.
The same seed, start time and Event Log always give the same trajectory. Weather and IT Load
follow the time of day, so a story played at another time takes the same actions in slightly
different weather.

    {
      "format": "graphene-demo-twin/event-log",
      "version": 1,
      "title": "…", "description": "…",          # optional
      "seed": 0, "start": 1790000000,             # optional: where an export came from
      "events": [
        {"offset": 60, "kind": "fault.inject", "target": "Meter/SPPA Incomer 1",
         "params": {"fault": "utility.incomer_loss"}, "note": "…"}
      ]
    }
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphene_demo_twin.sim import Event, EventError

FORMAT = "graphene-demo-twin/event-log"
VERSION = 1
MAX_OFFSET_S = 7 * 86_400
"""Longest a played Event Log may run ahead of now."""
GOLDEN_DEMO_PATH = Path(__file__).with_name("golden_demo.json")


class EventLogError(EventError):
    """An Event Log document is malformed, or cannot be played against the world."""


@dataclass(frozen=True, slots=True)
class LogEntry:
    offset: int
    """Seconds after the start of the story."""
    kind: str
    target: str
    params: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""
    """What this step of the story shows, for the operator; never part of the event."""

    def event(self, start: int) -> Event:
        return Event(start + self.offset, self.kind, self.target, dict(self.params))


@dataclass(frozen=True, slots=True)
class EventLogDoc:
    entries: tuple[LogEntry, ...]
    title: str = ""
    description: str = ""
    seed: int | None = None
    start: int | None = None

    @property
    def duration_s(self) -> int:
        return max((e.offset for e in self.entries), default=0)

    def to_json(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"format": FORMAT, "version": VERSION}
        if self.title:
            doc["title"] = self.title
        if self.description:
            doc["description"] = self.description
        if self.seed is not None:
            doc["seed"] = self.seed
        if self.start is not None:
            doc["start"] = self.start
        doc["events"] = [_entry_json(e) for e in self.entries]
        return doc


def export_log(events: Iterable[Event], *, seed: int, start: int) -> EventLogDoc:
    """The Event Log of a world built at sim time `start`, as a document."""
    entries = tuple(LogEntry(e.at - start, e.kind, e.target, dict(e.params)) for e in events)
    return EventLogDoc(entries, seed=seed, start=start)


def parse_log(doc: Any) -> EventLogDoc:
    """An Event Log document, checked for shape only; raises EventLogError. Whether its
    actions make sense against the world is for whoever plays it to check."""
    if not isinstance(doc, dict):
        raise EventLogError("an Event Log document is a JSON object")
    if doc.get("format") != FORMAT:
        raise EventLogError(f"not an Event Log document: format must be {FORMAT!r}")
    if doc.get("version") != VERSION:
        raise EventLogError(f"unsupported Event Log version {doc.get('version')!r}")
    raw = doc.get("events")
    if not isinstance(raw, list):
        raise EventLogError("events must be a list")
    entries = []
    for i, e in enumerate(raw):
        where = f"event {i}"
        if not isinstance(e, dict):
            raise EventLogError(f"{where} must be an object")
        if unknown := sorted(e.keys() - {"offset", "at", "kind", "target", "params", "note"}):
            raise EventLogError(f"{where} has unknown fields {unknown}")
        offset = e.get("offset")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise EventLogError(f"{where}: offset must be whole seconds ≥ 0")
        if offset > MAX_OFFSET_S:
            raise EventLogError(f"{where}: offset beyond {MAX_OFFSET_S} s")
        kind, target = e.get("kind"), e.get("target")
        if not isinstance(kind, str) or not isinstance(target, str):
            raise EventLogError(f"{where}: kind and target must be strings")
        params = e.get("params", {})
        if not isinstance(params, dict):
            raise EventLogError(f"{where}: params must be an object")
        note = e.get("note", "")
        if not isinstance(note, str):
            raise EventLogError(f"{where}: note must be a string")
        try:
            Event(offset, kind, target, params)  # scalar params only
        except EventError as err:
            raise EventLogError(f"{where}: {err}") from None
        entries.append(LogEntry(offset, kind, target, params, note))
    offsets = [e.offset for e in entries]
    if offsets != sorted(offsets):
        raise EventLogError("events must be in time order")
    seed, start = doc.get("seed"), doc.get("start")
    return EventLogDoc(
        tuple(entries),
        title=str(doc.get("title", "")),
        description=str(doc.get("description", "")),
        seed=seed if isinstance(seed, int) else None,
        start=start if isinstance(start, int) else None,
    )


def golden_demo() -> EventLogDoc:
    """The pre-authored Golden Demo."""
    return parse_log(json.loads(GOLDEN_DEMO_PATH.read_text(encoding="utf-8")))


def _entry_json(e: LogEntry) -> dict[str, Any]:
    out: dict[str, Any] = {
        "offset": e.offset,
        "kind": e.kind,
        "target": e.target,
        "params": dict(e.params),
    }
    if e.note:
        out["note"] = e.note
    return out
