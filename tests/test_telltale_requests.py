"""Asking the framework for a status, and asking it to run a diagnostic.

The interface is not a picture of a run that already finished. A tap has to be
able to say "tell me what the vehicle is doing now" or "go check it", and get a
freshly composed surface back. That request crosses the same wall everything
else does: a name the catalog registered, a scope this module recognises, and a
surface that passes the validator before anyone sees it.

Two scopes, deliberately distinct:

  status       what the bus is reporting right now
  diagnostics  every threshold evaluated against its limit, and what that means
"""
from __future__ import annotations

from pathlib import Path

import pytest

from s13code.ui.validator import validate_surface
from telltale.vehicle import dashboard
from telltale.vehicle.dbc import SignalCatalog
from telltale.vehicle.profile import drive_profile

BENCH_DBC = Path(__file__).resolve().parents[1] / "telltale" / "vehicle" / "fixtures" / "bench_rig.dbc"


@pytest.fixture(scope="module")
def catalog() -> SignalCatalog:
    return SignalCatalog.load(BENCH_DBC)


@pytest.fixture(scope="module")
def frames(catalog: SignalCatalog) -> list:
    return drive_profile(catalog)


def surface_for(catalog, frames, scope):
    return dashboard.build_surface(catalog, frames, scope)


# --------------------------------------------------------------------------- #
# the scopes a request may name
# --------------------------------------------------------------------------- #

def test_the_recognised_scopes_are_a_closed_set() -> None:
    """Same reasoning as the action catalog: a scope nobody registered cannot
    be named into existence by whatever sends the request."""
    assert dashboard.SCOPES == ("status", "diagnostics")


def test_a_scope_nobody_registered_is_refused(catalog, frames) -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        surface_for(catalog, frames, "drop_tables")


@pytest.mark.parametrize("scope", dashboard.SCOPES)
def test_every_scope_composes_a_surface_the_validator_accepts(catalog, frames, scope) -> None:
    result = validate_surface(surface_for(catalog, frames, scope))

    assert result.ok, [r.as_dict() for r in result.rejections]
    assert result.accepted


@pytest.mark.parametrize("scope", dashboard.SCOPES)
def test_every_scope_offers_the_next_request_as_a_registered_action(catalog, frames, scope) -> None:
    """The loop has to be closable from inside the surface itself, or the user
    is looking at a dead end."""
    surface = surface_for(catalog, frames, scope)

    actions = {
        prop["action"]
        for comp in surface["components"]
        for key, prop in comp.items()
        if isinstance(prop, dict) and "action" in prop
    }

    assert "request_data" in actions


# --------------------------------------------------------------------------- #
# status: what the bus is saying now
# --------------------------------------------------------------------------- #

def test_a_status_surface_groups_every_domain_the_dbc_declares(catalog, frames) -> None:
    surface = surface_for(catalog, frames, "status")

    assert surface["dataModel"]["domainCount"] == 8
    assert surface["dataModel"]["signalCount"] == 40


def test_a_status_surface_reports_the_health_the_codes_imply(catalog, frames) -> None:
    surface = surface_for(catalog, frames, "status")

    assert surface["dataModel"]["health"] == "critical"


# --------------------------------------------------------------------------- #
# diagnostics: every threshold actually evaluated
# --------------------------------------------------------------------------- #

def test_a_diagnostic_evaluates_every_signal_that_has_a_limit(catalog, frames) -> None:
    checks = dashboard.run_diagnostics(catalog, frames)["checks"]

    checked = {row["signal"] for row in checks}
    assert "EngCoolantTemp" in checked
    assert "BrakeFluidLevel" in checked
    assert len(checks) >= 15


def test_a_signal_past_its_alarm_is_reported_as_failed(catalog, frames) -> None:
    checks = {row["signal"]: row for row in dashboard.run_diagnostics(catalog, frames)["checks"]}

    assert checks["EngCoolantTemp"]["result"] == "fail"
    assert checks["BrakeFluidLevel"]["result"] == "fail"


def test_a_signal_inside_its_limits_is_reported_as_passing(catalog, frames) -> None:
    checks = {row["signal"]: row for row in dashboard.run_diagnostics(catalog, frames)["checks"]}

    assert checks["HeadlampLoadFL"]["result"] == "pass"
    assert checks["TirePressureFR"]["result"] == "pass"


def test_a_failed_check_states_the_value_and_the_limit_it_broke(catalog, frames) -> None:
    """A diagnostic that says "failed" without saying against what is an opinion,
    not a measurement."""
    checks = {row["signal"]: row for row in dashboard.run_diagnostics(catalog, frames)["checks"]}
    coolant = checks["EngCoolantTemp"]

    assert coolant["value"] == 124
    assert coolant["limit"] == 118
    assert coolant["unit"] == "degC"


def test_a_diagnostic_counts_what_it_ran_and_what_failed(catalog, frames) -> None:
    report = dashboard.run_diagnostics(catalog, frames)

    assert report["checksRun"] == len(report["checks"])
    assert report["checksFailed"] == sum(1 for r in report["checks"] if r["result"] == "fail")
    assert report["checksFailed"] > 0


def test_a_diagnostic_agrees_with_the_codes_the_bus_stored(catalog, frames) -> None:
    """The two halves are computed independently — thresholds evaluated here,
    codes read off the bus — so agreeing is evidence, not tautology."""
    report = dashboard.run_diagnostics(catalog, frames)

    failed = {row["signal"] for row in report["checks"] if row["result"] == "fail"}
    assert "EngCoolantTemp" in failed  # P0217 is stored
    assert report["storedCodes"] == 7


def test_a_clean_bus_fails_no_checks_and_says_so(catalog) -> None:
    quiet = [(0.0, *catalog.encode("EngineData", {
        "EngineSpeed": 800, "VehicleSpeed": 0, "EngCoolantTemp": 88,
        "ThrottlePosition": 4, "EngineOilTemp": 92,
    }))]

    report = dashboard.run_diagnostics(catalog, quiet)

    assert report["checksFailed"] == 0
    assert report["storedCodes"] == 0
    assert report["verdict"].startswith("All")
