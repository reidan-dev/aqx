from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".aqx"
CONFIG_PATH = CONFIG_DIR / "settings.json"


@dataclass
class Settings:
    prep_delay_seconds: int = 5
    emergency_key: str = "f12"
    playback_speed: float = 1.0
    default_repeat: int = 1
    last_flow_path: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    mouse_guard_enabled: bool = True
    # px/sec of real mouse movement (smoothed over ~80ms) that counts as "aggressive" -
    # calibrated from one real session: ordinary movement peaked ~9700 px/sec, a
    # deliberate fast swipe reached ~20300 px/sec. Expect to retune per machine/mouse.
    mouse_guard_threshold: float = 15000.0
    # What triggering the guard actually does: "stop" ends the run outright (the
    # original behavior); "pause" just pauses it, same as the pause hotkey, so a
    # sudden grab for the real mouse doesn't lose an otherwise-fine run - resume
    # normally once you're back in control.
    mouse_guard_action: str = "stop"

    @staticmethod
    def load() -> "Settings":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                return Settings(**{**asdict(Settings()), **data})
            except Exception:
                return Settings()
        return Settings()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))
