"""Turn CAN frames into evidence an interface can bind to.

The reader takes an ordered stream of ``(timestamp, frame_id, data)`` frames and
answers one question: what were the signals doing over the last N seconds. It
returns two shapes, because two different components need them:

  - ``gauges``  the latest value of each signal, with the bounds and unit the
    DBC defines for it
  - ``series``  every value in the window, in order, for a trend

A frame the DBC does not describe is counted and dropped. It never becomes a
gauge with guessed bounds, because a fabricated gauge looks exactly as
authoritative as a real one.

Where the frames come from is deliberately not the reader's problem. A list of
tuples, a replay file and a live SocketCAN bus all present the same stream, so
the bench and the vehicle exercise identical code.
"""

from __future__ import annotations

from collections.abc import Iterable

from .dbc import SignalCatalog

Frame = tuple[float, int, bytes]

DEFAULT_WINDOW_S = 60.0

# The message carrying stored trouble codes. Its signals describe the bus rather
# than the vehicle, so they are reported as codes and never drawn as gauges.
DIAGNOSTIC_MESSAGE = "DiagnosticCodes"

_DTC_LETTERS = ("P", "C", "B", "U")


def decode_dtc(raw: int) -> tuple[str, str]:
    """Split a raw 24-bit DTC into its standard code and the ECU that set it.

    The low 16 bits are the usual two-byte trouble code: two bits of system
    letter, two bits of first digit, three nibbles of number. The high byte is
    the address of the ECU reporting it.
    """
    ecu = raw >> 16
    code = raw & 0xFFFF
    letter = _DTC_LETTERS[(code >> 14) & 0x3]
    first_digit = (code >> 12) & 0x3
    return f"{letter}{first_digit}{code & 0x0FFF:03X}", f"0x{ecu:02X}"


_HEALTH_RANK = {"info": 1, "warn": 2, "severe": 2, "critical": 3}


def vehicle_health(dtcs: list[dict]) -> str:
    """One word a dashboard header can lead with, ranked by the worst code present.

    ``severe`` and ``critical`` are distinct severities in the DBC's own table,
    but only ``critical`` means the vehicle is in the state the word critical
    implies here; ``severe`` still reads as a warning that wants attention.
    """
    if not dtcs:
        return "normal"
    worst = max((_HEALTH_RANK.get(d.get("severity"), 1) for d in dtcs), default=1)
    return {1: "watch", 2: "warning", 3: "critical"}[worst]


class CanReader:
    """Decoded signal history over a bounded window."""

    def __init__(self, catalog: SignalCatalog, frames: Iterable[Frame]) -> None:
        self._catalog = catalog
        self._frames: list[Frame] = list(frames)
        try:
            self._diagnostic_signals = set(catalog.signals_of(DIAGNOSTIC_MESSAGE))
        except KeyError:
            self._diagnostic_signals = set()

    def read_signals(self, window_s: float = DEFAULT_WINDOW_S) -> dict:
        """Signals seen in the last ``window_s`` seconds of the stream."""
        if not self._frames:
            return {"gauges": [], "series": {}, "window_s": window_s,
                    "sample_count": 0, "undecoded_frames": 0}

        latest_timestamp = max(timestamp for timestamp, _, _ in self._frames)
        cutoff = latest_timestamp - window_s

        series: dict[str, list[float]] = {}
        undecoded = 0
        samples = 0
        for timestamp, frame_id, data in self._frames:
            if timestamp < cutoff:
                continue
            decoded = self._catalog.decode(frame_id, data)
            if not decoded:
                undecoded += 1
                continue
            samples += 1
            for name, value in decoded.items():
                if name in self._diagnostic_signals:
                    continue
                series.setdefault(name, []).append(value)

        return {
            "gauges": [self._gauge(name, values) for name, values in series.items()],
            "series": series,
            "window_s": window_s,
            "sample_count": samples,
            "undecoded_frames": undecoded,
        }

    def list_dtcs(self) -> dict:
        """Stored trouble codes, each reported once with the time it first appeared.

        A code that keeps repeating on the bus is still one fault. Reporting it
        per frame would turn a single problem into a wall of rows.
        """
        first_seen: dict[str, dict] = {}
        for timestamp, frame_id, data in self._frames:
            decoded = self._catalog.decode(frame_id, data)
            raw = int(decoded.get("ActiveDtc", 0))
            if not raw:
                continue
            code, ecu = decode_dtc(raw)
            if code in first_seen:
                continue
            severity = self._catalog.choice("DtcSeverity", decoded.get("DtcSeverity", 0))
            first_seen[code] = {"code": code, "ecu": ecu,
                                "severity": severity or "info", "first_seen": timestamp}
        return {"dtcs": list(first_seen.values())}

    def _gauge(self, name: str, values: list[float]) -> dict:
        """The latest value of one signal, on the scale the DBC gave it."""
        bounds = self._catalog.bounds(name)
        return {
            "label": name,
            "value": values[-1],
            "min": bounds.minimum if bounds else None,
            "max": bounds.maximum if bounds else None,
            "unit": bounds.unit if bounds else None,
        }
