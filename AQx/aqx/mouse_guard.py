from __future__ import annotations

import sys
import time
from collections import deque
from math import hypot
from typing import Callable, Optional

from pynput import mouse

WINDOW_SECONDS = 0.08  # smoothing window - see _on_move for why a raw two-sample delta doesn't work


class MouseAggressionGuard:
    """Watches *real* (hardware) mouse movement while a flow runs and calls
    on_trigger() the instant the user swipes the physical mouse fast enough to
    read as "I want control back now" - a panic/dead-man's-switch companion to the
    global hotkey. Deliberately blind to the flow's own synthetic mouse moves
    (Recorded Block / Code block playback, via pynput's mouse.Controller): those
    post CGEvents whose source state is CombinedSessionState (or PrivateState),
    never HIDSystemState - the same distinction macOS itself uses to tell a
    script's input from a person's. Only HID-sourced move events count toward the
    speed check, so a fast (even 10x-speed) recording can never trip this, no
    matter how large its per-event jumps are.

    macOS-only (the HID-vs-synthetic distinction is read via Quartz, which is only
    meaningful on darwin) - arm() is a no-op everywhere else, so recording
    playback is never mistaken for real input on platforms where the two can't be
    told apart. Like GlobalEmergencyStop, this is a dumb notifier: the caller
    decides what on_trigger() means (here: always "stop now").

    The underlying CGEventTap (via a pynput mouse.Listener) is created at most
    ONCE, lazily, on first use, and kept alive for the app's whole lifetime - it
    is NOT re-created per run. Use arm()/disarm() to turn detection on/off around
    each run instead of start()/stop() churn: an earlier version tore the tap down
    between runs, and repeatedly creating/destroying it turned out to destabilize
    macOS's event-tap machinery enough to break the *unrelated* global hotkey
    listener too (same process, same underlying CGEventTap subsystem) - a classic
    "looks unrelated, shares a resource" bug. arm()/disarm() just flip a flag; no
    tap is touched after the first arm()."""

    def __init__(self, threshold_px_per_sec: float, on_trigger: Callable[[], None]):
        self.threshold = max(threshold_px_per_sec, 1.0)
        self.on_trigger = on_trigger
        self._listener: Optional[mouse.Listener] = None
        self._armed = False
        self._last: Optional[tuple] = None  # (x, y, monotonic time) of the last real sample
        self._last_source_state: Optional[int] = None
        self._triggered = False
        self._window: deque = deque()  # (t, distance) pairs within the trailing WINDOW_SECONDS

    @staticmethod
    def available() -> bool:
        """Whether the HID-vs-synthetic distinction this guard depends on can
        actually be read on this machine - false anywhere except macOS with
        pyobjc-framework-quartz importable."""
        if sys.platform != "darwin":
            return False
        try:
            import Quartz  # noqa: F401

            return True
        except ImportError:
            return False

    def arm(self) -> None:
        """Call at the start of each run. Lazily creates the tap on the very
        first call (across the app's whole session); every call after that just
        resets the detection state and flips a flag - no tap churn."""
        if not self.available():
            return
        if self._listener is None:
            self._create_listener()
        self._triggered = False
        self._last = None
        self._window.clear()
        self._armed = True

    def disarm(self) -> None:
        """Call when a run ends. Leaves the tap itself running - only stops
        on_move from doing anything - so the next arm() is instant and doesn't
        touch the OS at all."""
        self._armed = False

    def shutdown(self) -> None:
        """Full teardown - only meaningful at app exit, never between runs (use
        disarm() for that)."""
        self._armed = False
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _create_listener(self) -> None:
        listener = mouse.Listener(on_move=self._on_move)
        self._patch_source_state_capture(listener)
        self._listener = listener
        listener.start()

    def _on_move(self, x: int, y: int) -> None:
        """Speed is measured as total path distance over a trailing WINDOW_SECONDS
        window, not a raw two-sample delta - macOS mouse-move events aren't evenly
        spaced (they can burst, or a source can go briefly quiet then resume), so a
        single delta between two events routinely reads as tens of thousands of
        px/sec even during perfectly ordinary movement (confirmed empirically: a
        calibration run's "normal" movement produced instantaneous spikes over
        60,000 px/sec with a raw two-sample calculation). Averaging over a short
        window smooths that noise out while still reacting within ~WINDOW_SECONDS
        of a genuine fast swipe."""
        if not self._armed or self._triggered:
            return
        import Quartz

        if self._last_source_state != Quartz.kCGEventSourceStateHIDSystemState:
            return  # synthetic (or unreadable) - never counts, however fast
        now = time.monotonic()
        if self._last is not None:
            px, py, pt = self._last
            dist = hypot(x - px, y - py)
            self._window.append((now, dist))
            cutoff = now - WINDOW_SECONDS
            while self._window and self._window[0][0] < cutoff:
                self._window.popleft()
            span = now - self._window[0][0] if self._window else 0.0
            if span > 0.01:  # needs a few samples in the window, not just one, to mean anything
                speed = sum(d for _, d in self._window) / span
                if speed > self.threshold:
                    self._triggered = True
                    self.on_trigger()
                    return
        self._last = (x, y, now)

    def _patch_source_state_capture(self, listener: mouse.Listener) -> None:
        """Reads each event's kCGEventSourceStateID before pynput decodes it into
        an on_move(x, y) call, stashing it on self so _on_move can check it -
        that field is only reachable off the raw CGEventRef the tap callback
        receives, which pynput's on_move never exposes on its own. This overrides
        _handler on this one listener instance only (not the shared class the way
        macos_keyboard_fix.py's tap-reenable patch does), so no other Listener
        anywhere in the app (the recorder, the OCR region picker, the global
        hotkey) is touched. Must be set before start(): CGEventTapCreate() is
        handed a bound _handler reference at tap-creation time, so patching it
        after start() wouldn't reach the tap pynput already created."""
        try:
            import Quartz
        except ImportError:
            return
        original_handler = listener._handler

        def handler_with_source_capture(proxy, event_type, event, refcon):
            try:
                self._last_source_state = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceStateID)
            except Exception:
                self._last_source_state = None
            return original_handler(proxy, event_type, event, refcon)

        listener._handler = handler_with_source_capture
