"""A scripted drive for the bench rig.

The demo has to show the same drive every time it is recorded, so the profile is
a pure function of the DBC: no randomness, no wall-clock time, no hardware. The
same frames go onto a virtual bus during development and onto a real one when
the ESP32 is wired up.

The drive is shaped so the interesting interface states have to be earned, and
it earns all four severities the DBC's own value table defines, not just one:

    0-20 s   idle          warm engine, nothing wrong
    20-50 s  acceleration  engine speed and throttle climb
    50-90 s  thermal climb coolant rises past its limit
    ~72 s    fault         P0562 battery pack undervoltage (warn)
    ~75 s    fault         C0035 front-left tire pressure low (warn)
    ~78 s    fault         P0217 engine over-temperature (severe)
    ~110 s   fault         C0040 brake fluid critically low (critical)

Nothing plants a fault at the start. A gauge that is already red says nothing
about whether the system noticed it turning red.
"""

from __future__ import annotations

from .bus import Frame
from .dbc import SignalCatalog

SAMPLE_INTERVAL_S = 0.5
DRIVE_DURATION_S = 120.0

IDLE_UNTIL_S = 20.0
ACCELERATION_UNTIL_S = 50.0
THERMAL_CLIMB_UNTIL_S = 90.0

COOLANT_LIMIT_C = 118.0
OVERHEAT_DTC = 0x100217  # ECU 0x10, P0217 engine over-temperature
OVERHEAT_SEVERITY = 2  # "severe" in the bench DBC value table

UNDERVOLTAGE_LIMIT_V = 392.0
UNDERVOLTAGE_DTC = 0x200562  # ECU 0x20 (BMS), P0562 system voltage low
UNDERVOLTAGE_SEVERITY = 1  # "warn"

TIRE_PRESSURE_LIMIT_KPA = 180.0
TIRE_LOW_DTC = 0x304035  # ECU 0x30 (chassis), C0035 tire pressure low
TIRE_LOW_SEVERITY = 1  # "warn"

BRAKE_FLUID_CRITICAL_PCT = 45.0
BRAKE_CRITICAL_DTC = 0x304040  # ECU 0x30 (chassis), C0040 brake fluid critical
BRAKE_CRITICAL_SEVERITY = 3  # "critical"

RADAR_BLOCKAGE_LIMIT_PCT = 45.0
RADAR_BLOCKED_DTC = 0x50C104  # ECU 0x50 (ADAS), U0104 cruise control comms lost
RADAR_BLOCKED_SEVERITY = 1  # "warn"

HEADLAMP_MIN_LOAD_A = 0.3
HEADLAMP_OUT_AT_S = 100.0
HEADLAMP_OUT_DTC = 0x60A477  # ECU 0x60 (lighting), B2477 lamp circuit failure
HEADLAMP_OUT_SEVERITY = 2  # "severe"

WASHER_LOW_PCT = 20.0
WASHER_LOW_DTC = 0x409318  # ECU 0x40 (body), B1318 washer fluid low
WASHER_LOW_SEVERITY = 0  # "info" — worth saying, not worth stopping for


def _ramp(value: float, start: float, end: float, low: float, high: float) -> float:
    """Linear interpolation from ``low`` to ``high`` across a time span."""
    if value <= start:
        return low
    if value >= end:
        return high
    return low + (high - low) * (value - start) / (end - start)


def _engine_state(t: float) -> dict[str, float]:
    if t < IDLE_UNTIL_S:
        rpm, throttle, speed = 780.0, 4.0, 0.0
    elif t < ACCELERATION_UNTIL_S:
        rpm = _ramp(t, IDLE_UNTIL_S, ACCELERATION_UNTIL_S, 780.0, 3600.0)
        throttle = _ramp(t, IDLE_UNTIL_S, ACCELERATION_UNTIL_S, 4.0, 62.0)
        speed = _ramp(t, IDLE_UNTIL_S, ACCELERATION_UNTIL_S, 0.0, 96.0)
    else:
        rpm = _ramp(t, ACCELERATION_UNTIL_S, THERMAL_CLIMB_UNTIL_S, 3600.0, 4300.0)
        throttle = 68.0
        speed = 104.0

    coolant = _ramp(t, IDLE_UNTIL_S, THERMAL_CLIMB_UNTIL_S, 88.0, 124.0)
    oil = _ramp(t, IDLE_UNTIL_S, THERMAL_CLIMB_UNTIL_S, 92.0, 138.0)
    return {
        "EngineSpeed": round(rpm, 2), "VehicleSpeed": round(speed, 2),
        "EngCoolantTemp": round(coolant), "ThrottlePosition": round(throttle, 1),
        "EngineOilTemp": round(oil),
    }


