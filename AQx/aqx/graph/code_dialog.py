from __future__ import annotations

import html
import keyword
import re
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QRegularExpression, QStringListModel, Qt
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .model import Graph, Node

HELPERS = ("variables", "ocr", "play", "log", "sleep", "telegram", "keystroke", "skills", "stop_requested")

AUTOCOMPLETE_NOTE = 'While typing, this suggests names already used elsewhere in this flow.'

# tab label -> (signature, description, example, autocomplete note or None)
HELPER_DOCS: List[Tuple[str, str, str, str, Optional[str]]] = [
    (
        "variables",
        "variables",
        "Shared dict - the same one Set Variable and If read and write. Any key you "
        "set is visible to every block that runs after this one.",
        'variables["count"] = variables.get("count", 0) + 1',
        AUTOCOMPLETE_NOTE,
    ),
    (
        "ocr",
        "ocr(region, pattern=None)",
        "Live-reads a named OCR region right now (not cached). An optional regex "
        "pattern extracts part of the reading - the first capture group if there is "
        "one, else the whole match.",
        'text = ocr("region_name")\nnum = ocr("score_region", r"(\\d+)")',
        AUTOCOMPLETE_NOTE,
    ),
    (
        "play",
        "play(recording, repeat=1)",
        "Plays back a saved mouse/keyboard recording, same as a Recorded Block.",
        'play("farm_loop", repeat=3)',
        AUTOCOMPLETE_NOTE,
    ),
    (
        "log",
        "log(*values)",
        "Writes to the Execution Log - the args are joined with a space, same as print().",
        'log("checkpoint reached, count is", variables["count"])',
        None,
    ),
    (
        "sleep",
        "sleep(seconds)",
        "Interruptible delay, same as a Delay block - respects Stop/Pause instead of "
        "blocking the whole app.",
        "sleep(1.5)",
        None,
    ),
    (
        "telegram",
        "telegram(message)",
        "Sends a message via your configured Telegram bot (set in Settings > "
        "Preferences). A send failure is logged but doesn't stop the flow.",
        "telegram(f\"count is now {variables['count']}\")",
        AUTOCOMPLETE_NOTE,
    ),
    (
        "keystroke",
        "keystroke(keys, min_wait=0, max_wait=None)",
        "Taps each key in a string or list (characters or named keys like \"enter\"/"
        "\"cmd\"/\"tab\"), waiting after every tap - a fixed min_wait, or a fresh "
        "random duration in [min_wait, max_wait) per tap when max_wait is given.",
        'keystroke("tuv", 0.5)\nkeystroke(["enter", "cmd"], 0.5, 1)',
        None,
    ),
    (
        "skills",
        "skills()",
        "Returns a handle to the flow's one Skills block, exposing its rows as "
        ".s1, .s2, ... in the same order as the block's rows (row 1 is .s1, row 2 "
        "is .s2, and so on - independent of each row's own Key). Each .sN supports "
        ".is_ready() (just looks), .press() (taps the key if it was ready), and "
        ".wait_and_press(timeout=None, poll=0.2) (blocks on that one specific slot "
        "until it's off cooldown, then presses it). For a priority rotation across "
        "several slots - press whichever's ready first, skipping ones still on "
        "cooldown rather than waiting on them - call "
        ".press_ready([...], poll=0.2) on the handle itself.",
        's = skills()\n'
        's.s2.press()                          # one-liner: press if ready\n\n'
        'if s.s3.is_ready():                    # or check first, then decide\n'
        '    log("about to use s3")\n'
        '    s.s3.press()\n\n'
        '# priority rotation - fires s4 if ready, else s3, else s2, whichever\'s\n'
        '# actually off cooldown first - never stalls on one specific slot\n'
        'while not stop_requested():\n'
        '    s.press_ready([s.s4, s.s3, s.s2])',
        AUTOCOMPLETE_NOTE,
    ),
    (
        "stop_requested",
        "stop_requested()",
        "True once Stop has been pressed - check it inside a long loop of your own "
        "so it can exit cleanly instead of running to completion regardless.",
        "while not stop_requested():\n    ...",
        None,
    ),
]

