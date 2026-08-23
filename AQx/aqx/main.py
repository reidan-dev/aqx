from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .config import Settings
from .emergency import GlobalEmergencyStop
from .graph.editor_window import GraphEditorWindow
from .macos_keyboard_fix import prime_macos_keyboard_listener
from .recording.player import StopFlag


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("AQx")

    # Must happen on the main thread, before any keyboard.Listener is created anywhere
    # in the app (see macos_keyboard_fix.py for why).
    prime_macos_keyboard_listener()

    settings = Settings.load()
    stop_flag = StopFlag()
    global_stop = GlobalEmergencyStop(settings.emergency_key, stop_flag)
    window = GraphEditorWindow(stop_flag=stop_flag, settings=settings, global_stop=global_stop)

    try:
        global_stop.start()
    except Exception as exc:  # macOS Input Monitoring / Accessibility permission not yet granted
        print(
            "AQx: could not start the global emergency-stop listener "
            f"({exc}). Grant Input Monitoring and Accessibility permission "
            "to your terminal/Python in System Settings > Privacy & Security, "
            "then restart AQx."
        )

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
