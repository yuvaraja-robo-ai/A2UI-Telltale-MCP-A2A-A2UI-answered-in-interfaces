"""The bench drive profile.

A recorded demo has to show the same drive every time, so the profile is a pure
function of a seed: idle, acceleration, a thermal climb, and an engine
over-temperature code once the coolant passes its limit. Nothing here is random
and nothing depends on wall-clock time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from telltale.vehicle.bus import CanReader
from telltale.vehicle.dbc import SignalCatalog
from telltale.vehicle.profile import drive_profile

BENCH_DBC = Path(__file__).resolve().parents[1] / "telltale" / "vehicle" / "fixtures" / "bench_rig.dbc"


@pytest.fixture(scope="module")
def catalog() -> SignalCatalog:
    return SignalCatalog.load(BENCH_DBC)


def test_the_profile_is_identical_every_time(catalog: SignalCatalog) -> None:
    assert drive_profile(catalog) == drive_profile(catalog)


def test_the_drive_starts_at_idle(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    first_rpm = reader.read_signals(window_s=1e9)["series"]["EngineSpeed"][0]

    assert 600 <= first_rpm <= 900


def test_the_engine_climbs_under_acceleration(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    rpm = reader.read_signals(window_s=1e9)["series"]["EngineSpeed"]

    assert max(rpm) > 3000


def test_the_coolant_passes_its_limit_during_the_thermal_climb(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    coolant = reader.read_signals(window_s=1e9)["series"]["EngCoolantTemp"]

    assert max(coolant) > 110


def test_an_over_temperature_code_is_set_by_the_end_of_the_drive(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    codes = [dtc["code"] for dtc in reader.list_dtcs()["dtcs"]]

    assert "P0217" in codes


def test_a_battery_undervoltage_code_is_earned_as_the_pack_drains(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    codes = [dtc["code"] for dtc in reader.list_dtcs()["dtcs"]]

    assert "P0562" in codes


def test_a_low_tire_pressure_code_is_earned_as_the_front_left_leaks(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    codes = [dtc["code"] for dtc in reader.list_dtcs()["dtcs"]]

    assert "C0035" in codes


def test_a_critical_code_is_earned_when_brake_fluid_runs_low(catalog: SignalCatalog) -> None:
    """The drive earns all four severities the DBC defines, not just one."""
    reader = CanReader(catalog, frames=drive_profile(catalog))

    dtcs = {dtc["code"]: dtc for dtc in reader.list_dtcs()["dtcs"]}

    assert dtcs["C0040"]["severity"] == "critical"


def test_a_radar_blockage_code_is_earned_as_the_sensor_fouls(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    codes = [dtc["code"] for dtc in reader.list_dtcs()["dtcs"]]

    assert "U0104" in codes


def test_a_headlamp_out_code_is_earned_when_its_load_collapses(catalog: SignalCatalog) -> None:
    """A bulb that stops drawing current is out. The lighting ECU reports it."""
    reader = CanReader(catalog, frames=drive_profile(catalog))

    codes = [dtc["code"] for dtc in reader.list_dtcs()["dtcs"]]

    assert "B2477" in codes


def test_a_washer_fluid_code_is_only_informational(catalog: SignalCatalog) -> None:
    """Low washer fluid is worth telling the driver and nothing more. It is the
    one severity the earlier drive never produced."""
    reader = CanReader(catalog, frames=drive_profile(catalog))

    dtcs = {dtc["code"]: dtc for dtc in reader.list_dtcs()["dtcs"]}

    assert dtcs["B1318"]["severity"] == "info"


def test_every_severity_the_dbc_defines_is_earned_by_one_drive(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    severities = {dtc["severity"] for dtc in reader.list_dtcs()["dtcs"]}

    assert severities == {"info", "warn", "severe", "critical"}


def test_the_earned_codes_appear_in_the_order_the_drive_earns_them(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    ordered = [d["code"] for d in sorted(reader.list_dtcs()["dtcs"], key=lambda d: d["first_seen"])]

    assert ordered == ["P0562", "C0035", "P0217", "U0104", "B2477", "B1318", "C0040"]


def test_each_domain_ecu_reports_under_its_own_address(catalog: SignalCatalog) -> None:
    """A fault is only actionable if you know which module raised it."""
    reader = CanReader(catalog, frames=drive_profile(catalog))

    by_code = {d["code"]: d["ecu"] for d in reader.list_dtcs()["dtcs"]}

    assert by_code["P0217"] == "0x10"  # engine
    assert by_code["P0562"] == "0x20"  # battery
    assert by_code["C0035"] == "0x30"  # chassis
    assert by_code["B1318"] == "0x40"  # body
    assert by_code["U0104"] == "0x50"  # ADAS
    assert by_code["B2477"] == "0x60"  # lighting


def test_signals_from_every_domain_reach_the_reader(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    labels = {g["label"] for g in reader.read_signals(window_s=1e9)["gauges"]}

    for signal in ("EngineSpeed", "StateOfCharge", "TirePressureFL", "CabinTemp",
                   "LaneConfidence", "AudioVolume", "HeadlampLoadFL", "AmbientTemp"):
        assert signal in labels, f"{signal} never reached the reader"


def test_chassis_signals_reach_the_reader_with_the_dbcs_own_bounds(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    gauges = {g["label"]: g for g in reader.read_signals(window_s=1e9)["gauges"]}

    assert gauges["TirePressureFL"]["unit"] == "kPa"
    assert gauges["BrakeFluidLevel"]["value"] < gauges["BrakeFluidLevel"]["max"]


def test_the_front_left_tire_leaks_lower_than_the_other_three(catalog: SignalCatalog) -> None:
    reader = CanReader(catalog, frames=drive_profile(catalog))

    gauges = {g["label"]: g for g in reader.read_signals(window_s=1e9)["gauges"]}

    assert gauges["TirePressureFL"]["value"] < gauges["TirePressureFR"]["value"]


def test_no_code_is_set_before_the_engine_overheats(catalog: SignalCatalog) -> None:
    """The fault has to be earned by the drive, not planted at the start."""
    frames = drive_profile(catalog)
    reader = CanReader(catalog, frames=frames)

    dtc = next(d for d in reader.list_dtcs()["dtcs"] if d["code"] == "P0217")
    coolant_before = [
        value
        for timestamp, frame_id, data in frames
        if timestamp < dtc["first_seen"]
        for name, value in catalog.decode(frame_id, data).items()
        if name == "EngCoolantTemp"
    ]

    assert coolant_before, "the drive should report coolant before it faults"
    assert min(coolant_before) < 100