SAMPLE_CODE = '''# Example - branching is just normal Python; delete this and write your own.
count = variables.get("count", 0)
text = ocr("region_name")            # live OCR read, or None if nothing was read

if text and "Ready" in text:
    play("my_recording", repeat=2)
    keystroke("tuv", 0.5, 1)          # tap t, u, v - random 0.5-1s gap after each
    variables["count"] = count + 1
    log("ran, count is now", variables["count"])
else:
    sleep(1.0)
    log("not ready yet")
'''

# Where each name's suggestions come from, and the pattern that detects "the cursor
# is sitting inside that call/subscript's first string argument, right after
# whatever's already been typed" - group 1 captures what's typed so far.
COMPLETION_PATTERNS: List[Tuple[str, "re.Pattern"]] = [
    ("ocr", re.compile(r'\bocr\(\s*[\'"](\w*)$')),
    ("play", re.compile(r'\bplay\(\s*[\'"](\w*)$')),
    ("variables", re.compile(r'\bvariables\[\s*[\'"](\w*)$')),
    ("variables", re.compile(r'\bvariables\.get\(\s*[\'"](\w*)$')),
]

# Dot-completion after a receiver whose members we can recognize lexically -
# `variables` is always the flow's dict; any bare identifier named "skills" is
# assumed to hold whatever skills() returned (its .sN slots plus .press_ready),
# since that's the only thing skills() is meant to be assigned to. Pattern's
# group 1 is what's been typed after the dot so far; the suggestion_key is looked
# up in `self._suggestions` (see suggestions_from_graph) since both lists depend
# on what's actually in this flow's graph.
MEMBER_COMPLETIONS: List[Tuple["re.Pattern", str]] = [
    (re.compile(r'\bvariables\.(\w*)$'), "variables_members"),
    (re.compile(r'\bskills\.(\w*)$'), "skills_members"),
]

# A bare identifier being typed at an expression-start position (after whitespace,
# an opening bracket, an operator, etc. - never mid-string) - suggests it might be
# the start of one of this namespace's own function names.
NAME_PREFIX_RE = re.compile(r'(?:^|[\s([{,=+\-*/%<>!&|:~])(\w+)$')


def _inside_string(text_before_cursor: str) -> bool:
    """Naive same-line check: True if, scanning left to right, the text ends inside
    an unterminated ' or " string. Not a real tokenizer - just enough to stop the
    bare function-name completion from popping up while typing plain text inside a
    string literal (e.g. a Log-style message that happens to contain "sleep")."""
    in_single = in_double = False
    i = 0
    while i < len(text_before_cursor):
        c = text_before_cursor[i]
        if c == "\\" and i + 1 < len(text_before_cursor):
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        i += 1
    return in_single or in_double


def suggestions_from_graph(graph: Graph) -> Dict[str, List[str]]:
    """What a Code block's autocomplete should offer for ocr()/play()/variables[]/
    variables./skills., sourced from whatever OCR / Recorded Block / Set Variable /
    Skills nodes already exist in this flow - so suggestions always match names
    that actually do something here, not every region/recording/variable that
    exists anywhere, and skills.sN only goes up to however many rows the flow's
    one Skills block actually has."""
    ocr_names = {n.props.get("region") for n in graph.nodes.values() if n.type == "ocr"}
    play_names = {n.props.get("recording") for n in graph.nodes.values() if n.type == "recorded_block"}
    var_names = {n.props.get("name") for n in graph.nodes.values() if n.type == "set_variable"}
    skills_node = next((n for n in graph.nodes.values() if n.type == "skills"), None)
    slot_count = len(skills_node.props.get("skills") or []) if skills_node is not None else 0
    return {
        "ocr": sorted(n for n in ocr_names if n),
        "play": sorted(n for n in play_names if n),
        "variables": sorted(n for n in var_names if n),
        "variables_members": ["get", "keys", "values", "items", "pop", "setdefault"],
        "skills_members": [f"s{i}" for i in range(1, slot_count + 1)] + ["press_ready"],
    }


