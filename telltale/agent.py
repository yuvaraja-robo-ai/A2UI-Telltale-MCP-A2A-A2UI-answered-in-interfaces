"""Turns that are composed by a model, through the real framework.

Everything else Telltale serves is composed deterministically in Python: the bus
is read and the surface is built by code that cannot surprise anyone. That is
the right default for a dashboard whose job is to be true, but it means no turn
exercises the thing the framework is actually for.

These turns do:

    POST {S14CODE}/v1/agent/runs   {"prompt": ..., "respond_as": "ui"}
    GET  {S14CODE}/v1/runs/{id}/composed

which is the loop the assignment describes, over HTTP, against a separately
running S14Code. No import shortcut: if the framework were broken, this would
fail rather than quietly fall back to the deterministic path.

A tap shapes the next prompt. The scope of the previous turn and the thing that
was tapped both travel into the next one, so turn three is a question that could
only have been asked because of turn two.

The composed surface still goes through the validator on the way out, because a
surface a model wrote is exactly the input the wall exists for — that it came
back through our own framework changes nothing about how much it is trusted.
"""

from __future__ import annotations

import os

import json

import httpx
from s13code.ui.validator import validate_surface

from .vehicle import dashboard
from .vehicle.bus import CanReader, vehicle_health

S14CODE = os.getenv("TELLTALE_S14CODE", "http://127.0.0.1:8113").rstrip("/")
TENANT = os.getenv("TELLTALE_TENANT", "telltale-demo")

#: The prompts each turn sends. Deliberately data-shaped rather than
#: component-shaped: they describe what the person wants to know, never which
#: component should answer. Picking the component is the framework's job, and a
#: prompt that named one would be composing the interface here instead.
TURNS: dict[str, str] = {
    "overview": (
        "A technician is standing at a test bench looking at a vehicle. "
        "Summarise the state of the vehicle so they can decide what to look at "
        "first, using ONLY the readings below."
    ),
    "domain": (
        "The technician tapped {focus}. Report what that part of the vehicle is "
        "saying and how close each reading is to its limit, using ONLY the "
        "readings below."
    ),
    "fault": (
        "The technician tapped the stored fault {focus}. Explain what was "
        "measured when it was stored and what to check next, using ONLY the "
        "readings below."
    ),
}

#: How many readings travel with a turn.
#:
#: Tuned down from twelve after watching what the local model actually did with
#: them: it composed a Row naming five StatTiles and then never emitted the
#: tiles, ending its JSON early. It was not truncated — the object closed
#: cleanly — it simply lost track of what it had promised itself. Fewer readings
#: means a shorter interface to keep straight, and the ones that matter are the
#: ones nearest their limits anyway.
MAX_READINGS = 6


def _severity_first(reading: dict) -> float:
    """How close a reading sits to the limit that would raise a fault."""
    value, low, high = (reading.get(k) for k in ("value", "min", "max"))
    limit = reading.get("alarm", reading.get("warn"))
    if not isinstance(value, (int, float)) or not isinstance(limit, (int, float)):
        if isinstance(value, (int, float)) and isinstance(low, (int, float)) \
                and isinstance(high, (int, float)) and high != low:
            return abs(value - low) / (high - low)
        return 0.0
    span = (high - low) if isinstance(low, (int, float)) and isinstance(high, (int, float)) \
        and high != low else 1.0
    # Distance past the limit, in fractions of the range; negative when healthy.
    return ((limit - value) if reading.get("invert") else (value - limit)) / span + 1.0


def bench_evidence(focus: str = "") -> dict:
    """The vehicle's actual readings, as evidence the turn is answered from.

    The first attempt at these turns sent the question alone. The framework
    composed two paragraphs of Text about checking for fluid leaks — fluent,
    plausible, and about a vehicle nobody had looked at, because nothing in the
    prompt carried what the bus was saying. A generative interface over invented
    data is worse than a fixed screen, not better.

    So the application supplies the domain data and the framework composes the
    interface for it. That division is the whole point: Telltale knows what a
    headlamp current means, S14Code knows what a bounded reading should look
    like on a screen, and neither needs to learn the other's job.
    """
    catalog, frames, source = dashboard.current_source()
    reader = CanReader(catalog, frames)
    signals = reader.read_signals()
    dtcs = reader.list_dtcs()["dtcs"]
    readings = dashboard._mark(signals["gauges"])
    # Nearest-to-the-limit first, so trimming the list drops the calm readings
    # rather than an arbitrary tail.
    readings.sort(key=_severity_first, reverse=True)

    # When a domain or a fault was tapped, lead with what it names. The tapped
    # label is matched against data, never executed and never trusted to be a
    # real name — an unmatched focus simply changes no ordering.
    needle = focus.strip().lower()
    if needle:
        readings.sort(key=lambda r: needle not in str(r.get("label", "")).lower())

    return {
        "source": source,
        "health": vehicle_health(dtcs),
        "readings": [
            {key: reading[key] for key in ("label", "value", "min", "max", "unit",
                                           "warn", "alarm", "invert")
             if key in reading}
            for reading in readings[:MAX_READINGS]
        ],
        "storedFaults": [
            {"code": d["code"], "severity": d["severity"], "ecu": d["ecu"]}
            for d in dtcs
        ],
    }


class AgentError(RuntimeError):
    """The framework did not produce a composed interface."""


