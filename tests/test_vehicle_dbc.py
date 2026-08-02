"""The DBC is the source of truth for a signal's scale.

A gauge needs a label, a unit and a pair of bounds. Those come from the vehicle
database, never from the model and never from a hardcoded table, so a composed
interface cannot choose a flattering axis for an overheating engine.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from telltale.vehicle.dbc import SignalCatalog

BENCH_DBC = Path(__file__).resolve().parents[1] / "telltale" / "vehicle" / "fixtures" / "bench_rig.dbc"


@pytest.fixture(scope="module")
def catalog() -> SignalCatalog:
    return SignalCatalog.load(BENCH_DBC)


def test_signal_bounds_come_from_the_dbc(catalog: SignalCatalog) -> None:
    bounds = catalog.bounds("EngCoolantTemp")

    assert bounds is not None
    assert bounds.minimum == -40
    assert bounds.maximum == 215
    assert bounds.unit == "degC"


def test_a_signal_absent_from_the_dbc_has_no_bounds(catalog: SignalCatalog) -> None:
    assert catalog.bounds("NoSuchSignal") is None


def test_decodes_a_frame_using_the_scale_the_dbc_defines(catalog: SignalCatalog) -> None:
    # EngineData, hand-encoded little-endian against bench_rig.dbc:
    #   EngineSpeed      2400 rpm  / 0.25      -> 9600 == 0x2580
    #   VehicleSpeed     60 km/h   / 0.01      -> 6000 == 0x1770
    #   EngCoolantTemp   96 degC   offset -40  ->  136 == 0x88
    #   ThrottlePosition 20 %      / 0.4       ->   50 == 0x32
    #   EngineOilTemp    100 degC  offset -40  ->  140 == 0x8C
    frame = bytes([0x80, 0x25, 0x70, 0x17, 0x88, 0x32, 0x8C, 0x00])

    signals = catalog.decode(0x100, frame)

    assert signals["EngineSpeed"] == 2400
    assert signals["VehicleSpeed"] == 60
    assert signals["EngCoolantTemp"] == 96
    assert signals["ThrottlePosition"] == 20
    assert signals["EngineOilTemp"] == 100


def test_chassis_signals_carry_their_own_bounds(catalog: SignalCatalog) -> None:
    """Tire pressure and brake fluid are their own message, their own ECU node,
    on a different bus cadence — a separate parser path, not a relabeled engine
    signal."""
    tire = catalog.bounds("TirePressureFL")
    brake = catalog.bounds("BrakeFluidLevel")

    assert tire is not None and tire.minimum == 100 and tire.maximum == 350 and tire.unit == "kPa"
    assert brake is not None and brake.minimum == 0 and brake.maximum == 100 and brake.unit == "%"


def test_chassis_data_round_trips_through_the_dbc(catalog: SignalCatalog) -> None:
    frame_id, data = catalog.encode("ChassisData", {
        "TirePressureFL": 220, "TirePressureFR": 222,
        "TirePressureRL": 218, "TirePressureRR": 220, "BrakeFluidLevel": 95,
    })

    signals = catalog.decode(frame_id, data)

    assert signals["TirePressureFL"] == 220
    assert signals["BrakeFluidLevel"] == 95


@pytest.mark.parametrize("message,signal,unit", [
    ("BodyData", "CabinTemp", "degC"),
    ("AdasData", "ForwardObstacleDistance", "m"),
    ("InfotainmentData", "CabinNoise", "dB"),
    ("LightingData", "HeadlampLoadFL", "A"),
    ("SensorData", "BarometricPressure", "hPa"),
])
def test_every_domain_contributes_signals_with_their_own_units(
    catalog: SignalCatalog, message: str, signal: str, unit: str
) -> None:
    """Eight domains, eight messages, eight ECU nodes. Each signal carries the
    unit its own domain measures in — no shared, guessed scale across them."""
    assert signal in catalog.signals_of(message)
    bounds = catalog.bounds(signal)
    assert bounds is not None and bounds.unit == unit


@pytest.mark.parametrize("message,signals", [
    ("BodyData", {"CabinTemp": 21, "FuelLevel": 60, "WasherFluidLevel": 45,
                  "DoorsAjar": 0, "CentralLockEngaged": 1, "WindowPositionFL": 0}),
    ("AdasData", {"ForwardObstacleDistance": 42.5, "LaneConfidence": 88,
                  "CruiseSetSpeed": 100, "DriverAttention": 76, "RadarBlockage": 12}),
    ("InfotainmentData", {"AudioVolume": 32, "ScreenBrightness": 60,
                          "GpsSatellites": 11, "CabinNoise": 52, "HeadUnitTemp": 40}),
    ("LightingData", {"HeadlampLoadFL": 1.2, "HeadlampLoadFR": 1.2, "BrakeLampLoad": 0.8,
                      "AmbientLightLevel": 8200, "IndicatorState": 0}),
    ("SensorData", {"AmbientTemp": 18, "Humidity": 55, "BarometricPressure": 1013,
                    "YawRate": -2.5, "LongAccel": 1.25}),
])
def test_every_domain_message_round_trips_through_the_dbc(
    catalog: SignalCatalog, message: str, signals: dict
) -> None:
    frame_id, data = catalog.encode(message, signals)

    decoded = catalog.decode(frame_id, data)

    for name, value in signals.items():
        assert decoded[name] == pytest.approx(value, abs=0.5), f"{message}.{name}"


def test_a_value_table_outside_the_diagnostic_message_still_resolves(catalog: SignalCatalog) -> None:
    """The DBC's own words, wherever they are defined — not a table in our code."""
    assert catalog.choice("IndicatorState", 3) == "hazard"
    assert catalog.choice("IndicatorState", 0) == "off"


def test_an_unknown_frame_id_decodes_to_nothing(catalog: SignalCatalog) -> None:
    assert catalog.decode(0x7FF, bytes(8)) == {}


def test_encodes_named_signals_into_the_frame_the_dbc_describes(catalog: SignalCatalog) -> None:
    frame_id, data = catalog.encode("EngineData", {
        "EngineSpeed": 2400, "VehicleSpeed": 60, "EngCoolantTemp": 96,
        "ThrottlePosition": 20, "EngineOilTemp": 100,
    })

    assert frame_id == 0x100
    assert data == bytes([0x80, 0x25, 0x70, 0x17, 0x88, 0x32, 0x8C, 0x00])
