from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class InputEvent:
    type: str  # mouse_move | mouse_down | mouse_up | mouse_scroll | key_down | key_up
    t: float  # seconds since recording start
    x: Optional[float] = None
    y: Optional[float] = None
    button: Optional[str] = None
    dx: Optional[float] = None
    dy: Optional[float] = None
    key: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @staticmethod
    def from_dict(d: dict) -> "InputEvent":
        return InputEvent(
            type=d["type"],
            t=d["t"],
            x=d.get("x"),
            y=d.get("y"),
            button=d.get("button"),
            dx=d.get("dx"),
            dy=d.get("dy"),
            key=d.get("key"),
        )
