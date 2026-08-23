from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".aqx"
CONFIG_PATH = CONFIG_DIR / "settings.json"


@dataclass
class Settings:
    prep_delay_seconds: int = 5
    emergency_key: str = "backspace"
    playback_speed: float = 1.0
    default_repeat: int = 1
    last_flow_path: str = ""

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
