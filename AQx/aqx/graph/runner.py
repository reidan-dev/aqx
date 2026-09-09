from __future__ import annotations

import inspect
import json
import random
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal
from pynput import keyboard

from ..config import Settings
from ..ocr.capture import capture_cgimage
from ..ocr.engine import read_text_from_cgimage
from ..ocr.extract import apply_extract_pattern
from ..ocr.region import Region
from ..recording.events import InputEvent
from ..recording.player import PauseFlag, Player, StopFlag, key_from_name
from ..telegram import TelegramError, send_message as send_telegram_message
from .conditions import evaluate_condition
from .model import Connection, Graph, Node
from .nodes import format_duration_seconds, parse_duration_seconds

STEP_LIMIT = 10000
VAR_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class _LoopBreak(Exception):
    """Raised by an Exit Loop block whose condition is true. Caught by the nearest
    enclosing _execute_loop call, i.e. the direct parent loop - this falls out
    naturally from _execute_loop/_walk_chain's own call recursion for nested loops,
    with no separate loop-stack bookkeeping needed."""


class _CodeStopped(Exception):
    """Raised internally when a Code block's sleep()/play() helper notices stop_flag
    fired mid-call, so the user's own script unwinds immediately instead of running
    on to completion - caught only by _execute_code, right where it's raised."""


class SkillRef:
    """A lightweight reference to one row (1-based position) in the flow's single
    Skills block - identifies which region/key to check, but carries no cached
    state itself. is_ready()/press()/wait_and_press() each do a fresh live
    screen-read at the moment they're called, so repeated calls are independent -
    there's no "already pressed" snapshot to guard, each call is its own
    check-and-act."""

    def __init__(self, index: int, read_ready, press_key, waiter):
        self.index = index  # 1-based row position - this is skills.s{index}
        self._read_ready = read_ready  # callable() -> bool, a live re-read
        self._press_key = press_key  # callable() -> None
        self._waiter = waiter  # callable(seconds) -> bool - interruptible sleep, like sleep()

    def is_ready(self) -> bool:
        """Live-checks this slot right now - True if off cooldown."""
        return self._read_ready()

    def press(self) -> bool:
        """Live-checks this slot and taps its key if it's off cooldown. Returns
        whether it was ready (and so got pressed)."""
        ready = self._read_ready()
        if ready:
            self._press_key()
        return ready

    def wait_and_press(self, timeout: Optional[float] = None, poll: float = 0.2) -> bool:
        """Waits, re-checking every `poll` seconds, until this slot is off
        cooldown, then presses it - for a fire-in-order rotation where this
        specific slot should be waited on rather than skipped. Gives up and
        returns False if `timeout` seconds pass first (None waits indefinitely).
        Like sleep()/play()/keystroke(), a Stop mid-wait unwinds the whole script
        rather than returning normally."""
        elapsed = 0.0
        while True:
            if self.press():
                return True
            if timeout is not None and elapsed >= timeout:
                return False
            if not self._waiter(poll):
                raise _CodeStopped()
            elapsed += poll

    def spam_press(self, poll: float = 0.05, max_presses: Optional[int] = None) -> int:
        """The opposite of wait_and_press: presses this slot immediately, then
        keeps re-checking every `poll` seconds and pressing again for as long as
        it's still off cooldown, stopping the instant a live re-read finds it on
        cooldown (or, if given, after `max_presses` presses land). Useful for a
        skill that stays clickable across several rapid taps before it actually
        goes on cooldown. Returns how many presses landed. Like the other
        blocking helpers, a Stop mid-wait unwinds the whole script rather than
        returning normally."""
        count = 0
        while self.press():
            count += 1
            if max_presses is not None and count >= max_presses:
                break
            if not self._waiter(poll):
                raise _CodeStopped()
        return count

    def __bool__(self) -> bool:
        return self.is_ready()

    def __repr__(self) -> str:
        return f"skills.s{self.index}"


