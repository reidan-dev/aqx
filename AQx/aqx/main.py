from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .config import Settings
from .emergency import GlobalEmergencyStop, preflight_accessibility, preflight_input_monitoring
from .graph.editor_window import GraphEditorWindow
from .macos_keyboard_fix import patch_event_tap_auto_reenable, prime_macos_keyboard_listener
from .recording.player import StopFlag


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("AQx")

    # Both must happen on the main thread, before any keyboard/mouse Listener is
    # created anywhere in the app (see macos_keyboard_fix.py for why) - in
    # particular before GlobalEmergencyStop below, whose listener runs for the
    # entire app lifetime and is exactly the one that needs the second fix.
    prime_macos_keyboard_listener()
    patch_event_tap_auto_reenable()

    settings = Settings.load()
    stop_flag = StopFlag()
    # global_stop is wired up after the window exists, since its trigger callback is
    # the window's own hotkey handler (which decides pause-vs-stop for a running flow).
    window = GraphEditorWindow(stop_flag=stop_flag, settings=settings, global_stop=None)
    global_stop = GlobalEmergencyStop(settings.emergency_key, window.hotkey_triggered.emit)
    window.global_stop = global_stop

    permission_granted = preflight_input_monitoring()
    if permission_granted is False:
        message = (
            f"Input Monitoring not granted to {sys.executable} - the global "
            f"{settings.emergency_key.upper()} hotkey will only work while AQx itself has focus "
            "(via the in-app shortcut) until this is fixed. Go to System Settings > Privacy & "
            "Security > Input Monitoring, enable it for this Python (or remove and re-add it if "
            "it's already listed but not working - a stale entry from a previous venv won't "
            "reactivate just by toggling it), then restart AQx."
        )
        print(f"AQx: {message}")
        window.show_permission_warning(message)

    accessibility_granted = preflight_accessibility()
    if accessibility_granted is False:
        message = (
            f"Accessibility not granted to {sys.executable} - Recorded Block playback (moving "
            "the mouse, pressing keys) will silently do nothing until this is fixed. Go to "
            "System Settings > Privacy & Security > Accessibility, enable it for this Python "
            "(or remove and re-add it if it's already listed but not working - a stale entry "
            "from a previous venv won't reactivate just by toggling it), then restart AQx."
        )
        print(f"AQx: {message}")
        window.show_permission_warning(message)

    try:
        global_stop.start()
    except Exception as exc:  # covers non-macOS/older-macOS paths preflight can't check
        message = (
            f"Global {settings.emergency_key.upper()} hotkey unavailable outside AQx ({exc}). "
            f"Grant Input Monitoring permission to {sys.executable} in System Settings > "
            "Privacy & Security, then restart AQx."
        )
        print(f"AQx: {message}")
        window.show_permission_warning(message)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
