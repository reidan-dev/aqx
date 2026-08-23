from __future__ import annotations

from pynput import keyboard

from .recording.player import StopFlag


class GlobalEmergencyStop:
    """Watches for the global emergency-stop key for the lifetime of the app and
    sets a StopFlag when it's pressed. Never consumes or blocks the keystroke."""

    def __init__(self, key_name: str, stop_flag: StopFlag):
        self.key_name = key_name
        self.stop_flag = stop_flag
        self._listener = keyboard.Listener(on_press=self._on_press)

    def _on_press(self, key) -> None:
        if isinstance(key, keyboard.KeyCode):
            name = key.char if key.char is not None else f"vk_{key.vk}"
        else:
            name = key.name
        if name == self.key_name:
            self.stop_flag.set()

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