class PythonHighlighter(QSyntaxHighlighter):
    """Minimal Python syntax highlighting: keywords, the block's own helper names,
    strings, comments, and numbers. Not a full tokenizer - just enough to make a
    multi-line code block readable at a glance."""

    def __init__(self, document):
        super().__init__(document)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        keyword_fmt = QTextCharFormat()
        keyword_fmt.setForeground(QColor("#c586c0"))
        keyword_fmt.setFontWeight(QFont.Bold)
        for word in keyword.kwlist:
            self._rules.append((QRegularExpression(rf"\b{word}\b"), keyword_fmt))

        helper_fmt = QTextCharFormat()
        helper_fmt.setForeground(QColor("#4ec9b0"))
        for word in HELPERS:
            self._rules.append((QRegularExpression(rf"\b{word}\b"), helper_fmt))

        number_fmt = QTextCharFormat()
        number_fmt.setForeground(QColor("#b5cea8"))
        self._rules.append((QRegularExpression(r"\b[0-9]+\.?[0-9]*\b"), number_fmt))

        string_fmt = QTextCharFormat()
        string_fmt.setForeground(QColor("#ce9178"))
        self._rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), string_fmt))
        self._rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string_fmt))

        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setForeground(QColor("#6a9955"))
        self._comment_re = QRegularExpression(r"#[^\n]*")

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        m = self._comment_re.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(), text.__len__() - m.capturedStart(), self._comment_fmt)


class CodeEditor(QPlainTextEdit):
    """A QPlainTextEdit that pops up a completion list while typing:
    - inside ocr("...), play("...), variables["...], or variables.get("...) -
      candidates are flow-specific names (see suggestions_from_graph)
    - after variables. or skills. - candidates are that receiver's members
      (variables' dict methods, or skills()'s .s1../.press_ready - see
      MEMBER_COMPLETIONS)
    - at the start of a bare identifier - candidates are this namespace's own
      function names (HELPERS), so typing part of e.g. "skills" or "sleep" offers
      the rest
    Same technique as Qt's own Custom Completer example, adapted to trigger on
    these specific contexts instead of generic word-prefix matching."""

    def __init__(self, parent, suggestions: Dict[str, List[str]]):
        super().__init__(parent)
        self._suggestions = suggestions
        self._completer = QCompleter(self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)

    def _context(self) -> Optional[Tuple[List[str], str]]:
        cursor = self.textCursor()
        text_before = cursor.block().text()[: cursor.positionInBlock()]

        for key, pattern in COMPLETION_PATTERNS:
            m = pattern.search(text_before)
            if m:
                candidates = self._suggestions.get(key) or []
                if candidates:
                    return candidates, m.group(1)

        for pattern, suggestion_key in MEMBER_COMPLETIONS:
            m = pattern.search(text_before)
            if m:
                candidates = self._suggestions.get(suggestion_key) or []
                if candidates:
                    return candidates, m.group(1)

        if not _inside_string(text_before):
            m = NAME_PREFIX_RE.search(text_before)
            if m:
                prefix = m.group(1)
                candidates = [h for h in HELPERS if h.startswith(prefix) and h != prefix]
                if candidates:
                    return candidates, prefix

        return None

    def _insert_completion(self, completion: str) -> None:
        ctx = self._context()
        prefix = ctx[1] if ctx else ""
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.KeepAnchor, len(prefix))
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:
        popup = self._completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key_Enter,
            Qt.Key_Return,
            Qt.Key_Escape,
            Qt.Key_Tab,
            Qt.Key_Backtab,
            Qt.Key_Up,
            Qt.Key_Down,
        ):
            event.ignore()
            return
        super().keyPressEvent(event)

        ctx = self._context()
        if ctx is None:
            popup.hide()
            return
        candidates, prefix = ctx
        self._completer.setModel(QStringListModel(candidates, self._completer))
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            popup.hide()
            return
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width())
        self._completer.complete(rect)


