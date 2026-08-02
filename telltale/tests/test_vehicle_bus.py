"""Frames in, gauge-shaped evidence out.

The reader is the only thing that turns a bus into numbers an interface can
bind to. Two properties matter more than the decoding itself: a signal the DBC
describes carries its own bounds, and a signal the DBC does not describe never
acquires invented ones.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from telltale.vehicle.bus import CanReader, vehicle_health
from telltale.vehicle.dbc import SignalCatalog

BENCH_DBC = Path(__file__).resolve().parents[1] / "telltale" / "vehicle" / "fixtures" / "bench_rig.dbc"


@pytest.fixture(scope="module")
def catalog() -> SignalCatalog:
    return SignalCatalog.load(BENCH_DBC)


def _engine_frame(catalog: SignalCatalog, *, rpm: float, coolant: float) -> tuple[int, bytes]:
    return catalog.encode("EngineData", {
        "EngineSpeed": rpm, "VehicleSpeed": 60, "EngCoolantTemp": coolant,
        "ThrottlePosition": 20, "EngineOilTemp": 100,
    })


def test_a_dbc_signal_becomes_a_gauge_carrying_its_own_bounds(catalog: SignalCatalog) -> None:
    frame_id, data = _engine_frame(catalog, rpm=2400, coolant=96)
    reader = CanReader(catalog, frames=[(0.0, frame_id, data)])

    gauges = {gauge["label"]: gauge for gauge in reader.read_signals()["gauges"]}

    assert gauges["EngCoolantTemp"]["value"] == 96
    assert gauges["EngCoolantTemp"]["min"] == -40
    assert gauges["EngCoolantTemp"]["max"] == 215
    assert gauges["EngCoolantTemp"]["unit"] == "degC"


def test_a_gauge_reports_the_most_recent_value_in_the_window(catalog: SignalCatalog) -> None:
    first = _engine_frame(catalog, rpm=800, coolant=70)
    last = _engine_frame(catalog, rpm=3200, coolant=104)
    reader = CanReader(catalog, frames=[(0.0, *first), (1.0, *last)])

    gauges = {gauge["label"]: gauge for gauge in reader.read_signals()["gauges"]}

    assert gauges["EngineSpeed"]["value"] == 3200


def test_a_signal_the_dbc_does_not_describe_never_becomes_a_gauge(catalog: SignalCatalog) -> None:
    """An undocumented frame decodes to nothing, so it cannot reach a gauge.

    This is the honesty rule at the lowest level: a gauge with invented bounds
    looks exactly as authoritative as a real one.
    """
    reader = CanReader(catalog, frames=[(0.0, 0x7FF, bytes(8))])

    snapshot = reader.read_signals()

    assert snapshot["gauges"] == []
    assert snapshot["undecoded_frames"] == 1


def test_a_signal_carries_its_own_series_for_a_trend(catalog: SignalCatalog) -> None:
    frames = [(float(i), *_engine_frame(catalog, rpm=800 + i * 400, coolant=70))
              for i in range(4)]
    reader = CanReader(catalog, frames=frames)

    series = reader.read_signals()["series"]

    assert series["EngineSpeed"] == [800, 1200, 1600, 2000]


def _dtc_frame(catalog: SignalCatalog, *, raw: int, severity: int) -> tuple[int, bytes]:
    return catalog.encode("DiagnosticCodes", {
        "DtcCount": 1, "ActiveDtc": raw, "DtcSeverity": severity,
    })


def test_a_raw_dtc_decodes_to_its_standard_code(catalog: SignalCatalog) -> None:
    # 0x100217: ECU 0x10, DTC 0x0217 == P0217, engine over-temperature.
    reader = CanReader(catalog, frames=[(4.0, *_dtc_frame(catalog, raw=0x100217, severity=2))])

    dtcs = reader.list_dtcs()["dtcs"]

    assert dtcs == [{"code": "P0217", "ecu": "0x10", "severity": "severe", "first_seen": 4.0}]


def test_a_dtc_is_reported_once_with_the_time_it_first_appeared(catalog: SignalCatalog) -> None:
    frame = _dtc_frame(catalog, raw=0x100217, severity=2)
    reader = CanReader(catalog, frames=[(4.0, *frame), (5.0, *frame), (6.0, *frame)])

    dtcs = reader.list_dtcs()["dtcs"]

    assert len(dtcs) == 1
    assert dtcs[0]["first_seen"] == 4.0


def test_a_clean_bus_reports_no_codes(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=[(0.0, *_engine_frame(catalog, rpm=800, coolant=70))])

    assert reader.list_dtcs()["dtcs"] == []


def test_diagnostic_housekeeping_signals_are_not_gauges(catalog: SignalCatalog) -> None:
    """A DTC count is a fact about the bus, not a measurement of the vehicle."""
    reader = CanReader(catalog, frames=[(0.0, *_dtc_frame(catalog, raw=0x100217, severity=2))])

    labels = [gauge["label"] for gauge in reader.read_signals()["gauges"]]

    assert labels == []


# --------------------------------------------------------------------------- #
# vehicle_health: one word a dashboard header can lead with
# --------------------------------------------------------------------------- #

def test_a_clean_fault_list_is_normal_health() -> None:
    assert vehicle_health([]) == "normal"


def test_an_info_only_code_is_still_watch_not_normal() -> None:
    """A stored code is a fact worth surfacing even when nothing is acute."""
    assert vehicle_health([{"severity": "info"}]) == "watch"


def test_a_warn_code_reads_as_warning() -> None:
    assert vehicle_health([{"severity": "warn"}]) == "warning"


def test_a_severe_code_reads_as_warning_not_critical() -> None:
    """Severe is serious but the vehicle is not yet in the state critical means."""
    assert vehicle_health([{"severity": "severe"}]) == "warning"


def test_a_critical_code_outranks_every_other_code_present() -> None:
    codes = [{"severity": "info"}, {"severity": "warn"}, {"severity": "critical"}]

    assert vehicle_health(codes) == "critical"


def test_the_window_excludes_samples_older_than_it(catalog: SignalCatalog) -> None:
    old = _engine_frame(catalog, rpm=800, coolant=70)
    recent = _engine_frame(catalog, rpm=3200, coolant=70)
    reader = CanReader(catalog, frames=[(0.0, *old), (100.0, *recent)])

    series = reader.read_signals(window_s=10.0)["series"]

    assert series["EngineSpeed"] == [3200]