class SkillsHandle:
    """What skills() returns - one instance per call, exposing the flow's single
    Skills block's rows as .s1, .s2, ... in row order (independent of each row's
    own Key), each a SkillRef. Accessing .sN past the last configured row raises a
    clear error instead of a bare AttributeError."""

    def __init__(self, refs: "list[SkillRef]", waiter):
        self._refs = refs
        self._waiter = waiter
        for ref in refs:
            setattr(self, f"s{ref.index}", ref)

    def __getattr__(self, name: str):
        if name.startswith("s") and name[1:].isdigit():
            raise AttributeError(
                f"skills.{name} doesn't exist - this flow's Skills block only has "
                f"{len(self._refs)} slot(s) (s1..s{len(self._refs)})."
            )
        raise AttributeError(name)

    def press_ready(self, refs: "list[SkillRef]", poll: float = 0.2) -> "SkillRef":
        """Priority rotation: scans `refs` in order and presses the first one
        that's off cooldown, skipping (never waiting on) any still on cooldown -
        so a lower-priority skill fires ahead of a higher-priority one that isn't
        ready yet. If none are ready, waits `poll` seconds and rescans from the
        top. Blocks until it presses something (or Stop unwinds the script);
        returns the SkillRef it pressed."""
        while True:
            for ref in refs:
                if ref.press():
                    return ref
            if not self._waiter(poll):
                raise _CodeStopped()


