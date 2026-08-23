from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..paths import REGIONS_DIR


@dataclass
class Region:
    """A named, reusable screen region, defined in global screen points (the same
    coordinate space Qt's QScreen geometry and macOS's Quartz CGRect both use, so no
    DPI conversion is needed between selecting a region and capturing it later)."""

    name: str
    x: float
    y: float
    width: float
    height: float

    def save(self) -> None:
        REGIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = REGIONS_DIR / f"{self.name}.json"
        path.write_text(json.dumps(asdict(self), indent=2))

    @staticmethod
    def load(name: str) -> "Region":
        path = REGIONS_DIR / f"{name}.json"
        return Region(**json.loads(path.read_text()))

    @staticmethod
    def list_names() -> list:
        REGIONS_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(p.stem for p in REGIONS_DIR.glob("*.json"))
