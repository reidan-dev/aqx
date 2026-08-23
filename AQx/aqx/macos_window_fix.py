"""
Makes a floating Qt window behave like a proper macOS utility panel: clickable
without first needing to activate the app, and persistently visible across app
switches. Neither property is reachable through Qt's own window-flag API alone:

- Without Qt.Tool, a widget is a plain NSWindow. Clicking a background app's plain
  window is often consumed just to activate that app - the click doesn't reliably
  reach the control itself, and app activation is an async transition with its own
  race window, so the very next synthetic click can land before it completes.
  Confirmed directly: real synthetic clicks on a toolbar button were silently
  dropped, or only registered after over a second's delay, when another app was
  frontmost.
- With Qt.Tool, Qt creates an NSPanel, which macOS treats as a non-activating
  utility panel - clicks are delivered immediately regardless of which app is
  currently frontmost. But NSPanel's hidesOnDeactivate defaults to True, which is
  exactly why an earlier Qt.Tool-based overlay in this app vanished the moment a
  different app became active.

Fix: use Qt.Tool for the NSPanel click-delivery behavior, then reach through to the
real NSWindow via PyObjC and explicitly set hidesOnDeactivate to False, getting both
properties instead of being forced to choose one.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QWidget


def keep_panel_visible_across_app_switches(widget: QWidget) -> None:
    """Call after the widget (built with Qt.Tool) has a native window - e.g. from
    showEvent or right after show(). No-ops outside macOS or if PyObjC isn't usable
    for some reason; this is a visibility nicety, not something core functionality
    should hard-depend on."""
    if sys.platform != "darwin":
        return
    try:
        import objc

        ns_view = objc.objc_object(c_void_p=int(widget.winId()))
        ns_window = ns_view.window()
        if ns_window is not None:
            ns_window.setHidesOnDeactivate_(False)
            # Makes sure the panel can properly become key (and so reliably receive
            # the click) without that requiring the owning app to activate first.
            ns_window.setBecomesKeyOnlyIfNeeded_(False)
            ns_window.orderFrontRegardless()
    except Exception:
        pass
