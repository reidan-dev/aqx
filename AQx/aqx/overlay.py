from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel


class CountdownOverlay(QLabel):
    """A frameless, click-through, always-on-top indicator for a prep countdown and
    subsequent status (recording, running an automation, or picking a screen region).
    The whole point is to give the user time to switch focus to the target app - at
    which point AQx's own windows are no longer visible, so this needs its own
    always-on-top surface that survives that app switch."""

    def __init__(self):
        super().__init__()
        # NOTE: no Qt.Tool here - on macOS, Qt.Tool windows are treated as the owning
        # app's utility/tool palette and get hidden as soon as a *different* app
        # becomes active, which is exactly when this overlay matters most (the user
        # switching focus to the target app). Confirmed by testing both ways with
        # real app-activation switches. Plain WindowStaysOnTopHint + FramelessWindowHint
        # persists across app switches instead.
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        # NOTE: WA_TranslucentBackground on a frameless window silently fails to
        # composite on macOS/Qt 6.11 - the window reports isVisible() True but paints
        # nothing at all. Confirmed by isolating each flag individually. Solid/opaque
        # background avoids that entirely and is just as reliable.
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "background: rgb(20,20,24); color:#ffffff; font-size:32px; font-weight:bold;"
            " padding:18px 30px;"
        )

    def show_message(self, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        screen = self.screen()
        if screen is None:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            geo = screen.geometry()
            self.move(geo.center().x() - self.width() // 2, geo.top() + 80)
        # Only call show() the first time: re-showing/re-raising an already-visible
        # window on every countdown tick (once a second) brings AQx back to the front
        # each time on macOS, stealing focus straight back from whatever app the user
        # just switched to. Once shown, WindowStaysOnTopHint alone keeps it above
        # everything without repeating that.
        if not self.isVisible():
            self.show()
