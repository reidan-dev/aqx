from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from pynput import keyboard, mouse

from .events import InputEvent


class Recorder:
    """Records mouse and/or keyboard input until stopped or the emergency key is pressed.

    The emergency key is never itself recorded, so it can't be accidentally replayed.
    """

    def __init__(
        self,
        record_mouse: bool = True,
        record_keyboard: bool = True,
        emergency_key: str = "backspace",
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self.record_mouse = record_mouse
        self.record_keyboard = record_keyboard
        self.emergency_key = emergency_key
        self.on_status = on_status or (lambda s: None)

        self._events: List[InputEvent] = []
        self._start_time: Optional[float] = None
        self._mouse_listener: Optional[mouse.Listener] = None
        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._stopped = threading.Event()

    def _elapsed(self) -> float:
        return time.monotonic() - self._start_time

    def _key_name(self, key) -> str:
        if isinstance(key, keyboard.KeyCode):
            return key.char if key.char is not None else f"vk_{key.vk}"
        return key.name

    def _on_move(self, x, y):
        if self._stopped.is_set():
            return
        self._events.append(InputEvent(type="mouse_move", t=self._elapsed(), x=x, y=y))

    def _on_click(self, x, y, button, pressed):
        if self._stopped.is_set():
            return
        self._events.append(
            InputEvent(
                type="mouse_down" if pressed else "mouse_up",
                t=self._elapsed(),
                x=x,
                y=y,
                button=button.name,
            )
        )

    def _on_scroll(self, x, y, dx, dy):
        if self._stopped.is_set():
            return
        self._events.append(InputEvent(type="mouse_scroll", t=self._elapsed(), x=x, y=y, dx=dx, dy=dy))

    def _on_press(self, key):
        name = self._key_name(key)
        if name == self.emergency_key:
            # Stop everything, but only ever touch the *other* listener from here -
            # calling this listener's own .stop() from inside its callback thread
            # can deadlock, so we signal our own stop via `return False`.
            self._stopped.set()
            if self._mouse_listener is not None:
                self._mouse_listener.stop()
            self.on_status("stopped")
            return False
        if self._stopped.is_set():
            return
        if self.record_keyboard:
            self._events.append(InputEvent(type="key_down", t=self._elapsed(), key=name))

    def _on_release(self, key):
        if self._stopped.is_set():
            return
        if self.record_keyboard:
            self._events.append(InputEvent(type="key_up", t=self._elapsed(), key=self._key_name(key)))

    def start(self) -> None:
        self._events = []
        self._stopped.clear()
        self._start_time = time.monotonic()
        if self.record_mouse:
            self._mouse_listener = mouse.Listener(
                on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
            )
            self._mouse_listener.start()
        # The keyboard listener always runs so the emergency key can end recording,
        # even when only mouse input is being recorded.
        self._keyboard_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._keyboard_listener.start()
        self.on_status("recording")

    def stop(self) -> None:
        """External stop, e.g. from a UI button (not called from inside a listener callback)."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
        self.on_status("stopped")

    @property
    def events(self) -> List[InputEvent]:
        return list(self._events)
