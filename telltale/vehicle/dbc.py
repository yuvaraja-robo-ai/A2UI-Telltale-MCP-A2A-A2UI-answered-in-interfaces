"""Signal metadata read from a DBC.

A DBC already states what a gauge needs: what the signal is called, what unit it
is in, and the range it lives on. Reading those bounds from the vehicle database
instead of inventing them is what keeps a composed gauge honest — the model
chooses which component to draw, never the axis it is drawn on.

The DBC is loaded at runtime from a path the operator supplies. It is not
committed, not embedded in a prompt, and not reachable by anything the model
says.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cantools


@dataclass(frozen=True)
class SignalBounds:
    """The scale one signal is measured on."""

    name: str
    minimum: float | None
    maximum: float | None
    unit: str | None


class SignalCatalog:
    """The signals one DBC defines, addressed by name."""

    def __init__(self, database) -> None:
        self._database = database
        self._bounds: dict[str, SignalBounds] = {}
        for message in database.messages:
            for signal in message.signals:
                self._bounds[signal.name] = SignalBounds(
                    name=signal.name,
                    minimum=signal.minimum,
                    maximum=signal.maximum,
                    unit=signal.unit,
                )

    @classmethod
    def load(cls, path: str | Path) -> SignalCatalog:
        return cls(cantools.database.load_file(str(path)))

    def bounds(self, signal_name: str) -> SignalBounds | None:
        """The scale for one signal, or None when the DBC does not define it."""
        return self._bounds.get(signal_name)

    def signal_names(self) -> tuple[str, ...]:
        """Every signal this DBC defines, in the order it defines them."""
        return tuple(self._bounds)

    def signals_of(self, message_name: str) -> tuple[str, ...]:
        """The signal names one message carries."""
        message = self._database.get_message_by_name(message_name)
        return tuple(signal.name for signal in message.signals)

    def choice(self, signal_name: str, value: float) -> str | None:
        """The label a DBC value table gives one raw value, if it defines one."""
        for message in self._database.messages:
            for signal in message.signals:
                if signal.name != signal_name or not signal.choices:
                    continue
                label = signal.choices.get(int(value))
                return str(label) if label is not None else None
        return None

    def encode(self, message_name: str, signals: dict[str, float]) -> tuple[int, bytes]:
        """Build one raw frame from named signal values.

        Used by the bench simulator to put frames on the bus. Encoding through
        the same DBC the reader decodes with is what makes the virtual bus a
        faithful stand-in for the physical one.
        """
        message = self._database.get_message_by_name(message_name)
        return message.frame_id, bytes(message.encode(signals, strict=True))

    def decode(self, frame_id: int, data: bytes) -> dict[str, float]:
        """Decode one raw frame into scaled signal values.

        A frame the DBC does not describe decodes to nothing. Guessing at an
        undocumented frame is how invented numbers reach an interface.
        """
        try:
            message = self._database.get_message_by_frame_id(frame_id)
        except KeyError:
            return {}
        decoded = message.decode(data, decode_choices=False, allow_truncated=True)
        return {name: float(value) for name, value in decoded.items()}
