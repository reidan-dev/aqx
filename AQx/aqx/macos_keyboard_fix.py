"""
Works around two separate bugs in pynput 1.8.2's macOS backend. Both fixes must be
applied once, early, before any keyboard/mouse Listener or Controller is created
anywhere in the app - see each function's own docstring for why.

=== Bug 1: a TSM crash (prime_macos_keyboard_listener) ===

Two separate call sites both query Carbon/HIToolbox Text Services Manager APIs
(TISCopyCurrentKeyboardInputSource / TISGetInputSourceProperty) to build a
keycode-to-character table, and both do it from a background thread:

- pynput.keyboard._darwin.Listener._run() calls keycode_context() every time a
  listener starts (used when *recording* - _run() is the listener's own thread).
- pynput._util.darwin.get_unicode_to_keycode_map() calls the same keycode_context()
  from Controller.__init__ (used when *replaying* - AQx constructs a
  keyboard.Controller() inside GraphRunner's background playback thread).

On this system's macOS version, HIToolbox asserts that this specific TSM codepath
only ever runs on the main thread, and aborts the whole process
(EXC_BREAKPOINT / dispatch_assert_queue_fail in TSMGetInputSourceProperty) when it
doesn't. This is reproducible from pynput's own source, independent of anything
AQx does with multiple listeners.

Fix: monkeypatch pynput to compute that keycode table exactly once, on the Qt main
thread, and cache it - so no background thread ever touches TSM. Both call sites
import keycode_context by name into their own module at import time
(`from pynput._util.darwin import keycode_context`), so each module's own
reference has to be patched separately - patching the source module alone does not
retroactively update the copy pynput.keyboard._darwin already imported.
"""

from __future__ import annotations

import contextlib
import sys
import threading

_primed = False
_tap_patched = False
_lock = threading.Lock()


def prime_macos_keyboard_listener() -> None:
    """Call once, early, from the Qt main thread - before any keyboard.Listener or
    keyboard.Controller is created anywhere in the app."""
    global _primed
    if sys.platform != "darwin" or _primed:
        return
    with _lock:
        if _primed:
            return
        try:
            from pynput._util import darwin as _pynput_util_darwin
            from pynput.keyboard import _darwin as _pynput_kb_darwin
        except ImportError:
            return

        original_keycode_context = _pynput_util_darwin.keycode_context
        cache: dict = {}

        @contextlib.contextmanager
        def cached_keycode_context():
            if "value" not in cache:
                with original_keycode_context() as ctx:
                    cache["value"] = ctx
            yield cache["value"]

        # Populate the cache right now, on the calling (main) thread - this is the
        # only place the real TIS/TSM query ever happens.
        with cached_keycode_context():
            pass

        _pynput_util_darwin.keycode_context = cached_keycode_context
        _pynput_kb_darwin.keycode_context = cached_keycode_context
        _primed = True


def patch_event_tap_auto_reenable() -> None:
    """=== Bug 2: the event tap silently going deaf after a while ===

    macOS automatically disables a CGEventTap (delivering a callback with event
    type kCGEventTapDisabledByTimeout, or more rarely kCGEventTapDisabledByUserInput)
    any time its callback is too slow to respond even once - which can happen in
    perfectly normal operation over a long-running session (a GC pause, the Qt main
    thread being briefly busy, system load, etc.), not just under actual failure.
    Apple's docs for CGEventTapCallBack say the fix is simple: call
    CGEventTapEnable(proxy, true) using the `proxy` argument the callback receives
    for that special event type. pynput 1.8.2's darwin ListenerMixin._handler (shared
    by both keyboard.Listener and mouse.Listener) never checks for this event type at
    all, so once it happens the listener thread keeps running and looks perfectly
    healthy, but silently stops delivering every future event - forever, until the
    process restarts. This is why AQx's global hotkey can work fine right after
    launch and then stop responding after a while with no error anywhere.

    Must run before any Listener is constructed: CGEventTapCreate() is handed a
    bound `self._handler` reference at tap-creation time, so a listener created
    before this patch is applied keeps using the original (unpatched) method for its
    entire lifetime - patching the class afterward wouldn't reach it."""
    global _tap_patched
    if sys.platform != "darwin" or _tap_patched:
        return
    with _lock:
        if _tap_patched:
            return
        try:
            from pynput._util import darwin as _pynput_util_darwin
            import Quartz
        except ImportError:
            return

        original_handler = _pynput_util_darwin.ListenerMixin._handler
        disabled_types = (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput)

        def patched_handler(self, proxy, event_type, event, refcon):
            if event_type in disabled_types:
                Quartz.CGEventTapEnable(proxy, True)
                return None
            return original_handler(self, proxy, event_type, event, refcon)

        _pynput_util_darwin.ListenerMixin._handler = patched_handler
        _tap_patched = True
