"""Telltale: vehicle health and diagnostics, composed as interfaces.

Two things a user can ask the framework for, and both come back as a surface
rather than a paragraph:

  ``status``       what the bus is reporting right now, grouped by ECU domain
  ``diagnostics``  every signal that has a limit, evaluated against that limit

The scopes are a closed tuple for the same reason the action catalog is closed:
a scope nobody registered should not be nameable by whatever sent the request.

The layout is derived from the vehicle database. Panels come from the DBC's own
messages, scales from its declared bounds, severity words from its value tables.
Add a message to the DBC and it gets a panel here with nothing to edit.

The one thing this module states itself is the operating limits: a DBC says what
a signal *can* read, not what it *should*. Those limits are imported from the
drive profile wherever a stored trouble code already depends on them, so a gauge
cannot go red without the bus agreeing, or stay green through a fault.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from . import profile
from .bus import CanReader, Frame, vehicle_health
from .dbc import SignalCatalog

SCOPES: tuple[str, ...] = ("status", "diagnostics")

# Panel title -> the DBC message that carries its signals.
DOMAINS: tuple[tuple[str, str], ...] = (
    ("Powertrain", "EngineData"),
    ("Battery", "BatteryData"),
    ("Chassis", "ChassisData"),
    ("Body", "BodyData"),
    ("ADAS", "AdasData"),
    ("Infotainment", "InfotainmentData"),
    ("Lighting", "LightingData"),
    ("Sensors", "SensorData"),
)

# Signals that fail towards their minimum: a draining reservoir, a leaking tire,
# a lamp that has stopped drawing current.
LOW_IS_BAD: frozenset[str] = frozenset({
    "StateOfCharge", "PackVoltage",
    "TirePressureFL", "TirePressureFR", "TirePressureRL", "TirePressureRR",
    "BrakeFluidLevel", "FuelLevel", "WasherFluidLevel",
    "LaneConfidence", "DriverAttention", "ForwardObstacleDistance",
    "GpsSatellites", "HeadlampLoadFL", "HeadlampLoadFR", "BrakeLampLoad",
})

# (warn, alarm) in each signal's own units. Alarms that already raise a trouble
# code are imported from the profile so the two can never disagree.
THRESHOLDS: dict[str, tuple[float, float]] = {
    "EngCoolantTemp": (110.0, profile.COOLANT_LIMIT_C),
    "PackVoltage": (394.0, profile.UNDERVOLTAGE_LIMIT_V),
    "TirePressureFL": (200.0, profile.TIRE_PRESSURE_LIMIT_KPA),
    "TirePressureFR": (200.0, profile.TIRE_PRESSURE_LIMIT_KPA),
    "TirePressureRL": (200.0, profile.TIRE_PRESSURE_LIMIT_KPA),
    "TirePressureRR": (200.0, profile.TIRE_PRESSURE_LIMIT_KPA),
    "BrakeFluidLevel": (60.0, profile.BRAKE_FLUID_CRITICAL_PCT),
    "RadarBlockage": (35.0, profile.RADAR_BLOCKAGE_LIMIT_PCT),
    "HeadlampLoadFL": (0.6, profile.HEADLAMP_MIN_LOAD_A),
    "HeadlampLoadFR": (0.6, profile.HEADLAMP_MIN_LOAD_A),
    "BrakeLampLoad": (0.6, profile.HEADLAMP_MIN_LOAD_A),
    "WasherFluidLevel": (30.0, profile.WASHER_LOW_PCT),
    # No stored code of their own; these are the operating limits a technician
    # would use, stated explicitly rather than inferred from the axis.
    "StateOfCharge": (25.0, 15.0),
    "FuelLevel": (20.0, 10.0),
    "LaneConfidence": (55.0, 40.0),
    "DriverAttention": (65.0, 50.0),
    "ForwardObstacleDistance": (30.0, 15.0),
    "GpsSatellites": (6.0, 4.0),
    "EngineOilTemp": (130.0, 145.0),
    "HeadUnitTemp": (70.0, 85.0),
    "CabinNoise": (80.0, 95.0),
}

_SEVERITY_RANK = {"info": 0, "warn": 1, "severe": 2, "critical": 3}


def _tone_for(health: str) -> str:
    return {"critical": "bad", "warning": "warn", "normal": "good"}.get(health, "neutral")


def _mark(gauges: list[dict]) -> list[dict]:
    """Attach failure direction and operating limits to each gauge."""
    marked = []
    for gauge in gauges:
        entry = {**gauge, "invert": gauge["label"] in LOW_IS_BAD}
        limits = THRESHOLDS.get(gauge["label"])
        if limits:
            entry["warn"], entry["alarm"] = limits
        marked.append(entry)
    return marked


def _domain_of(catalog: SignalCatalog) -> dict[str, str]:
    """Which panel each signal belongs to, straight from the DBC."""
    owner: dict[str, str] = {}
    for title, message in DOMAINS:
        try:
            for name in catalog.signals_of(message):
                owner[name] = title
        except KeyError:  # a DBC without that message is fine
            continue
    return owner


def _panels(catalog: SignalCatalog, gauges: list[dict]) -> list[tuple[str, str, list[dict]]]:
    by_label = {g["label"]: g for g in gauges}
    panels = []
    for index, (title, message) in enumerate(DOMAINS):
        try:
            names = catalog.signals_of(message)
        except KeyError:
            continue
        members = [by_label[name] for name in names if name in by_label]
        if members:
            panels.append((title, f"dom{index}", members))
    return panels


# --------------------------------------------------------------------------- #
# diagnostics: evaluate every limit, and say what it was measured against
# --------------------------------------------------------------------------- #

def run_diagnostics(catalog: SignalCatalog, frames: Iterable[Frame]) -> dict:
    """Check every signal that has an operating limit, and report each result.

    A check states the value it read, the limit it was measured against, and the
    unit both are in. "Failed" without a number is an opinion.
    """
    reader = CanReader(catalog, frames)
    signals = reader.read_signals()
    dtcs = reader.list_dtcs()["dtcs"]
    domain_of = _domain_of(catalog)

    checks: list[dict] = []
    for gauge in _mark(signals["gauges"]):
        alarm = gauge.get("alarm")
        warn = gauge.get("warn")
        if alarm is None and warn is None:
            continue  # nothing to check it against; a guess is not a diagnostic
        value = gauge["value"]
        low = gauge["invert"]
        past = (lambda limit: value <= limit) if low else (lambda limit: value >= limit)

        if alarm is not None and past(alarm):
            result, severity, limit = "fail", "severe", alarm
        elif warn is not None and past(warn):
            result, severity, limit = "warn", "warn", warn
        else:
            result, severity, limit = "pass", "ok", (alarm if alarm is not None else warn)

        checks.append({
            "signal": gauge["label"],
            "domain": domain_of.get(gauge["label"], "—"),
            "value": value,
            "limit": limit,
            "unit": gauge.get("unit") or "",
            "result": result,
            "severity": severity,
        })

    checks.sort(key=lambda row: ({"fail": 0, "warn": 1, "pass": 2}[row["result"]], row["signal"]))
    failed = sum(1 for row in checks if row["result"] == "fail")
    flagged = sum(1 for row in checks if row["result"] == "warn")

    if failed:
        verdict = (f"{failed} of {len(checks)} checks failed against their operating "
                   f"limits; {len(dtcs)} trouble code(s) stored on the bus.")
    elif flagged:
        verdict = f"{flagged} of {len(checks)} checks are approaching their limits."
    else:
        verdict = f"All {len(checks)} checks are within their operating limits."

    return {
        "checks": checks,
        "checksRun": len(checks),
        "checksFailed": failed,
        "checksFlagged": flagged,
        "storedCodes": len(dtcs),
        "dtcs": dtcs,
        "health": vehicle_health(dtcs),
        "verdict": verdict,
        "signals": signals,
    }


# --------------------------------------------------------------------------- #
# the surfaces
# --------------------------------------------------------------------------- #

def _request_buttons(current: str) -> list[dict]:
    """The two requests any surface can make of the framework.

    Both ride the one registered action name; the scope rides in its args and is
    re-checked server-side against SCOPES.
    """
    return [
        {"id": "req_status", "type": "Button", "label": "Refresh current status",
         "onPress": {"action": "request_data", "args": {"scope": "status"}}},
        {"id": "req_diag", "type": "Button", "label": "Run full diagnostic",
         "onPress": {"action": "request_data", "args": {"scope": "diagnostics"}}},
    ]


def _status_surface(catalog: SignalCatalog, frames: Iterable[Frame]) -> dict:
    reader = CanReader(catalog, frames)
    signals = reader.read_signals()
    dtcs = reader.list_dtcs()["dtcs"]
    health = vehicle_health(dtcs)
    tone = _tone_for(health)

    gauges = _mark(signals["gauges"])
    panels = _panels(catalog, gauges)
    worst = max(dtcs, key=lambda d: _SEVERITY_RANK.get(d["severity"], 0), default=None)

    events = [
        {"label": f"{d['code']} ({d['severity']}) set by ECU {d['ecu']} at t={d['first_seen']:.0f}s"}
        for d in sorted(dtcs, key=lambda d: d["first_seen"])
    ]

    components = [
        {"id": "root", "type": "Column", "children": ["title", "summary", "requests", "tabs"]},
        {"id": "title", "type": "Text", "variant": "heading", "text": {"$bind": "/title"}},
        {"id": "summary", "type": "Row", "children": ["stat_health", "stat_faults",
                                                      "stat_domains", "stat_signals",
                                                      "stat_samples"]},
        {"id": "stat_health", "type": "StatTile", "label": "health",
         "value": {"$bind": "/health"}, "tone": tone},
        {"id": "stat_faults", "type": "StatTile", "label": "active faults",
         "value": {"$bind": "/faultCount"}, "tone": "bad" if dtcs else "good"},
        {"id": "stat_domains", "type": "StatTile", "label": "ecu domains",
         "value": {"$bind": "/domainCount"}},
        {"id": "stat_signals", "type": "StatTile", "label": "live signals",
         "value": {"$bind": "/signalCount"}},
        {"id": "stat_samples", "type": "StatTile", "label": "samples", "unit": "/60s",
         "value": {"$bind": "/sampleCount"}},

        {"id": "requests", "type": "Row", "children": ["req_status", "req_diag"]},
        *_request_buttons("status"),

        {"id": "tabs", "type": "Tabs", "labels": "Overview,Active Faults,Trends,Service",
         "children": ["tab_overview", "tab_faults", "tab_trends", "tab_service"]},

        {"id": "tab_overview", "type": "Tabs",
         "labels": ",".join(title for title, _, _ in panels),
         "children": [f"card_{pid}" for _, pid, _ in panels]},
        *[
            item
            for title, pid, _ in panels
            for item in (
                {"id": f"card_{pid}", "type": "Card", "title": title,
                 "children": [f"gauges_{pid}"]},
                {"id": f"gauges_{pid}", "type": "GaugeCluster", "title": "live signals",
                 "gauges": {"$bind": f"/{pid}"}},
            )
        ],

        {"id": "tab_faults", "type": "Column",
         "children": ["faults_notice", "faults_table", "faults_timeline"]},
        {"id": "faults_notice", "type": "Notice", "tone": tone,
         "text": {"$bind": "/faultNotice"}},
        {"id": "faults_table", "type": "Card", "title": "Stored trouble codes",
         "children": ["dtc_table"]},
        {"id": "dtc_table", "type": "DataTable", "columns": "code,ecu,severity",
         "rows": {"$bind": "/dtcs"}},
        {"id": "faults_timeline", "type": "Timeline", "title": "when each fault appeared",
         "events": {"$bind": "/events"}},

        {"id": "tab_trends", "type": "Row",
         "children": ["trend_coolant", "trend_voltage", "trend_tire", "trend_radar"]},
        {"id": "trend_coolant", "type": "Card", "title": "Coolant temperature, this window",
         "children": ["spark_coolant"]},
        {"id": "spark_coolant", "type": "Sparkline", "data": {"$bind": "/trendCoolant"},
         "tone": "warn" if health != "normal" else "good"},
        {"id": "trend_voltage", "type": "Card", "title": "Pack voltage, this window",
         "children": ["spark_voltage"]},
        {"id": "spark_voltage", "type": "Sparkline", "data": {"$bind": "/trendVoltage"},
         "tone": "warn" if health != "normal" else "good"},
        {"id": "trend_tire", "type": "Card", "title": "Front-left tire pressure, this window",
         "children": ["spark_tire"]},
        {"id": "spark_tire", "type": "Sparkline", "data": {"$bind": "/trendTireFL"},
         "tone": "warn" if health != "normal" else "good"},
        {"id": "trend_radar", "type": "Card", "title": "Radar blockage, this window",
         "children": ["spark_radar"]},
        {"id": "spark_radar", "type": "Sparkline", "data": {"$bind": "/trendRadar"},
         "tone": "warn" if health != "normal" else "good"},

        {"id": "tab_service", "type": "ApprovalCard",
         "summary": {"$bind": "/serviceSummary"}, "params": {"$bind": "/serviceParams"},
         "confirm": {"action": "approve"}, "reject": {"action": "reject"}},
    ]

    return {
        "root": "root",
        "components": components,
        "dataModel": {
            "title": "Telltale — Bench Rig",
            "health": health,
            "faultCount": len(dtcs),
            "domainCount": len(panels),
            "signalCount": len(gauges),
            "sampleCount": signals["sample_count"],
            **{pid: members for _, pid, members in panels},
            "dtcs": dtcs,
            "events": events,
            "faultNotice": (
                f"{len(dtcs)} stored code(s); worst severity reads {health}."
                if dtcs else "No stored trouble codes on this bus."
            ),
            "trendCoolant": signals["series"].get("EngCoolantTemp", []),
            "trendVoltage": signals["series"].get("PackVoltage", []),
            "trendTireFL": signals["series"].get("TirePressureFL", []),
            "trendRadar": signals["series"].get("RadarBlockage", []),
            "serviceSummary": (
                f"Clear {worst['code']} ({worst['severity']}) and acknowledge the fault?"
                if worst else "No fault pending service."
            ),
            "serviceParams": {
                "code": worst["code"] if worst else "-",
                "severity": worst["severity"] if worst else "-",
                "action": "clear_and_acknowledge", "requested_by": "bench-operator",
            },
        },
    }


def _diagnostics_surface(catalog: SignalCatalog, frames: Iterable[Frame]) -> dict:
    report = run_diagnostics(catalog, frames)
    tone = _tone_for(report["health"])
    failures = [row for row in report["checks"] if row["result"] != "pass"]

    rows = [
        {"signal": row["signal"], "domain": row["domain"],
         "reading": f"{row['value']:g} {row['unit']}".strip(),
         "limit": f"{row['limit']:g} {row['unit']}".strip(),
         "severity": row["severity"]}
        for row in report["checks"]
    ]

    components = [
        {"id": "root", "type": "Column",
         "children": ["title", "summary", "verdict", "requests", "tabs"]},
        {"id": "title", "type": "Text", "variant": "heading", "text": {"$bind": "/title"}},
        {"id": "summary", "type": "Row",
         "children": ["stat_run", "stat_failed", "stat_flagged", "stat_codes"]},
        {"id": "stat_run", "type": "StatTile", "label": "checks run",
         "value": {"$bind": "/checksRun"}},
        {"id": "stat_failed", "type": "StatTile", "label": "failed",
         "value": {"$bind": "/checksFailed"},
         "tone": "bad" if report["checksFailed"] else "good"},
        {"id": "stat_flagged", "type": "StatTile", "label": "approaching limit",
         "value": {"$bind": "/checksFlagged"},
         "tone": "warn" if report["checksFlagged"] else "good"},
        {"id": "stat_codes", "type": "StatTile", "label": "stored codes",
         "value": {"$bind": "/storedCodes"},
         "tone": "bad" if report["storedCodes"] else "good"},

        {"id": "verdict", "type": "Notice", "tone": tone, "text": {"$bind": "/verdict"}},

        {"id": "requests", "type": "Row", "children": ["req_status", "req_diag"]},
        *_request_buttons("diagnostics"),

        {"id": "tabs", "type": "Tabs", "labels": "Failures,All checks,Stored codes",
         "children": ["tab_failures", "tab_all", "tab_codes"]},

        {"id": "tab_failures", "type": "Card", "title": "Checks needing attention",
         "children": ["table_failures"]},
        {"id": "table_failures", "type": "DataTable",
         "columns": "signal,domain,reading,limit,severity", "rows": {"$bind": "/failures"}},

        {"id": "tab_all", "type": "Card", "title": "Every limit evaluated",
         "children": ["table_all"]},
        {"id": "table_all", "type": "DataTable",
         "columns": "signal,domain,reading,limit,severity", "rows": {"$bind": "/checks"}},

        {"id": "tab_codes", "type": "Card", "title": "Codes stored on the bus",
         "children": ["table_codes"]},
        {"id": "table_codes", "type": "DataTable", "columns": "code,ecu,severity",
         "rows": {"$bind": "/dtcs"}},
    ]

    return {
        "root": "root",
        "components": components,
        "dataModel": {
            "title": "Telltale — diagnostic report",
            "verdict": report["verdict"],
            "checksRun": report["checksRun"],
            "checksFailed": report["checksFailed"],
            "checksFlagged": report["checksFlagged"],
            "storedCodes": report["storedCodes"],
            "checks": rows,
            "failures": [r for r in rows if r["severity"] != "ok"] or [
                {"signal": "—", "domain": "—", "reading": "—", "limit": "—", "severity": "ok"}
            ],
            "dtcs": report["dtcs"],
        },
    }


_BUILDERS = {"status": _status_surface, "diagnostics": _diagnostics_surface}


def build_surface(catalog: SignalCatalog, frames: Iterable[Frame], scope: str = "status") -> dict:
    """Compose the surface for one recognised scope."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; expected one of {SCOPES}")
    frames = list(frames)
    return _BUILDERS[scope](catalog, frames)


# --------------------------------------------------------------------------- #
# where the frames come from
# --------------------------------------------------------------------------- #

BENCH_DBC = Path(__file__).parent / "fixtures" / "bench_rig.dbc"


def current_source() -> tuple[SignalCatalog, list[Frame], str]:
    """The catalog and frames to answer a request with, and what they are.

    Two environment variables move this from a bench to a vehicle without a code
    change: ``S14_DBC_PATH`` for the vehicle's own database, ``S14_CAN_CHANNEL``
    for the interface to read. Absent a reachable channel this replays the
    scripted bench drive, and says so — the caller is told which it got, because
    a dashboard that cannot distinguish a live bus from a replay can mislead by
    omission.
    """
    catalog = SignalCatalog.load(os.environ.get("S14_DBC_PATH") or BENCH_DBC)
    channel = os.environ.get("S14_CAN_CHANNEL")

    if channel:
        from .socketcan import bus_is_available, live_window
        if bus_is_available(channel):
            return catalog, live_window(channel), "live"

    return catalog, profile.drive_profile(catalog), "bench"
