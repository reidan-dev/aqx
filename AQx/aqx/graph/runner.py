from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from ..ocr.capture import capture_cgimage
from ..ocr.engine import read_text_from_cgimage
from ..ocr.region import Region
from ..recording.events import InputEvent
from ..recording.player import Player, StopFlag
from .conditions import compare_values
from .model import Graph, Node

STEP_LIMIT = 10000


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
        self.variables: Dict[str, Any] = {}

    def run(self) -> None:
        start_node = next((n for n in self.graph.nodes.values() if n.type == "start"), None)
        if start_node is None:
            self.status.emit("No Start node in graph.")
            self.finished.emit()
            return

        self.variables = {}
        repeat = self.graph.repeat  # 0 = infinite
        count = 0
        aborted = False
        while not self.stop_flag.is_set():
            if not self._walk_chain(start_node):
                aborted = True
                break
            count += 1
            if repeat != 0 and count >= repeat:
                break

        if not aborted:
            self.status.emit("Stopped." if self.stop_flag.is_set() else "Done.")
        self.finished.emit()

    def _walk_chain(self, start: Node) -> bool:
        """Walks a linear sequence of nodes, following whichever output port each
        node's own execution selects (normally "out"; branch/loop nodes pick
        differently). Returns False only on the step-limit abort; True covers both a
        normal finish and an early stop via stop_flag."""
        current: Optional[Node] = start
        steps = 0
        while current is not None:
            if self.stop_flag.is_set():
                return True
            steps += 1
            if steps > STEP_LIMIT:
                self.status.emit("Aborted: too many steps (possible infinite loop).")
                return False
            self.node_started.emit(current.id)
            next_port = self._execute(current)
            self.node_updated.emit(current.id)
            if next_port is None:
                return True
            conn = self.graph.outgoing(current.id, next_port)
            current = self.graph.nodes.get(conn.to_node) if conn else None
        return True

    def _execute(self, node: Node) -> Optional[str]:
        """Executes one node. Returns the output port name the walk should continue
        from, or None to stop this chain here (either the node has no further
        continuation to pick, or stop_flag fired during a long-running step)."""
        if node.type == "start":
            return "out"

        if node.type == "delay":
            if not self._sleep_interruptible(float(node.props.get("seconds", 1.0))):
                return None
            return "out"

        if node.type == "log":
            self.status.emit(str(node.props.get("message", "")))
            return "out"

        if node.type == "recorded_block":
            self._execute_recorded_block(node)
            return None if self.stop_flag.is_set() else "out"

        if node.type == "ocr":
            self._execute_ocr(node)
            return None if self.stop_flag.is_set() else "out"

        if node.type == "set_variable":
            self._execute_set_variable(node)
            return "out"

        if node.type == "if":
            return self._execute_if(node)

        if node.type in ("for_loop", "while_loop", "until_loop"):
            return self._execute_loop(node)

        if node.type == "connector":
            return self._execute_connector(node)

        return "out"

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleeps up to `seconds`, checking stop_flag frequently. Returns False if
        interrupted early."""
        waited = 0.0
        while waited < seconds:
            if self.stop_flag.is_set():
                return False
            step = min(0.05, seconds - waited)
            time.sleep(step)
            waited += step
        return True

    def _execute_recorded_block(self, node: Node) -> None:
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

    def _read_ocr_region(self, region_name: str) -> Any:
        """Live-reads a named OCR region right now (capture + text recognition, no
        caching). Shared by a chain-walked OCR node's own execution and by any
        condition (If/While/Until) that references an OCR reading, so a loop
        condition always sees the screen as it is at each check - not a value cached
        from whenever some OCR node last happened to run as a regular chain step."""
        try:
            region = Region.load(region_name)
        except FileNotFoundError:
            self.status.emit(f"OCR region not found: {region_name}")
            return None
        image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
        return read_text_from_cgimage(image_ref)

    def _execute_ocr(self, node: Node) -> None:
        region_name = node.props.get("region")
        if not region_name:
            self.status.emit(f"OCR '{node.id}' has no region set.")
            return
        value = self._read_ocr_region(region_name)
        node.props["last_value"] = value
        self.status.emit(f"OCR '{region_name}': {value!r}" if value is not None else f"OCR '{region_name}': no text detected")

        # Delay after the read, not a background timer - looping back to this node
        # (e.g. via the flow's Loops setting, or a While/Until loop) is what gives
        # "read every N seconds".
        self._sleep_interruptible(float(node.props.get("interval_seconds", 5.0)))

    def _execute_set_variable(self, node: Node) -> None:
        name = node.props.get("name")
        if not name:
            self.status.emit(f"Set Variable '{node.id}' has no variable name.")
            return
        if node.props.get("source_type") == "ocr":
            source_node = self.graph.nodes.get(node.props.get("source_node"))
            value = source_node.props.get("last_value") if source_node is not None else None
        else:
            value = node.props.get("literal_value", "")
        self.variables[name] = value
        self.status.emit(f"${name} = {value!r}")

    def _resolve_condition_value(self, condition: dict) -> Any:
        source_type = condition.get("source_type")
        if source_type == "ocr":
            source_node = self.graph.nodes.get(condition.get("source_node"))
            region_name = source_node.props.get("region") if source_node is not None else None
            if not region_name:
                return None
            value = self._read_ocr_region(region_name)
            source_node.props["last_value"] = value  # keeps that OCR node's own summary in sync too
            return value
        if source_type == "variable":
            return self.variables.get(condition.get("source_name"))
        return None

    def _evaluate_condition(self, condition: dict) -> bool:
        value = self._resolve_condition_value(condition)
        return compare_values(value, condition.get("operator", "equals"), condition.get("compare_to", ""))

    def _execute_if(self, node: Node) -> str:
        cases = node.props.get("cases") or []
        for i, case in enumerate(cases):
            if self._evaluate_condition(case):
                return "If" if i == 0 else f"Elif {i}"
        return "Else"

    def _execute_loop(self, node: Node) -> Optional[str]:
        body_conn = self.graph.outgoing(node.id, "body")
        body_start = self.graph.nodes.get(body_conn.to_node) if body_conn else None

        if node.type == "for_loop":
            count = int(node.props.get("count", 0))
            i = 0
            while i < count:
                if self.stop_flag.is_set():
                    return None
                if body_start is not None and not self._walk_chain(body_start):
                    return None
                if self.stop_flag.is_set():
                    return None
                i += 1
            return "done"

        invert = node.type == "until_loop"  # Until repeats while the condition is still False
        guard = 0
        while True:
            if self.stop_flag.is_set():
                return None
            condition = node.props.get("condition", {})
            result = self._evaluate_condition(condition)
            should_continue = (not result) if invert else result
            if not should_continue:
                break
            if body_start is not None and not self._walk_chain(body_start):
                return None
            if self.stop_flag.is_set():
                return None
            guard += 1
            if guard > STEP_LIMIT:
                self.status.emit("Aborted: loop exceeded step limit.")
                return None
        return "done"

    def _execute_connector(self, node: Node) -> Optional[str]:
        """Fans out to every connected output in order, walking each one's chain to
        completion before moving to the next. A Connector has no "after the fan-out"
        continuation of its own - it always ends the chain it was reached from."""
        for port in node.outputs:
            if self.stop_flag.is_set():
                return None
            conn = self.graph.outgoing(node.id, port.name)
            target = self.graph.nodes.get(conn.to_node) if conn else None
            if target is not None and not self._walk_chain(target):
                return None
        return None
