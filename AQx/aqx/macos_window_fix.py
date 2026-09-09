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
- With Qt.Tool, Qt creates an NSPanel, which is enough for clicks to be delivered
  reliably regardless of which app is currently frontmost. But NSPanel's
  hidesOnDeactivate defaults to True, which is exactly why an earlier Qt.Tool-based
  overlay in this app vanished the moment a different app became active. And by
  itself an NSPanel is still an *activating* panel: clicking any control in it makes
  AQx the frontmost app as a side effect (menu bar switches, target app loses
  focus) - confirmed directly, clicking the mini run toolbar's Play/Pause button
  while another app was frontmost brought AQx's own window to the front instead of
  leaving the target app alone. The style bit that actually prevents that,
  NSWindowStyleMaskNonactivatingPanel, isn't reachable through Qt's window-flag API.

Fix: use Qt.Tool for the NSPanel click-delivery behavior, then reach through to the
real NSWindow via PyObjC and explicitly set hidesOnDeactivate to False and add the
nonactivatingPanel style bit, getting all three properties instead of being forced
to choose.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QWidget


def keep_panel_visible_across_app_switches(widget: QWidget) -> None:
    """Call after the widget (built with Qt.Tool) has a native window - e.g. from
    showEvent or right after show(). No-ops outside macOS or if PyObjC isn't usable
    for some reason; this is a visibility nicety, not something core functionality
    should hard-depend on.

    Also no-ops when Qt isn't actually using the native "cocoa" platform plugin (e.g.
    the "offscreen" plugin used for headless testing) - there's no real NSWindow
    behind the widget in that case, and wrapping a bogus native handle with PyObjC
    can crash the process outright rather than raising a catchable exception."""
    if sys.platform != "darwin":
        return
    app = QApplication.instance()
    if app is not None and app.platformName() != "cocoa":
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
            # NSWindowStyleMaskNonactivatingPanel (1 << 7) - without this bit, clicking
            # any control in the panel still activates AQx as a side effect (brings its
            # own window/menu bar to the front), even though becomesKeyOnlyIfNeeded
            # already let the click land. This bit is what actually stops that.
            NON_ACTIVATING_PANEL_MASK = 1 << 7
            ns_window.setStyleMask_(ns_window.styleMask() | NON_ACTIVATING_PANEL_MASK)
            ns_window.orderFrontRegardless()
    except Exception:
        pass


def make_window_click_through(widget: QWidget) -> None:
    """Call after the widget has a native window (e.g. from showEvent) to make it
    truly pass every click through to whatever's beneath it. Qt's own
    WA_TransparentForMouseEvents attribute is set on these overlays already, but
    for a frameless, no-Qt.Tool, always-on-top window it doesn't reliably stop the
    window from being hit-tested on macOS - confirmed directly, a region-picker
    frame with the attribute set was still eating clicks meant for the window
    underneath it. Setting NSWindow.ignoresMouseEvents directly is what actually
    works, same category of gap as keep_panel_visible_across_app_switches above.

    Same no-ops as keep_panel_visible_across_app_switches: skipped outside macOS,
    outside the "cocoa" platform plugin, or if PyObjC isn't usable."""
    if sys.platform != "darwin":
        return
    app = QApplication.instance()
    if app is not None and app.platformName() != "cocoa":
        return
    try:
        import objc

        ns_view = objc.objc_object(c_void_p=int(widget.winId()))
        ns_window = ns_view.window()
        if ns_window is not None:
            ns_window.setIgnoresMouseEvents_(True)
    except Exception:
        pass
