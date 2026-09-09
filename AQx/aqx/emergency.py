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


def preflight_accessibility() -> Optional[bool]:
    """Checks whether Accessibility is currently granted, without prompting.
    This is the *other*, separate macOS permission from Input Monitoring above:
    Input Monitoring lets pynput's Listener *observe* events; Accessibility lets
    pynput's mouse.Controller/keyboard.Controller *inject* them, which is what every
    Recorded Block playback does. Same silent-failure shape as Input Monitoring -
    Controller.press()/.release()/.position setters don't raise when this is
    missing, the injected events are just dropped by the OS - so a flow "runs" to
    completion with nothing visibly happening. Returns None (treat as "unknown") on
    non-macOS or if the check itself isn't available."""
    if sys.platform != "darwin":
        return None
    try:
        import ApplicationServices

        return bool(ApplicationServices.AXIsProcessTrusted())
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
