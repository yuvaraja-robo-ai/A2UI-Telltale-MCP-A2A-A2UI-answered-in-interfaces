"""Telltale's A2A skills: another agent asking this one to look at the vehicle.

The interface is one way in; a peer agent is another. A message whose first
token is a skill tag this agent advertises on its card is executed here and
answered with three things:

  a text part  the verdict, and every failure with the number that caused it
  a data part  the structured result, for an agent that will act on it
  the surface  the composed A2UI interface, so a peer that can render one does
               not rebuild it from the raw numbers and end up disagreeing with
               us about the same vehicle

Anything without a recognised tag is not claimed. :func:`route` returns ``None``
and the caller runs whatever it ran before, so adding diagnostics did not narrow
what this agent already accepted. A tag in our namespace that is *not* a skill
is an error rather than a fallthrough: silently answering a typo with a
general-purpose graph run is how a caller ends up believing a diagnostic ran
when none did.
"""

from __future__ import annotations

from typing import Any

from s13code.ui.validator import validate_surface
from . import dashboard

NAMESPACE = "telltale."

SKILLS: tuple[dict[str, Any], ...] = (
    {
        "id": "telltale.status",
        "name": "Vehicle status",
        "description": (
            "Reads the CAN bus and reports current health: every signal grouped by "
            "ECU domain, with stored trouble codes. Returns a renderable A2UI surface."
        ),
        "tags": ["can", "telemetry", "vehicle-health", "a2ui"],
    },
    {
        "id": "telltale.diagnose",
        "name": "Full vehicle diagnostic",
        "description": (
            "Evaluates every signal that has an operating limit against that limit, "
            "cross-checks the trouble codes the bus stored, and reports each result "
            "with the value read and the limit it broke."
        ),
        "tags": ["can", "diagnostics", "dtc", "a2ui"],
    },
)

_SKILL_IDS = tuple(skill["id"] for skill in SKILLS)


def _surface_part(catalog, frames, scope: str) -> tuple[dict, bool]:
    """Compose the surface for a scope and put it through the same wall the UI uses."""
    surface = dashboard.build_surface(catalog, frames, scope)
    result = validate_surface(surface)
    return (
        {"root": surface["root"], "components": result.accepted,
         "dataModel": surface["dataModel"]},
        result.ok,
    )


def _diagnose() -> list[dict[str, Any]]:
    catalog, frames, source = dashboard.current_source()
    report = dashboard.run_diagnostics(catalog, frames)
    surface, clean = _surface_part(catalog, frames, "diagnostics")

    failures = [row for row in report["checks"] if row["result"] != "pass"]
    lines = [f"Telltale diagnostic — {source} bus", report["verdict"]]
    if failures:
        lines.append("")
        for row in failures:
            unit = f" {row['unit']}".rstrip()
            lines.append(
                f"{row['result'].upper():<4} {row['signal']} ({row['domain']}): "
                f"read {row['value']:g}{unit}, limit {row['limit']:g}{unit}"
            )
    if report["dtcs"]:
        lines.append("")
        lines.append("Stored codes: " + ", ".join(
            f"{d['code']} {d['severity']} on ECU {d['ecu']}"
            for d in sorted(report["dtcs"], key=lambda d: d["first_seen"])
        ))

    return [
        {"kind": "text", "text": "\n".join(lines)},
        {"kind": "data", "data": {
            "skill": "telltale.diagnose",
            "source": source,
            "health": report["health"],
            "verdict": report["verdict"],
            "checksRun": report["checksRun"],
            "checksFailed": report["checksFailed"],
            "checksFlagged": report["checksFlagged"],
            "checks": report["checks"],
            "storedCodes": report["storedCodes"],
            "dtcs": report["dtcs"],
            "surface": surface,
            "surfaceClean": clean,
        }},
    ]


def _status() -> list[dict[str, Any]]:
    catalog, frames, source = dashboard.current_source()
    surface, clean = _surface_part(catalog, frames, "status")
    model = surface["dataModel"]

    dtc_line = ", ".join(
        f"{d['code']} {d['severity']}" for d in model["dtcs"]
    ) or "none"
    text = (
        f"Telltale status — {source} bus\n"
        f"Health: {model['health']}. "
        f"{model['signalCount']} signals across {model['domainCount']} ECU domains, "
        f"{model['sampleCount']} samples in the window.\n"
        f"Stored codes: {dtc_line}."
    )

    return [
        {"kind": "text", "text": text},
        {"kind": "data", "data": {
            "skill": "telltale.status",
            "source": source,
            "health": model["health"],
            "faultCount": model["faultCount"],
            "domainCount": model["domainCount"],
            "signalCount": model["signalCount"],
            "sampleCount": model["sampleCount"],
            "dtcs": model["dtcs"],
            "surface": surface,
            "surfaceClean": clean,
        }},
    ]


_HANDLERS = {"telltale.status": _status, "telltale.diagnose": _diagnose}


def route(text: str) -> list[dict[str, Any]] | None:
    """Execute a tagged message, or return None when the tag is not ours.

    The tag is the first whitespace-separated token; anything after it is free
    text a human may have added and this agent does not need.
    """
    token = (text or "").strip().split(maxsplit=1)
    if not token:
        return None
    tag = token[0].strip().rstrip(":")
    if not tag.startswith(NAMESPACE):
        return None
    if tag not in _HANDLERS:
        raise ValueError(
            f"unknown Telltale skill {tag!r}; this agent advertises {list(_SKILL_IDS)}"
        )
    return _HANDLERS[tag]()