def _battery_state(t: float) -> dict[str, float]:
    return {
        "StateOfCharge": round(_ramp(t, 0.0, DRIVE_DURATION_S, 82.0, 61.0) * 2) / 2,
        "PackVoltage": round(_ramp(t, 0.0, DRIVE_DURATION_S, 398.0, 388.0), 2),
        "PackCurrent": round(_ramp(t, IDLE_UNTIL_S, ACCELERATION_UNTIL_S, -4.0, -180.0), 1),
        "PackTemp": round(_ramp(t, 0.0, DRIVE_DURATION_S, 28.0, 46.0)),
    }


def _chassis_state(t: float) -> dict[str, float]:
    """Three tires hold; the front-left develops a slow leak across the drive."""
    return {
        "TirePressureFL": round(_ramp(t, 0.0, DRIVE_DURATION_S, 230.0, 150.0)),
        "TirePressureFR": 230.0,
        "TirePressureRL": 228.0,
        "TirePressureRR": 229.0,
        "BrakeFluidLevel": round(_ramp(t, 0.0, DRIVE_DURATION_S, 100.0, 40.0), 1),
    }


def _body_state(t: float) -> dict[str, float]:
    """Cabin warms as the drive goes on; washer fluid is used and not refilled."""
    return {
        "CabinTemp": round(_ramp(t, 0.0, DRIVE_DURATION_S, 16.0, 23.0) * 2) / 2,
        "FuelLevel": round(_ramp(t, 0.0, DRIVE_DURATION_S, 68.0, 54.0) * 2) / 2,
        "WasherFluidLevel": round(_ramp(t, 0.0, DRIVE_DURATION_S, 100.0, 10.0) * 2) / 2,
        "DoorsAjar": 0.0,
        "CentralLockEngaged": 1.0,
        "WindowPositionFL": 0.0,
    }


def _adas_state(t: float) -> dict[str, float]:
    """Radar fouls steadily; lane tracking and driver attention degrade with it."""
    return {
        "ForwardObstacleDistance": round(_ramp(t, IDLE_UNTIL_S, DRIVE_DURATION_S, 120.0, 38.0), 1),
        "LaneConfidence": round(_ramp(t, 0.0, DRIVE_DURATION_S, 96.0, 62.0) * 2) / 2,
        "CruiseSetSpeed": 100.0,
        "DriverAttention": round(_ramp(t, 0.0, DRIVE_DURATION_S, 92.0, 71.0) * 2) / 2,
        "RadarBlockage": round(_ramp(t, 0.0, DRIVE_DURATION_S, 0.0, 60.0) * 2) / 2,
    }


def _infotainment_state(t: float) -> dict[str, float]:
    """The head unit warms in traffic; cabin noise tracks road speed."""
    return {
        "AudioVolume": 32.0,
        "ScreenBrightness": round(_ramp(t, 0.0, DRIVE_DURATION_S, 70.0, 45.0) * 2) / 2,
        "GpsSatellites": round(_ramp(t, 0.0, DRIVE_DURATION_S, 11.0, 8.0)),
        "CabinNoise": round(_ramp(t, IDLE_UNTIL_S, ACCELERATION_UNTIL_S, 41.0, 68.0) * 2) / 2,
        "HeadUnitTemp": round(_ramp(t, 0.0, DRIVE_DURATION_S, 34.0, 58.0)),
    }


def _lighting_state(t: float) -> dict[str, float]:
    """Both headlamps draw nominal current until the right-hand bulb fails open."""
    return {
        "HeadlampLoadFL": 1.2,
        "HeadlampLoadFR": 0.0 if t >= HEADLAMP_OUT_AT_S else 1.2,
        "BrakeLampLoad": 0.8,
        "AmbientLightLevel": round(_ramp(t, 0.0, DRIVE_DURATION_S, 8200.0, 1400.0)),
        "IndicatorState": 0.0,
    }


