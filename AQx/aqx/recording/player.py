from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from pynput import keyboard, mouse

from .events import InputEvent


class StopFlag:
    """A thread-safe flag shared between the UI, the recorder, and playback/graph execution."""

    def __init__(self):
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()


class PauseFlag(StopFlag):
    """Same shape as StopFlag, but toggled on/off repeatedly during a run (rather
    than set once and done) to freeze and resume execution in place."""


class Player:
    def __init__(
        self,
        events: List[InputEvent],
        stop_flag: StopFlag,
        speed: float = 1.0,
        on_status: Optional[Callable[[str], None]] = None,
        pause_flag: Optional[PauseFlag] = None,
    ):
        self.events = events
        self.stop_flag = stop_flag
        self.pause_flag = pause_flag
        self.speed = max(speed, 0.01)
        self.on_status = on_status or (lambda s: None)
        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()

    def _key_from_name(self, name: str):
        if name.startswith("vk_"):
            return keyboard.KeyCode(vk=int(name[3:]))
        if hasattr(keyboard.Key, name):
            return getattr(keyboard.Key, name)
        return keyboard.KeyCode.from_char(name)

    def _apply(self, ev: InputEvent) -> None:
        if ev.type == "mouse_move":
            self._mouse.position = (ev.x, ev.y)
        elif ev.type in ("mouse_down", "mouse_up"):
            self._mouse.position = (ev.x, ev.y)
            btn = getattr(mouse.Button, ev.button, mouse.Button.left)
            if ev.type == "mouse_down":
                self._mouse.press(btn)
            else:
                self._mouse.release(btn)
        elif ev.type == "mouse_scroll":
            self._mouse.scroll(ev.dx, ev.dy)
        elif ev.type == "key_down":
            self._keyboard.press(self._key_from_name(ev.key))
        elif ev.type == "key_up":
            self._keyboard.release(self._key_from_name(ev.key))

    def play_once(self) -> None:
        if not self.events:
            return
        origin = time.monotonic()
        paused_total = 0.0
        for ev in self.events:
            while True:
                if self.stop_flag.is_set():
                    return
                if self.pause_flag is not None and self.pause_flag.is_set():
                    # Freeze mid-action; shift the timeline forward by however long we
                    # were paused so playback resumes at the same relative pace
                    # instead of bursting through every event that "missed" its cue.
                    paused_start = time.monotonic()
                    while self.pause_flag.is_set():
                        if self.stop_flag.is_set():
                            return
                        time.sleep(0.03)
                    paused_total += time.monotonic() - paused_start
                    continue
                target = origin + paused_total + ev.t / self.speed
                remaining = target - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.01))
            self._apply(ev)

    def run(self, repeat: int = 1) -> None:
        """repeat=0 means repeat indefinitely until stopped."""
        self.on_status("running")
        count = 0
        try:
            while not self.stop_flag.is_set():
                self.play_once()
                count += 1
                if repeat != 0 and count >= repeat:
                    break
        finally:
            self.on_status("idle")