class GraphRunner(QObject):
    status = Signal(str)
    error = Signal(str, str)  # node_id, message - a Code block exception (also stops the run)
    node_started = Signal(str)
    node_updated = Signal(str)
    finished = Signal()
    controls_registered = Signal(list)  # [{"name", "options", "current"}, ...] - once per run()

    def __init__(
        self,
        graph: Graph,
        stop_flag: StopFlag,
        recordings_dir: Path,
        pause_flag: Optional[PauseFlag] = None,
        settings: Optional[Settings] = None,
    ):
        super().__init__()
        self.graph = graph
        self.stop_flag = stop_flag
        self.pause_flag = pause_flag or PauseFlag()
        self.recordings_dir = recordings_dir
        self.settings = settings or Settings()
        self.variables: Dict[str, Any] = {}
        self._logic_eval_stack: set = set()  # guards against a Logic block cycle (A -> B -> A)
        self._telegram_send_counts: Dict[str, int] = {}  # node id -> successful sends this run()
        self._keyboard: Optional[keyboard.Controller] = None  # lazy - only a Code block's keystroke() needs it
        self._control_sources: Dict[str, tuple] = {}  # variable name -> (Controls node, index into its list)
        self._skills_node: Optional[Node] = None  # the flow's single Skills block, if any
        self._skill_slots: list = []  # [{"region", "key"}, ...] in row order - row i is skills.s{i+1}
        self._run_started_at: float = 0.0  # time.monotonic() at the top of run() - what a Turn Off block counts from
        self._turn_off_armed: bool = False  # a Turn Off block only arms its timer once per run(), even if reached again
        self._turn_off_timer: Optional[threading.Timer] = None
        self._code_error = False  # a Code block exception fired this run - suppresses the redundant Stopped./Done. message
        # Per Code-block-node state for the setup()/loop() split - a node's namespace
        # (so functions/variables setup() declares are still there for loop(), on
        # this call and every later one) plus whether its setup() has already run
        # this run() - both keyed by node id since a flow can have more than one
        # Code block, and both reset per run() (see run()), not per lap: setup()
        # means "once ever this run", regardless of how many laps or how many times
        # an inner loop revisits this node.
        self._code_namespaces: Dict[str, Dict[str, Any]] = {}
        self._code_setup_done: set = set()

    def _wait_while_paused(self) -> bool:
        """Blocks while paused, still watching stop_flag. Returns False if stopped
        while waiting, so the caller can bail the same way it would for a plain stop."""
        while self.pause_flag.is_set():
            if self.stop_flag.is_set():
                return False
            time.sleep(0.05)
        return True

    def _interpolate(self, text: str) -> str:
        """Replaces every $name in text with that variable's current value (set
        earlier in the flow by a Set Variable block). An unrecognized $name is left
        untouched, so a typo shows up as a literal "$typo" rather than silently
        vanishing."""

        def replace(match: "re.Match") -> str:
            name = match.group(1)
            if name in self.variables:
                return str(self.variables[name])
            return match.group(0)

        return VAR_REF_RE.sub(replace, text)

    def _emit_tap(self, conn: Optional[Connection]) -> None:
        """A wire can carry an optional Log "tap" - a message logged whenever that
        specific connection is traversed, without becoming a real step in the chain
        (the wire's own routing is completely untouched)."""
        if conn is not None and conn.log_message:
            self.status.emit(self._interpolate(conn.log_message))

    def _register_controls(self) -> None:
        """Seeds `variables` from every Controls block's current value and tells the
        UI what to show in the floating control - once per run(), regardless of
        whether any Controls block is actually wired into the flow, so a value can
        be changed before the flow ever reaches (or even if it never reaches) that
        block."""
        self._control_sources = {}
        registered = []
        for node in self.graph.nodes.values():
            if node.type != "controls":
                continue
            entries = node.props.get("controls") or []
            for i, entry in enumerate(entries):
                name = entry.get("name")
                options = entry.get("options") or []
                if not name or not options:
                    continue
                current = entry.get("current")
                if current not in options:
                    current = options[0]
                    entry["current"] = current
                self.variables[name] = current
                self._control_sources[name] = (node, i)
                registered.append({"name": name, "options": list(options), "current": current})
        self.controls_registered.emit(registered)

    def set_control_value(self, name: str, value: str) -> None:
        """Called from the floating control (main thread) when the user picks a new
        value - updates the live variable a running flow reads, and writes it back
        into the source Controls block's props so it's what Save writes to disk and
        what the next run starts from."""
        entry = self._control_sources.get(name)
        if entry is None:
            return
        node, index = entry
        options = (node.props.get("controls") or [])[index].get("options") or []
        if value not in options:
            return
        node.props["controls"][index]["current"] = value
        self.variables[name] = value
        self.status.emit(f"${name} = {value!r} (changed from the floating control)")

    def _register_skills(self) -> None:
        """Finds the flow's Skills block (the editor only allows one; if more than
        one somehow exists - e.g. an older flow file - the first one found wins
        and the rest are ignored) - once per run(), same as _register_controls, so
        skills() from a Code block and this block's own chain execution both
        resolve the same rows regardless of where in the flow (or whether at all)
        it's wired in."""
        self._skills_node = None
        self._skill_slots = []
        for node in self.graph.nodes.values():
            if node.type != "skills":
                continue
            self._skills_node = node
            self._skill_slots = [
                {"region": e.get("region"), "key": e.get("key")}
                for e in (node.props.get("skills") or [])
                if e.get("region") and e.get("key")
            ]
            break

    def _read_slot_ready(self, index: int) -> bool:
        """Live-reads row `index`'s (1-based) region - True if it came back empty
        (off cooldown). Pure query, no key press - also updates the "s{index}_ready"
        variable and the Skills block's on-face last_state, same as a press would."""
        slot = self._skill_slots[index - 1]
        value = self._read_ocr_region(slot["region"])
        ready = value is None or not str(value).strip()
        self._skills_node.props.setdefault("last_state", {})[f"s{index}"] = "ready" if ready else "cooldown"
        self.variables[f"s{index}_ready"] = ready
        return ready

    def _press_slot_key(self, index: int) -> None:
        """Taps row `index`'s (1-based) configured key, unconditionally - callers
        decide whether it's actually ready first. Blocks on pause right before the
        real keystroke goes out - this is the one choke point every press reaches
        (the Skills block's own chain execution below, and a Code block's
        skills().press()/press_ready()/wait_and_press()), none of which otherwise
        pass through a pause check of their own."""
        if not self._wait_while_paused():
            return
        if self._keyboard is None:
            self._keyboard = keyboard.Controller()
        key = key_from_name(self._skill_slots[index - 1]["key"])
        self._keyboard.press(key)
        self._keyboard.release(key)

    def _execute_skills(self, node: Node) -> Optional[str]:
        for index in range(1, len(self._skill_slots) + 1):
            if self.stop_flag.is_set():
                return None
            if not self._wait_while_paused():
                return None
            if self._read_slot_ready(index):
                self._press_slot_key(index)
        return None if self.stop_flag.is_set() else "out"

    def _arm_turn_off(self, node: Node) -> None:
        """Schedules a background timer that stops the run once `duration` has
        passed since run() started - counted from run start, not from when this
        block is reached, and only armed once per run() even if reached again
        (e.g. inside a loop). Runs on its own thread via threading.Timer, so it
        fires regardless of what the rest of the flow is doing in the meantime -
        the same stop_flag every other stop path already uses."""
        if self._turn_off_armed:
            return
        self._turn_off_armed = True
        duration = str(node.props.get("duration", ""))
        seconds = parse_duration_seconds(duration)
        if seconds is None:
            self.status.emit(f"Turn Off: invalid duration {duration!r} - ignored.")
            return
        remaining = max(0.0, (self._run_started_at + seconds) - time.monotonic())
        self.status.emit(f"Turn Off armed: stopping in {format_duration_seconds(remaining)}.")
        self._turn_off_timer = threading.Timer(remaining, self._fire_turn_off)
        self._turn_off_timer.daemon = True
        self._turn_off_timer.start()

    def _fire_turn_off(self) -> None:
        self.status.emit("Turn Off timer elapsed - stopping.")
        self.stop_flag.set()

    def run(self) -> None:
        start_node = next((n for n in self.graph.nodes.values() if n.type == "start"), None)
        if start_node is None:
            self.status.emit("No Start node in graph.")
            self.finished.emit()
            return

        self.variables = {}
        self._register_controls()
        self._register_skills()
        self._run_started_at = time.monotonic()
        self._turn_off_armed = False
        self._turn_off_timer = None
        self._code_error = False
        # Reset once per run(), not per loop iteration or per flow-repeat, so "max
        # sends" caps the total across the whole run - exactly the "despite being
        # called many times" case a loop body creates.
        self._telegram_send_counts = {}
        self._code_namespaces = {}
        self._code_setup_done = set()
        repeat = self.graph.repeat  # 0 = infinite
        count = 0
        aborted = False
        try:
            while not self.stop_flag.is_set():
                # A flow that's little more than Start -> Recorded Block gives no
                # other visible sign a lap ever finished and a new one began -
                # recorded-block playback doesn't log anything of its own, so
                # without this a correctly looping run and a silently-stuck one
                # look identical. Skipped for a single-pass run (repeat == 1)
                # where lap numbering isn't informative.
                if repeat != 1:
                    self.status.emit(f"Lap {count + 1}" + ("" if repeat == 0 else f" of {repeat}"))
                try:
                    if not self._walk_chain(start_node):
                        aborted = True
                        break
                except _LoopBreak:
                    # An Exit Loop block fired outside of any loop - nothing to
                    # break out of, so just treat it as the end of this pass and
                    # keep going.
                    self.status.emit("Exit Loop block used outside a loop - ignored.")
                count += 1
                if repeat != 0 and count >= repeat:
                    break
        finally:
            # Whatever ended the run - natural completion, Stop, the step limit -
            # a still-pending Turn Off timer has nothing left to stop.
            if self._turn_off_timer is not None:
                self._turn_off_timer.cancel()

        if not aborted and not self._code_error:
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
            if not self._wait_while_paused():
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
            self._emit_tap(conn)
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
            self.status.emit(self._interpolate(str(node.props.get("message", ""))))
            return "out"

        if node.type == "telegram":
            return "out" if self._execute_telegram(node) else None

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

        if node.type == "logic":
            result = self._evaluate_condition(node.props.get("condition", {}))
            node.props["last_value"] = result
            return "out"

        if node.type == "loop_exit":
            if self._evaluate_condition(node.props.get("condition", {})):
                raise _LoopBreak()
            return "out"

        if node.type == "code":
            return "out" if self._execute_code(node) else None

        if node.type == "controls":
            # Its values are already live in `variables` via _register_controls at
            # run start - reaching it in the chain doesn't do anything further.
            return "out"

        if node.type == "skills":
            return self._execute_skills(node)

        if node.type == "turn_off":
            self._arm_turn_off(node)
            return "out"

        return "out"

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleeps up to `seconds`, checking stop_flag and pause_flag frequently.
        Returns False if interrupted early. Time spent paused doesn't count against
        `seconds`, so a paused delay simply resumes with whatever was left."""
        waited = 0.0
        while waited < seconds:
            if self.stop_flag.is_set():
                return False
            if not self._wait_while_paused():
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
        speed = float(node.props.get("speed", 1.0))
        Player(events, self.stop_flag, speed=speed, pause_flag=self.pause_flag).run(repeat=repeat)

    _OCR_READ_ATTEMPTS = 3
    _OCR_RETRY_DELAY = 0.08

    def _read_ocr_region(self, region_name: str) -> Any:
        """Live-reads a named OCR region right now (capture + text recognition, no
        caching). Shared by a chain-walked OCR node's own execution and by any
        condition (If/While/Until) that references an OCR reading, so a loop
        condition always sees the screen as it is at each check - not a value cached
        from whenever some OCR node last happened to run as a regular chain step.

        Retries a few times on a miss, re-capturing each attempt rather than
        re-running Vision on the same pixels - a single frame's rendering (glow,
        animation, compositing, sub-pixel anti-aliasing on the digits) can make
        Vision return nothing even though the text is clearly there; the next
        frame's capture is often just different enough to read cleanly."""
        try:
            region = Region.load(region_name)
        except FileNotFoundError:
            self.status.emit(f"OCR region not found: {region_name}")
            return None
        for attempt in range(self._OCR_READ_ATTEMPTS):
            image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
            text = read_text_from_cgimage(image_ref) if image_ref is not None else None
            if text is not None:
                return text
            if attempt < self._OCR_READ_ATTEMPTS - 1:
                if self.stop_flag.is_set():
                    return None
                time.sleep(self._OCR_RETRY_DELAY)
        return None

    def _apply_extract(self, node: Node, value: Any) -> Any:
        pattern = node.props.get("extract_pattern")
        result, error = apply_extract_pattern(value, pattern)
        if error:
            self.status.emit(f"OCR '{node.id}': invalid extract pattern ({error}) - using raw text.")
        return result

    def _execute_ocr(self, node: Node) -> None:
        region_name = node.props.get("region")
        if not region_name:
            self.status.emit(f"OCR '{node.id}' has no region set.")
            return
        value = self._read_ocr_region(region_name)
        value = self._apply_extract(node, value)
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

    def _execute_telegram(self, node: Node) -> bool:
        """Sends a message via the Telegram bot, honoring an optional per-block "max
        sends" cap (so a block reached many times, e.g. inside a loop, doesn't spam
        one notification per iteration) and an optional "wait before sending" delay.
        A network call, unlike Log - failures (missing bot config, no connection,
        Telegram rejecting the request) are logged the same way any other status
        message is, but never abort the flow, matching Log's own "never blocks
        anything" behavior. Returns False only if the wait was interrupted by a
        stop - a skipped-due-to-limit or failed-to-send message still lets the flow
        continue normally."""
        max_sends = int(node.props.get("max_sends", 0))
        sent_so_far = self._telegram_send_counts.get(node.id, 0)
        if max_sends and sent_so_far >= max_sends:
            self.status.emit(f"Telegram: send limit ({max_sends}) already reached, skipping.")
            return True

        wait_seconds = float(node.props.get("wait_seconds", 0.0))
        if wait_seconds > 0 and not self._sleep_interruptible(wait_seconds):
            return False

        message = self._interpolate(str(node.props.get("message", "")))
        try:
            send_telegram_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, message)
            self._telegram_send_counts[node.id] = sent_so_far + 1
            self.status.emit(f"Telegram: sent {message!r}")
        except TelegramError as exc:
            self.status.emit(f"Telegram: failed to send ({exc})")
        return True

    def _resolve_leaf_value(self, condition: dict) -> Any:
        source_type = condition.get("source_type")
        if source_type == "ocr":
            source_node = self.graph.nodes.get(condition.get("source_node"))
            region_name = source_node.props.get("region") if source_node is not None else None
            if not region_name:
                return None
            value = self._read_ocr_region(region_name)
            value = self._apply_extract(source_node, value)
            source_node.props["last_value"] = value  # keeps that OCR node's own summary in sync too
            return value
        if source_type == "variable":
            return self.variables.get(condition.get("source_name"))
        if source_type == "logic":
            source_node = self.graph.nodes.get(condition.get("source_node"))
            if source_node is None:
                return None
            if source_node.id in self._logic_eval_stack:
                self.status.emit(f"Logic block cycle detected at '{source_node.id}' - treated as False.")
                return False
            self._logic_eval_stack.add(source_node.id)
            try:
                result = self._evaluate_condition(source_node.props.get("condition", {}))
            finally:
                self._logic_eval_stack.discard(source_node.id)
            source_node.props["last_value"] = result  # keeps that Logic node's own summary in sync too
            return result
        return None

    def _evaluate_condition(self, condition: dict) -> bool:
        """A condition is either a single leaf or an AND/OR group of clauses (see
        conditions.evaluate_condition); each leaf's actual value is resolved live via
        _resolve_leaf_value at the moment it's needed."""
        return evaluate_condition(condition, self._resolve_leaf_value)

    def _execute_if(self, node: Node) -> str:
        cases = node.props.get("cases") or []
        for i, case in enumerate(cases):
            if self._evaluate_condition(case):
                return "If" if i == 0 else f"Elif {i}"
        return "Else"

    def _execute_loop(self, node: Node) -> Optional[str]:
        body_conn = self.graph.outgoing(node.id, "body")
        body_start = self.graph.nodes.get(body_conn.to_node) if body_conn else None

        try:
            if node.type == "for_loop":
                count = int(node.props.get("count", 0))
                i = 0
                while i < count:
                    if self.stop_flag.is_set():
                        return None
                    self._emit_tap(body_conn)
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
                self._emit_tap(body_conn)
                if body_start is not None and not self._walk_chain(body_start):
                    return None
                if self.stop_flag.is_set():
                    return None
                guard += 1
                if guard > STEP_LIMIT:
                    self.status.emit("Aborted: loop exceeded step limit.")
                    return None
            return "done"
        except _LoopBreak:
            return "done"

    def _execute_connector(self, node: Node) -> Optional[str]:
        """Fans out to every wire attached to its single "out" port, in the order
        they were connected, walking each one's chain to completion before moving to
        the next. A Connector has no "after the fan-out" continuation of its own - it
        always ends the chain it was reached from."""
        for conn in self.graph.outgoing_all(node.id, "out"):
            if self.stop_flag.is_set():
                return None
            self._emit_tap(conn)
            target = self.graph.nodes.get(conn.to_node)
            if target is not None and not self._walk_chain(target):
                return None
        return None

    def _execute_code(self, node: Node) -> bool:
        """Runs a Code node's setup()/functions()/loop() split with a small fixed
        namespace - `variables` is the same dict every other block reads/writes, so
        the script's edits are visible immediately to whatever runs next.
        setup_code runs once - the first time this specific node is reached in this
        run() (see _code_setup_done) - and functions_code then code (the loop()
        body) run on every visit, including that first one, always functions_code
        before code, right after setup_code. All three share one namespace dict,
        persisted per node id in _code_namespaces, so a function or variable any of
        them declares is still there for whatever runs after it - this call and
        every later one - not just local to a single exec(). functions_code exists
        purely to keep helper `def`s out of the way of loop()'s main logic; nothing
        about it is special beyond running first each lap. Branching/looping within
        any of the three is left to normal Python control flow rather than modeled
        as extra output ports - there's always exactly one continuation ("out")
        once the loop() body returns. An exception is logged with its traceback
        (via `status`, and separately via `error` so the mini toolbar can surface it
        even once minimized/backgrounded), then stops the run - same as a Stop
        mid-sleep()/play()/keystroke() unwinding the script early via _CodeStopped,
        just via stop_flag instead so it also aborts any further laps rather than
        just this chain."""
        setup_code = str(node.props.get("setup_code", ""))
        functions_code = str(node.props.get("functions_code", ""))
        code = str(node.props.get("code", ""))
        if not setup_code.strip() and not functions_code.strip() and not code.strip():
            return True

        def ocr_helper(region_name: str, pattern: Optional[str] = None) -> Any:
            value = self._read_ocr_region(region_name)
            if pattern:
                value, error = apply_extract_pattern(value, pattern)
                if error:
                    self.status.emit(f"Code '{node.id}': invalid extract pattern in ocr() call ({error}) - using raw text.")
            return value

        def ocr_lines_helper(region_name: str, pattern: Optional[str] = None) -> Optional[list]:
            """ocr_lines(region, pattern=None) - like ocr(), but reads the region as
            multiple lines (blank lines dropped) and returns a list, one entry per
            line, instead of one combined string. With a pattern, that same regex is
            applied to each line independently - a line with no match becomes None
            in the list (same "no match" convention as a single ocr() call), rather
            than one pattern matched once against the whole block. Assign the
            result straight to a variable, e.g. variables["items"] = ocr_lines(...)."""
            text = self._read_ocr_region(region_name)
            if text is None:
                return None
            lines = [line for line in text.split("\n") if line.strip()]
            if not pattern:
                return lines
            results = []
            for line in lines:
                result, error = apply_extract_pattern(line, pattern)
                if error:
                    self.status.emit(f"Code '{node.id}': invalid extract pattern in ocr_lines() call ({error}) - using raw lines.")
                    return lines
                results.append(result)
            return results

        def play_helper(recording_name: str, repeat: int = 1, speed: float = 1.0) -> None:
            path = self.recordings_dir / f"{recording_name}.json"
            if not path.exists():
                self.status.emit(f"Code '{node.id}': recording not found: {recording_name}")
                return
            data = json.loads(path.read_text())
            events = [InputEvent.from_dict(e) for e in data.get("events", [])]
            Player(events, self.stop_flag, speed=float(speed), pause_flag=self.pause_flag).run(repeat=int(repeat))
            if self.stop_flag.is_set():
                raise _CodeStopped()

        def log_helper(*values: Any) -> None:
            self.status.emit(" ".join(str(v) for v in values))

        def sleep_helper(seconds: float) -> None:
            if not self._sleep_interruptible(float(seconds)):
                raise _CodeStopped()

        def telegram_helper(message: Any) -> None:
            try:
                send_telegram_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, str(message))
                self.status.emit(f"Telegram: sent {message!r}")
            except TelegramError as exc:
                self.status.emit(f"Telegram: failed to send ({exc})")

        def keystroke_helper(keys: Any, min_wait: float = 0.0, max_wait: Optional[float] = None) -> None:
            """Taps each key in `keys` (a string of single characters, or a list
            mixing characters with named keys like "enter"/"cmd"/"tab") one at a
            time, waiting after every tap - a fixed `min_wait` seconds, or a fresh
            random duration in [min_wait, max_wait) per tap when `max_wait` is
            given."""
            if self._keyboard is None:
                self._keyboard = keyboard.Controller()
            for token in keys:
                if self.stop_flag.is_set():
                    raise _CodeStopped()
                key = key_from_name(token)
                self._keyboard.press(key)
                self._keyboard.release(key)
                wait = min_wait if max_wait is None else random.uniform(min_wait, max_wait)
                if wait > 0 and not self._sleep_interruptible(wait):
                    raise _CodeStopped()

        def skills_helper() -> SkillsHandle:
            """skills() - one instance per call, exposing the flow's single Skills
            block's rows as .s1, .s2, ... in row order. See SkillsHandle/SkillRef."""
            refs = [
                SkillRef(
                    index,
                    read_ready=lambda i=index: self._read_slot_ready(i),
                    press_key=lambda i=index: self._press_slot_key(i),
                    waiter=self._sleep_interruptible,
                )
                for index in range(1, len(self._skill_slots) + 1)
            ]
            if not refs:
                self.status.emit("skills(): no Skills block (or no rows in it) found in this flow.")
            return SkillsHandle(refs, waiter=self._sleep_interruptible)

        _SYNC_CALL_RE = re.compile(r'\b(?:sync|save)\(\s*([A-Za-z_]\w*)\s*\)')

        def sync_helper(value: Any) -> Any:
            """sync(x) / save(x) - a plain checkpoint: writes x's current value onto
            this Code block (node.props["sync_vars"][x's name]), so it's included
            whenever the flow is next saved and survives Stop -> Run again (even an
            app restart, once saved). Must be called with a single bare variable
            name - the name itself isn't passed as an argument, it's recovered from
            the call site's own source line (there's no other way to know "x" was
            the name behind the value that got passed in).

            Nothing but checkpointing happens here - restoring a persisted value
            back into setup()'s variable on the *next* run is handled separately,
            automatically, right after setup_code runs (see the "auto-restore"
            block below), using whatever this checkpointed last. That split is
            what makes call order not matter: sync(x) after modifying x always
            saves the new value, never the old one."""
            frame = inspect.currentframe().f_back
            filename = frame.f_code.co_filename
            if filename.endswith(" setup>"):
                source = setup_code
            elif filename.endswith(" functions>"):
                source = functions_code
            else:
                source = code
            lines = source.splitlines()
            line = lines[frame.f_lineno - 1] if 0 <= frame.f_lineno - 1 < len(lines) else ""
            match = _SYNC_CALL_RE.search(line)
            if not match:
                raise ValueError(
                    "sync()/save() must be called with a single bare variable name, e.g. sync(x) - "
                    f"couldn't find that on the call's source line: {line.strip()!r}"
                )
            name = match.group(1)
            node.props.setdefault("sync_vars", {})[name] = value
            return value

        # Persisted per node id (see __init__/run()) so setup_code's functions/
        # variables are still there for code on this call and every later one - the
        # helper bindings themselves are rebound fresh each call (harmless - they
        # just close over self/node, no per-call state of their own) so they always
        # point at this call's closures.
        namespace = self._code_namespaces.setdefault(node.id, {})
        namespace.update({
            "variables": self.variables,
            "ocr": ocr_helper,
            "ocr_lines": ocr_lines_helper,
            "play": play_helper,
            "log": log_helper,
            "sleep": sleep_helper,
            "telegram": telegram_helper,
            "keystroke": keystroke_helper,
            "skills": skills_helper,
            "sync": sync_helper,
            "save": sync_helper,
            "stop_requested": lambda: self.stop_flag.is_set(),
        })
        run_setup = node.id not in self._code_setup_done
        self._code_setup_done.add(node.id)
        try:
            if run_setup and setup_code.strip():
                exec(compile(setup_code, f"<code block {node.id} setup>", "exec"), namespace)
            if run_setup:
                # Auto-restore: whatever a previous run last sync()/save()'d for
                # this node overwrites setup_code's just-assigned value here - so
                # the "declare a default, then sync() it once it changes" pattern
                # in this Code block's docstring/reference actually results in
                # "starts from wherever it was left" on the next run, with no
                # extra restore call needed. Only happens the one time setup_code
                # itself runs, not every lap - after this, ordinary lap-to-lap
                # persistence is just normal Python (the namespace dict itself).
                for name, value in node.props.get("sync_vars", {}).items():
                    namespace[name] = value
            if functions_code.strip():
                exec(compile(functions_code, f"<code block {node.id} functions>", "exec"), namespace)
            if code.strip():
                exec(compile(code, f"<code block {node.id} loop>", "exec"), namespace)
        except _CodeStopped:
            return False
        except Exception:
            message = f"Code '{node.id}' error:\n{traceback.format_exc()}"
            self.status.emit(message)
            self.error.emit(node.id, message)
            self._code_error = True
            self.stop_flag.set()
            return False
        return True
