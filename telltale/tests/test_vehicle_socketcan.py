"""The virtual bus and the physical bus are the same bus.

These tests need a SocketCAN interface and are skipped without one. That is the
point of the design rather than a gap in it: the recorder binds to an interface
name, so `vcan0` on a bench and `can0` on a vehicle exercise identical code.

    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0

One kernel behaviour drives the shape of this file. SocketCAN delivers a frame
to every socket on the interface *except* the one that sent it, unless that
socket asks for its own echo. So "a recorder that also sends" and "a recorder
listening to other ECUs" are two different situations, and a test that conflates
them proves nothing. Both are pinned below.
"""
from __future__ import annotations

import time
from pathlib import Path

import can
import pytest

from telltale.vehicle.dbc import SignalCatalog
from telltale.vehicle.socketcan import SocketCanRecorder, bus_is_available

BENCH_DBC = Path(__file__).resolve().parents[1] / "telltale" / "vehicle" / "fixtures" / "bench_rig.dbc"
CHANNEL = "vcan0"

pytestmark = pytest.mark.skipif(
    not bus_is_available(CHANNEL), reason=f"no SocketCAN interface {CHANNEL}"
)


@pytest.fixture(scope="module")
def catalog() -> SignalCatalog:
    return SignalCatalog.load(BENCH_DBC)


@pytest.fixture()
def other_ecu():
    """A second node on the bus, which is what the recorder exists to hear."""
    bus = can.Bus(channel=CHANNEL, interface="socketcan")
    yield bus
    bus.shutdown()


def wait_for(recorder: SocketCanRecorder, count: int, timeout: float = 2.0) -> list:
    deadline = time.monotonic() + timeout
    while len(recorder.frames()) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return recorder.frames()


def engine_frame(catalog: SignalCatalog) -> tuple[int, bytes]:
    return catalog.encode("EngineData", {
        "EngineSpeed": 2400, "VehicleSpeed": 60, "EngCoolantTemp": 96,
        "ThrottlePosition": 20, "EngineOilTemp": 100,
    })


def test_a_frame_another_node_puts_on_the_bus_is_recorded(catalog, other_ecu) -> None:
    frame_id, data = engine_frame(catalog)

    with SocketCanRecorder(CHANNEL) as recorder:
        other_ecu.send(can.Message(arbitration_id=frame_id, data=data, is_extended_id=False))
        frames = wait_for(recorder, 1)

    assert [(fid, payload) for _, fid, payload in frames] == [(frame_id, data)]


def test_a_recorded_frame_decodes_back_to_the_signals_that_were_sent(catalog, other_ecu) -> None:
    """The bus round trip has to survive, not just the byte count."""
    frame_id, data = engine_frame(catalog)

    with SocketCanRecorder(CHANNEL) as recorder:
        other_ecu.send(can.Message(arbitration_id=frame_id, data=data, is_extended_id=False))
        frames = wait_for(recorder, 1)

    _, fid, payload = frames[0]
    signals = catalog.decode(fid, payload)

    assert signals["EngineSpeed"] == pytest.approx(2400, abs=1)
    assert signals["EngCoolantTemp"] == pytest.approx(96, abs=1)


def test_a_recorder_does_not_hear_its_own_injection(catalog) -> None:
    """A frame this process injected is not evidence about the vehicle.

    The kernel withholds it by default and the recorder keeps that default, so a
    bench injection can never be mistaken for something an ECU said.
    """
    frame_id, data = engine_frame(catalog)

    with SocketCanRecorder(CHANNEL) as recorder:
        recorder.send(frame_id, data)
        time.sleep(0.2)
        frames = recorder.frames()

    assert frames == []


def test_a_bench_recorder_can_ask_to_hear_its_own_injection(catalog) -> None:
    """The simulator is one process playing both ECU and listener, which only
    works if it opts in to the echo."""
    frame_id, data = engine_frame(catalog)

    with SocketCanRecorder(CHANNEL, receive_own_messages=True) as recorder:
        recorder.send(frame_id, data)
        frames = wait_for(recorder, 1)

    assert [(fid, payload) for _, fid, payload in frames] == [(frame_id, data)]


def test_the_recorder_keeps_only_the_recent_past(catalog, other_ecu) -> None:
    """A ring buffer bounds memory on a bus that never stops talking."""
    frame_id, data = catalog.encode("BatteryData", {
        "StateOfCharge": 80, "PackVoltage": 396, "PackCurrent": -12, "PackTemp": 30,
    })

    with SocketCanRecorder(CHANNEL, capacity=4) as recorder:
        for _ in range(20):
            other_ecu.send(can.Message(arbitration_id=frame_id, data=data, is_extended_id=False))
        wait_for(recorder, 4)
        time.sleep(0.2)
        frames = recorder.frames()

    assert len(frames) == 4


def test_a_replayed_drive_reaches_a_recorder_on_the_same_bus(catalog) -> None:
    """Replay is how the bench feeds everything downstream a real bus."""
    from telltale.vehicle.profile import drive_profile

    frames = drive_profile(catalog)[:12]
    # Collapse the original timing so the test does not sit through the drive.
    fast = [(t / 500.0, fid, data) for t, fid, data in frames]

    with SocketCanRecorder(CHANNEL) as recorder:
        from telltale.vehicle.socketcan import replay_onto_bus

        sent = replay_onto_bus(CHANNEL, fast)
        recorded = wait_for(recorder, sent)

    assert sent == len(fast)
    assert [(fid, data) for _, fid, data in recorded] == [(fid, data) for _, fid, data in fast]