def _sensor_state(t: float) -> dict[str, float]:
    """Environment and inertial cluster: the vehicle climbs, so pressure falls."""
    return {
        "AmbientTemp": round(_ramp(t, 0.0, DRIVE_DURATION_S, 19.0, 21.5) * 2) / 2,
        "Humidity": round(_ramp(t, 0.0, DRIVE_DURATION_S, 54.0, 61.0) * 2) / 2,
        "BarometricPressure": round(_ramp(t, 0.0, DRIVE_DURATION_S, 1013.0, 988.0) * 20) / 20,
        "YawRate": round(_ramp(t, ACCELERATION_UNTIL_S, DRIVE_DURATION_S, 0.0, -3.2), 2),
        "LongAccel": round(_ramp(t, IDLE_UNTIL_S, ACCELERATION_UNTIL_S, 0.0, 1.9), 3),
    }


def _fault_frame(catalog: SignalCatalog, t: float, dtc: int, severity: int) -> Frame:
    return (t, *catalog.encode("DiagnosticCodes", {
        "DtcCount": 1, "ActiveDtc": dtc, "DtcSeverity": severity,
    }))


def drive_profile(catalog: SignalCatalog) -> list[Frame]:
    """The whole scripted drive as an ordered frame stream."""
    frames: list[Frame] = []
    faulted = {"coolant": False, "voltage": False, "tire": False, "brake": False,
               "radar": False, "headlamp": False, "washer": False}
    steps = int(DRIVE_DURATION_S / SAMPLE_INTERVAL_S)

    for step in range(steps):
        t = round(step * SAMPLE_INTERVAL_S, 3)
        engine = _engine_state(t)
        battery = _battery_state(t)
        chassis = _chassis_state(t)
        body = _body_state(t)
        adas = _adas_state(t)
        infotainment = _infotainment_state(t)
        lighting = _lighting_state(t)
        sensors = _sensor_state(t)
        frames.append((t, *catalog.encode("EngineData", engine)))

        if step % 4 == 0:
            frames.append((t, *catalog.encode("BatteryData", battery)))
            frames.append((t, *catalog.encode("ChassisData", chassis)))
            frames.append((t, *catalog.encode("BodyData", body)))
            frames.append((t, *catalog.encode("AdasData", adas)))
            frames.append((t, *catalog.encode("InfotainmentData", infotainment)))
            frames.append((t, *catalog.encode("LightingData", lighting)))
            frames.append((t, *catalog.encode("SensorData", sensors)))

        # Each fault is a consequence of the drive: it is stored the first time
        # its condition is crossed, and it stays stored afterwards. Independent
        # conditions across seven ECUs can all be true by the end of one drive.
        if engine["EngCoolantTemp"] >= COOLANT_LIMIT_C:
            faulted["coolant"] = True
        if battery["PackVoltage"] <= UNDERVOLTAGE_LIMIT_V:
            faulted["voltage"] = True
        if chassis["TirePressureFL"] <= TIRE_PRESSURE_LIMIT_KPA:
            faulted["tire"] = True
        if chassis["BrakeFluidLevel"] <= BRAKE_FLUID_CRITICAL_PCT:
            faulted["brake"] = True
        if adas["RadarBlockage"] >= RADAR_BLOCKAGE_LIMIT_PCT:
            faulted["radar"] = True
        if lighting["HeadlampLoadFR"] <= HEADLAMP_MIN_LOAD_A:
            faulted["headlamp"] = True
        if body["WasherFluidLevel"] <= WASHER_LOW_PCT:
            faulted["washer"] = True

        if step % 4 == 0:
            stored = (
                ("voltage", UNDERVOLTAGE_DTC, UNDERVOLTAGE_SEVERITY),
                ("tire", TIRE_LOW_DTC, TIRE_LOW_SEVERITY),
                ("coolant", OVERHEAT_DTC, OVERHEAT_SEVERITY),
                ("radar", RADAR_BLOCKED_DTC, RADAR_BLOCKED_SEVERITY),
                ("headlamp", HEADLAMP_OUT_DTC, HEADLAMP_OUT_SEVERITY),
                ("washer", WASHER_LOW_DTC, WASHER_LOW_SEVERITY),
                ("brake", BRAKE_CRITICAL_DTC, BRAKE_CRITICAL_SEVERITY),
            )
            for key, dtc, severity in stored:
                if faulted[key]:
                    frames.append(_fault_frame(catalog, t, dtc, severity))

    return frames
