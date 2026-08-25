from __future__ import annotations

import sys
from typing import Callable, Optional

from pynput import keyboard


def preflight_input_monitoring() -> Optional[bool]:
    """Checks whether Input Monitoring is currently granted, without prompting.
    pynput's keyboard.Listener.start() on macOS does NOT reliably raise when this
    permission is missing - the CGEventTap is just created disabled and silently
    never delivers events, so catching an exception around start() alone misses this
    case entirely. Returns None (treat as "unknown, let start() surface anything
    real") on non-macOS or if the check itself isn't available."""
    if sys.platform != "darwin":
        return None
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return None


class GlobalEmergencyStop:
    """Watches for the global hotkey for the lifetime of the app and calls
    on_trigger() each time it's pressed. Never consumes or blocks the keystroke.

    Deciding what a press actually *means* (pause vs. stop, single vs. double press)
    is not this class's job - it's a dumb key-press notifier. The caller (the editor
    window) owns run state and makes that decision, since it already knows whether a
    flow is running."""

    def __init__(self, key_name: str, on_trigger: Callable[[], None]):
        self.key_name = key_name
        self.on_trigger = on_trigger
        self._listener = keyboard.Listener(on_press=self._on_press)

    def _on_press(self, key) -> None:
        if isinstance(key, keyboard.KeyCode):
            name = key.char if key.char is not None else f"vk_{key.vk}"
        else:
            name = key.name
        if name == self.key_name:
            self.on_trigger()

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
