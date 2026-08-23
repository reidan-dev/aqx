from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..ocr.capture import capture_cgimage
from ..ocr.engine import read_text_from_cgimage
from ..ocr.region import Region
from ..recording.events import InputEvent
from ..recording.player import Player, StopFlag
from .model import Graph


class GraphRunner(QObject):
    status = Signal(str)
    node_started = Signal(str)
    node_updated = Signal(str)
    finished = Signal()

    def __init__(self, graph: Graph, stop_flag: StopFlag, recordings_dir: Path):
        super().__init__()
        self.graph = graph
        self.stop_flag = stop_flag
        self.recordings_dir = recordings_dir

    def run(self) -> None:
        start_node = next((n for n in self.graph.nodes.values() if n.type == "start"), None)
        if start_node is None:
            self.status.emit("No Start node in graph.")
            self.finished.emit()
            return

        repeat = self.graph.repeat  # 0 = infinite
        count = 0
        aborted = False
        while not self.stop_flag.is_set():
            if not self._run_once(start_node):
                aborted = True
                break
            count += 1
            if repeat != 0 and count >= repeat:
                break

        if not aborted:
            self.status.emit("Stopped." if self.stop_flag.is_set() else "Done.")
        self.finished.emit()

    def _run_once(self, start_node) -> bool:
        """Runs the flow once, start to end. Returns False if aborted (likely a cycle)."""
        current = start_node
        steps = 0
        while current is not None:
            if self.stop_flag.is_set():
                return True
            steps += 1
            if steps > 10000:
                self.status.emit("Aborted: too many steps (possible cycle).")
                return False
            self.node_started.emit(current.id)
            self._execute(current)
            self.node_updated.emit(current.id)
            conn = self.graph.outgoing(current.id, "out")
            current = self.graph.nodes.get(conn.to_node) if conn else None
        return True

    def _execute(self, node) -> None:
        if node.type == "start":
            return
        if node.type == "delay":
            seconds = float(node.props.get("seconds", 1.0))
            waited = 0.0
            while waited < seconds:
                if self.stop_flag.is_set():
                    return
                step = min(0.05, seconds - waited)
                time.sleep(step)
                waited += step
        elif node.type == "log":
            self.status.emit(str(node.props.get("message", "")))
        elif node.type == "recorded_block":
            name = node.props.get("recording")
            if not name:
                self.status.emit(f"Recorded Block '{node.id}' has no recording set.")
                return
            path = self.recordings_dir / f"{name}.json"
            if not path.exists():
                self.status.emit(f"Recording not found: {name}")
                return
            data = json.loads(path.read_text())
            events = [InputEvent.from_dict(e) for e in data.get("events", [])]
            repeat = int(node.props.get("repeat", 1))
            Player(events, self.stop_flag, speed=1.0).run(repeat=repeat)
        elif node.type == "ocr":
            self._execute_ocr(node)

    def _execute_ocr(self, node) -> None:
        region_name = node.props.get("region")
        if not region_name:
            self.status.emit(f"OCR '{node.id}' has no region set.")
            return
        try:
            region = Region.load(region_name)
        except FileNotFoundError:
            self.status.emit(f"OCR region not found: {region_name}")
            return

        image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
        value = read_text_from_cgimage(image_ref)
        node.props["last_value"] = value
        self.status.emit(f"OCR '{region_name}': {value!r}" if value is not None else f"OCR '{region_name}': no text detected")

        # Delay after the read, not a background timer - looping back to this node
        # (e.g. via the flow's Loops setting) is what gives "read every N seconds".
        interval = float(node.props.get("interval_seconds", 5.0))
        waited = 0.0
        while waited < interval:
            if self.stop_flag.is_set():
                return
            step = min(0.05, interval - waited)
            time.sleep(step)
            waited += step
