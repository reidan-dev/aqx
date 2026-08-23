"""
Works around a crash in pynput 1.8.2's macOS keyboard backend.

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
