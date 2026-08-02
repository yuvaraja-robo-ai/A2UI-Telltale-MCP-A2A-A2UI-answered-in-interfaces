"""A live SocketCAN bus, recorded into a bounded ring.

The recorder binds to an interface *name*. That is the whole portability
argument: `vcan0` on a bench and `can0` on a vehicle present the same kernel
interface, so moving from simulation to hardware changes a string and nothing
else.

A bus never stops talking, so the recorder keeps a fixed number of recent frames
rather than the whole history. What reaches an interface is the recent past,
which is what a diagnostic question is actually about.

The kernel does not deliver a frame back to the socket that sent it. The
recorder keeps that default: a frame this process injected is not evidence about
a vehicle, and it should not be able to arrive looking like evidence. A bench
simulator that plays both ECU and listener in one process opts in explicitly
with ``receive_own_messages=True``.
"""

from __future__ import annotations

import socket
import threading
import time
from collections import deque

import can

from .bus import Frame

DEFAULT_CAPACITY = 20_000  # a few minutes of a busy powertrain bus


def bus_is_available(channel: str) -> bool:
    """Whether a SocketCAN interface of this name exists on the host."""
    try:
        socket.if_nametoindex(channel)
    except OSError:
        return False
    return True


class SocketCanRecorder:
    """Record frames off a live bus into a bounded, time-stamped ring."""

    def __init__(
        self,
        channel: str,
        capacity: int = DEFAULT_CAPACITY,
        *,
        receive_own_messages: bool = False,
    ) -> None:
        self.channel = channel
        self.receive_own_messages = receive_own_messages
        self._frames: deque[Frame] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._bus: can.BusABC | None = None
        self._notifier: can.Notifier | None = None

    def open(self) -> SocketCanRecorder:
        self._bus = can.Bus(
            channel=self.channel,
            interface="socketcan",
            receive_own_messages=self.receive_own_messages,
        )
        self._notifier = can.Notifier(self._bus, [self._record])
        return self

    def close(self) -> None:
        if self._notifier is not None:
            self._notifier.stop()
            self._notifier = None
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def __enter__(self) -> SocketCanRecorder:
        return self.open()

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _record(self, message: can.Message) -> None:
        with self._lock:
            self._frames.append((message.timestamp, message.arbitration_id, bytes(message.data)))

    def frames(self) -> list[Frame]:
        """The recorded frames, oldest first."""
        with self._lock:
            return list(self._frames)

    def send(self, frame_id: int, data: bytes) -> None:
        """Put one frame on the bus. Used by the bench simulator."""
        if self._bus is None:
            raise RuntimeError("recorder is not open")
        self._bus.send(can.Message(arbitration_id=frame_id, data=data, is_extended_id=False))


def live_window(channel: str, *, seconds: float = 1.0) -> list[Frame]:
    """Listen to a live bus for a bounded moment and return what it said.

    A request has to answer promptly, so this captures a short window rather
    than holding a recorder open. On a quiet bus it returns nothing, which is
    the honest answer: no frames means no gauges, not invented ones.
    """
    with SocketCanRecorder(channel) as recorder:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.02)
        return recorder.frames()


def replay_onto_bus(channel: str, frames: list[Frame], *, speed: float = 1.0) -> int:
    """Play a recorded drive onto a bus in its original timing.

    Used to drive the virtual bus during development, so everything downstream
    sees a bus that behaves like the real one instead of a file that does not.
    """
    sent = 0
    with can.Bus(channel=channel, interface="socketcan") as bus:
        started = time.monotonic()
        origin = frames[0][0] if frames else 0.0
        for timestamp, frame_id, data in frames:
            due = (timestamp - origin) / speed
            delay = due - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
            bus.send(can.Message(arbitration_id=frame_id, data=data, is_extended_id=False))
            sent += 1
    return sent