def _reference_tab(signature: str, description: str, example: str, note: Optional[str]) -> QWidget:
    """One page of the Reference tab widget: a helper's signature, a short
    description, and a runnable-looking usage example - kept on its own tab instead
    of piled into one long scrolling block. Wrapped in its own QScrollArea so a
    longer entry (a multi-line example, a longer description) scrolls within the
    tab instead of being clipped or overlapping other text under the tab widget's
    fixed height."""
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(10, 8, 10, 8)
    page_layout.setSpacing(6)

    sig_label = QLabel(
        f'<span style="font-family:Menlo,monospace; font-weight:bold; '
        f'color:#4ec9b0; font-size:13px;">{html.escape(signature)}</span>'
    )
    sig_label.setTextFormat(Qt.RichText)
    page_layout.addWidget(sig_label)

    desc_label = QLabel(description)
    desc_label.setWordWrap(True)
    desc_label.setStyleSheet("color:#9aa4b2;")
    page_layout.addWidget(desc_label)

    example_label = QLabel(
        f'<pre style="font-family:Menlo,monospace; background:#1e1e1e; color:#d4d4d4; '
        f'padding:6px 8px; border-radius:4px; margin:0;">{html.escape(example)}</pre>'
    )
    example_label.setTextFormat(Qt.RichText)
    example_label.setWordWrap(True)
    page_layout.addWidget(example_label)

    if note:
        note_label = QLabel(f'<span style="color:#6a9955;">{html.escape(note)}</span>')
        note_label.setTextFormat(Qt.RichText)
        note_label.setWordWrap(True)
        page_layout.addWidget(note_label)

    page_layout.addStretch(1)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(page)
    return scroll


class CodeDialog(QDialog):
    """Configure a Code node: a plain Python script with a small fixed namespace -
    `variables` (the flow's shared variable dict) plus the helpers `ocr()`, `play()`,
    `log()`, `sleep()`, `telegram()`, `keystroke()`, `skills()`, and
    `stop_requested()`. Branching/looping is just normal Python - the block always
    continues to its single "out" once the code finishes; an error is logged (with
    traceback) rather than halting the flow. The collapsed-by-default Reference
    panel below the editor gives each helper its own tab instead of one long
    scrolling block."""

    def __init__(self, parent, graph: Graph, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Code")
        self.resize(560, 480)
        self._node = node

        layout = QVBoxLayout(self)

        toggle_btn = QToolButton()
        toggle_btn.setText("Reference")
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(False)
        toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle_btn.setArrowType(Qt.RightArrow)
        toggle_btn.setStyleSheet("QToolButton { border: none; }")
        layout.addWidget(toggle_btn)

        ref_tabs = QTabWidget()
        ref_tabs.setFixedHeight(220)
        for key, signature, description, example, note in HELPER_DOCS:
            ref_tabs.addTab(_reference_tab(signature, description, example, note), key)
        ref_tabs.setVisible(False)
        layout.addWidget(ref_tabs)

        def on_toggled(checked: bool) -> None:
            ref_tabs.setVisible(checked)
            toggle_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

        toggle_btn.toggled.connect(on_toggled)

        self.code_edit = CodeEditor(self, suggestions_from_graph(graph))
        self.code_edit.setPlainText(str(node.props.get("code", "")))
        self.code_edit.setFont(QFont("Menlo", 12))
        self.code_edit.setTabStopDistance(4 * self.code_edit.fontMetrics().horizontalAdvance(" "))
        self.code_edit.setPlaceholderText(SAMPLE_CODE)
        self._highlighter = PythonHighlighter(self.code_edit.document())
        layout.addWidget(self.code_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def closeEvent(self, event) -> None:
        # Closing the window (the titlebar's close button, or Escape) goes through
        # here, not through accept()/reject() - by default that would discard
        # whatever's been typed, same as Cancel. Treat it as OK instead; only the
        # explicit Cancel button should actually discard.
        self.accept()
        super().closeEvent(event)

    def apply_to_node(self) -> None:
        self._node.props["code"] = self.code_edit.toPlainText()