#: Types that carry no shape of their own. A surface built only from these is
#: prose in a container, whatever the data underneath it was.
_FLAT = {"Text", "Column", "Row", "Card", "Divider"}

#: Pointers whose presence means the answer had structure available to it.
_RICH_POINTERS = ("gauges", "series", "series_values", "table_rows", "metrics", "choices")


def is_impoverished(component_types: set[str], data_model: dict) -> bool:
    """Did this turn answer with prose while structured data was sitting right there?

    Not a quality score — a specific, checkable mismatch. The data model carried
    bounded readings or rows or a series, the catalog offered a component for
    each, and the composition came back as Text in a Column. That is the exact
    failure the brief calls out: one text block, however clever the prompt.

    It happens because model output varies. The same prompt, the same catalog and
    the same readings produced a rich interface on one turn and two paragraphs on
    the next, so this is not something a better prompt fixes for good.
    """
    if not component_types or not component_types <= _FLAT:
        return False
    return any(data_model.get(pointer) for pointer in _RICH_POINTERS)


async def compose_turn(kind: str, focus: str = "", *, timeout: float = 900,
                       attempts: int = 3) -> dict:
    """Ask the framework for one composed turn, and ask again if it answered in prose.

    The retry is application policy, not a framework change: S14Code composed a
    valid interface both times and is not wrong to have done so. Telltale is the
    one that promised every turn would be an interface, so Telltale is the one
    that checks and asks again. It gives up after `attempts` and returns the best
    it got rather than looping — a slow honest answer beats an endless one, and
    the report says which attempt it settled for.
    """
    best: dict | None = None
    last_error: AgentError | None = None
    log: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            composed = await _compose_once(kind, focus, timeout=timeout)
        except AgentError as error:
            # A run that composed nothing at all is the same problem as a run
            # that composed prose: the model, not the framework. Both get the
            # same second chance, and the error survives to be raised if every
            # attempt fails — a turn that quietly returned nothing would be the
            # worst outcome of the three.
            last_error = error
            log.append(f"attempt {attempt}: {error}")
            continue
        composed["attempt"] = attempt
        types = set(composed["componentTypes"])
        composed["impoverished"] = is_impoverished(types, composed["surface"]["dataModel"])
        log.append(f"attempt {attempt}: {len(composed['componentTypes'])} types, "
                   f"{composed['componentCount']} components"
                   + (" — prose only" if composed["impoverished"] else ""))
        if best is None or (best["impoverished"] and not composed["impoverished"]):
            best = composed
        if not composed["impoverished"]:
            break
    if best is None:
        raise last_error or AgentError("no attempt produced a composed interface")
    # Every attempt is reported, not just the one that was kept. A turn that
    # needed three tries is a different claim from a turn that worked first
    # time, and the demo should not be able to hide the difference.
    best["attempts"] = log
    return best


async def _compose_once(kind: str, focus: str = "", *, timeout: float = 900) -> dict:
    """One trip through the framework: a prompt in, a composed interface out."""
    template = TURNS.get(kind)
    if template is None:
        raise AgentError(f"unknown turn {kind!r}")
    evidence = bench_evidence(focus)
    # The readings travel as a quoted JSON block: data the answer must come
    # from, never an instruction. Each carries its own scale and the limit that
    # raises the real fault, which is what lets the framework recognise a
    # bounded reading and pick a component for it without being told.
    prompt = (
        f"{template.format(focus=focus or 'the vehicle')}\n\n"
        f"Readings from the {evidence['source']} bus (JSON):\n"
        f"{json.dumps(evidence, indent=1)}\n\n"
        "Report every reading you use as a metric carrying its own label, value, "
        "unit, min, max and the warn/alarm limits given here, so the numbers keep "
        "their scale. Do not invent readings that are not listed."
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            started = await client.post(
                f"{S14CODE}/v1/agent/runs",
                json={"prompt": prompt, "respond_as": "ui", "tenant_id": TENANT,
                      "user_id": "bench-operator"},
            )
        except httpx.HTTPError as error:
            raise AgentError(f"S14Code unreachable at {S14CODE}: {error}") from error
        if started.status_code >= 400:
            raise AgentError(f"/v1/agent/runs {started.status_code}: {started.text[:300]}")

        run_id = started.json().get("run_id")
        if not run_id:
            raise AgentError("the run started without a run_id")

        composed = await client.get(f"{S14CODE}/v1/runs/{run_id}/composed")
        if composed.status_code >= 400:
            raise AgentError(f"/composed {composed.status_code}: {composed.text[:300]}")

    body = composed.json()
    surface = body.get("surface") or {}
    # Validated again on the way out. The framework already validated it; doing
    # it here too costs nothing and means the application never renders a
    # surface it did not check itself.
    result = validate_surface(surface)
    return {
        "kind": kind,
        "focus": focus,
        "prompt": prompt,
        "run_id": run_id,
        "provider": body.get("provider"),
        "model": body.get("model"),
        "clean": result.ok,
        "coherent": result.coherent,
        "structure": result.structure,
        "rejections": [rejection.as_dict() for rejection in result.rejections],
        "componentCount": len(result.accepted),
        "componentTypes": sorted({component.get("type") for component in result.accepted}),
        "surface": {"root": surface.get("root"), "components": result.accepted,
                    "dataModel": surface.get("dataModel") or {}},
    }
