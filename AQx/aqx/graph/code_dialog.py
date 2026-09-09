from __future__ import annotations

import builtins as _builtins_module
import html
import io
import keyword
import re
import tokenize
from typing import Dict, List, Optional, Tuple

import black
from PySide6.QtCore import QRect, QSize, QStringListModel, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextFormat
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .model import Graph, Node

HELPERS = ("variables", "ocr", "ocr_lines", "play", "log", "sleep", "telegram", "keystroke", "skills", "sync", "save", "stop_requested")

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
        "ocr_lines",
        "ocr_lines(region, pattern=None)",
        "Like ocr(), but for a region with more than one line of text (e.g. a "
        "list of items) - reads it as separate lines (blank ones dropped) and "
        "returns a list, one entry per line, instead of one combined string. With "
        "a pattern, that same regex is applied to each line independently - a "
        "line with no match becomes None in the list - rather than matched once "
        "against the whole block. Assign the result straight to a variable to "
        "keep it as a list.",
        'variables["items"] = ocr_lines("list_region")\n'
        'variables["scores"] = ocr_lines("scores_region", r"(\\d+)")\n\n'
        'items = ocr_lines("list_region")\n'
        'if items:\n'
        '    log("first item:", items[0], "-", len(items), "lines total")',
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
        "sync",
        "sync(name)  (alias: save(name))",
        "Checkpoints a plain variable's current value onto this block - call it "
        "right after you change the variable, e.g. sync(x) after x += 1. Once the "
        "flow is saved, that checkpointed value automatically overwrites whatever "
        "setup() assigns to that same name at the start of every later Run - even "
        "after Stop, even after reopening AQx - so it picks up wherever it was "
        "left instead of resetting to setup()'s hardcoded default. Must be called "
        "with a single bare variable name, e.g. sync(x) - not sync(x + 1) or "
        "sync(\"x\"). Call order doesn't matter - sync(x) always saves whatever x "
        "is *right now*, so calling it right after modifying x is exactly right.",
        'x = 0                     # in setup() - the default ONLY the very first\n'
        '                          # run ever uses; every later run overwrites\n'
        '                          # this with whatever sync() last saved\n\n'
        '# in loop():\n'
        'x += 1\n'
        'sync(x)                   # checkpoint the new value, every lap\n'
        'log("x is", x)',
        None,
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

SETUP_PLACEHOLDER = '''# Runs once - the very first time this block is reached, no matter how many
# laps the flow runs (the toolbar's Loops field) or how many times functions()/
# loop() below end up running. Good for one-time initialization: counters,
# constants, anything that shouldn't reset every lap.
attempts = 0
'''

FUNCTIONS_PLACEHOLDER = '''# Runs every time this block is reached - once per lap, including the first -
# always right before loop() (the Main tab). Nothing about this tab is special
# beyond running first; it's just a place to keep helper `def`s out of the way
# of your main per-lap logic below. Anything setup() declared (attempts above)
# is available here too.
def log_attempt():
    global attempts
    attempts += 1
    log("attempt", attempts)
'''

SAMPLE_CODE = '''# Runs every time this block is reached - once per lap, including the first,
# right after functions() above. Branching is just normal Python; delete this
# and write your own. Anything setup() or functions() declared (attempts,
# log_attempt() above) is available here too.
log_attempt()
text = ocr("region_name")            # live OCR read, or None if nothing was read

if text and "Ready" in text:
    play("my_recording", repeat=2)
    keystroke("tuv", 0.5, 1)          # tap t, u, v - random 0.5-1s gap after each
    variables["count"] = variables.get("count", 0) + 1
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
    ("ocr", re.compile(r'\bocr_lines\(\s*[\'"](\w*)$')),
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


_BUILTIN_NAMES = frozenset(dir(_builtins_module)) - {"True", "False", "None"}  # those three are real keywords
_FSTRING_TOKEN_TYPES = tuple(
    getattr(tokenize, name) for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END") if hasattr(tokenize, name)
)


class PythonHighlighter(QSyntaxHighlighter):
    """Real Python syntax highlighting, built on the stdlib `tokenize` module
    instead of ad hoc regexes run line-by-line. That line-by-line regex approach
    had real bugs a regex can't fix without becoming a tokenizer anyway: a '#'
    inside a string got treated as the start of a comment (mis-coloring the rest
    of the line), and a triple-quoted string spanning more than one line only had
    its opening/closing lines colored, with everything in between - including any
    keywords it happened to contain - left looking like code. tokenize sidesteps
    all of that by construction, since it already understands where a string or
    comment actually starts and ends.

    Re-tokenizes the whole document on every call rather than tracking state
    block-to-block - these are short scripts (dozens to a couple hundred lines),
    so this is cheap, and it's far simpler than reimplementing tokenize's own
    multi-line-string state tracking by hand. On a Python whose tokenizer
    supports PEP 701 (3.12+), an f-string's {expr} parts come through as their
    own real NAME/NUMBER/OP tokens - not one flat string color - so this doesn't
    even need special-casing here, they get the same per-token-type formatting as
    the rest of the code."""

    def __init__(self, document):
        super().__init__(document)

        def fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            if italic:
                f.setFontItalic(True)
            return f

        self._keyword_fmt = fmt("#c586c0", bold=True)
        self._helper_fmt = fmt("#4ec9b0")
        self._builtin_fmt = fmt("#4fc1ff")
        self._number_fmt = fmt("#b5cea8")
        self._string_fmt = fmt("#ce9178")
        self._comment_fmt = fmt("#6a9955", italic=True)

    def highlightBlock(self, text: str) -> None:
        block_no = self.currentBlock().blockNumber()
        for tok in self._tokenize(self.document().toPlainText()):
            self._apply_token(tok, block_no, text)

    @staticmethod
    def _tokenize(full_text: str) -> list:
        tokens = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(full_text).readline):
                tokens.append(tok)
        except Exception:
            # Incomplete/invalid code while typing (an unterminated string, a
            # dangling open bracket, bad indentation, ...) - keep whatever
            # tokens tokenize managed to produce before it gave up, rather than
            # losing highlighting for the whole document over one bad line.
            pass
        return tokens

    def _apply_token(self, tok, block_no: int, text: str) -> None:
        start_line, start_col = tok.start
        end_line, end_col = tok.end
        start_line -= 1  # tokenize is 1-indexed; QTextBlock is 0-indexed
        end_line -= 1
        if block_no < start_line or block_no > end_line:
            return
        char_fmt = self._format_for(tok)
        if char_fmt is None:
            return
        col_start = start_col if block_no == start_line else 0
        col_end = end_col if block_no == end_line else len(text)
        if col_end > col_start:
            self.setFormat(col_start, col_end - col_start, char_fmt)

    def _format_for(self, tok) -> Optional[QTextCharFormat]:
        tok_type = tok.type
        if tok_type == tokenize.COMMENT:
            return self._comment_fmt
        if tok_type == tokenize.STRING or tok_type in _FSTRING_TOKEN_TYPES:
            return self._string_fmt
        if tok_type == tokenize.NUMBER:
            return self._number_fmt
        if tok_type == tokenize.NAME:
            value = tok.string
            if keyword.iskeyword(value) or keyword.issoftkeyword(value):
                return self._keyword_fmt
            if value in HELPERS:
                return self._helper_fmt
            if value in _BUILTIN_NAMES:
                return self._builtin_fmt
        return None


class _LineNumberArea(QWidget):
    """The gutter strip to an editor's left, painted by asking the editor itself
    to draw into it - same split Qt's own Code Editor example uses, since the
    editor already has all the block-geometry info this needs."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.paint_line_number_area(event)


# Auto-close pairs for typed opening characters, and the reverse lookup (closer
# -> opener) used to recognize a freshly-opened, still-empty pair on Backspace.
_AUTO_CLOSE_PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
_CLOSERS = set(_AUTO_CLOSE_PAIRS.values())


class CodeEditor(QPlainTextEdit):
    """A QPlainTextEdit made to feel like an actual code editor rather than a
    plain text box: a line-number gutter, the current line subtly highlighted,
    Enter carries over the previous line's indent (and adds one more level after
    a line ending in ':'), Tab/Shift+Tab on a multi-line selection indents/dedents
    every selected line instead of just replacing the selection, typing an
    opening bracket/quote inserts its match and drops the cursor between them
    (or wraps a selection), typing a closing character that's already right there
    just steps over it instead of duplicating it, and Backspace between a still-
    empty auto-closed pair removes both sides at once. On top of that, it pops up
    a completion list while typing:
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

        self._line_number_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_area_width(0)
        self._highlight_current_line()

    # --- line number gutter (Qt's own Code Editor example's standard split) ---
    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def paint_line_number_area(self, event) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor("#1e1e1e"))
        block = self.firstVisibleBlock()
        block_no = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(QColor("#5a5a5a"))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, top, self._line_number_area.width() - 6, self.fontMetrics().height(),
                    Qt.AlignRight, str(block_no + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_no += 1

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._line_number_area.setGeometry(QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height()))

    def _update_line_number_area_width(self, _new_block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#2a2d33"))
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

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
        if not self._handle_editor_key(event):
            super().keyPressEvent(event)
        self._update_completer_popup()

    def _update_completer_popup(self) -> None:
        popup = self._completer.popup()
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

    def _handle_editor_key(self, event) -> bool:
        """The code-editor-feel key handling described in the class docstring.
        Returns True if this fully handled the key itself (caller should skip the
        default QPlainTextEdit behavior), False to fall through to it."""
        key = event.key()
        text = event.text()
        cursor = self.textCursor()

        if key in (Qt.Key_Tab, Qt.Key_Backtab) and cursor.hasSelection() and self._is_multiline_selection(cursor):
            self._indent_selection(cursor, dedent=(key == Qt.Key_Backtab))
            return True
        if key == Qt.Key_Backtab:
            self._dedent_current_line()
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._insert_auto_indented_newline()
            return True
        if key == Qt.Key_Backspace and not cursor.hasSelection() and self._delete_auto_close_pair(cursor):
            return True
        if text and text in _CLOSERS and not cursor.hasSelection() and self._skip_over_closer(cursor, text):
            return True
        if text and text in _AUTO_CLOSE_PAIRS:
            self._insert_auto_close_pair(cursor, text)
            return True
        return False

    @staticmethod
    def _is_multiline_selection(cursor) -> bool:
        doc = cursor.document()
        return doc.findBlock(cursor.selectionStart()).blockNumber() != doc.findBlock(cursor.selectionEnd()).blockNumber()

    def _indent_selection(self, cursor, dedent: bool) -> None:
        doc = self.document()
        start_block = doc.findBlock(cursor.selectionStart()).blockNumber()
        end_block = doc.findBlock(cursor.selectionEnd()).blockNumber()
        # A selection ending exactly at the start of a line (column 0) doesn't
        # visually include that line - don't indent/dedent one line too many.
        if doc.findBlock(cursor.selectionEnd()).position() == cursor.selectionEnd() and end_block > start_block:
            end_block -= 1

        edit_cursor = self.textCursor()
        edit_cursor.beginEditBlock()
        for block_no in range(start_block, end_block + 1):
            block = doc.findBlockByNumber(block_no)
            block_cursor = self.textCursor()
            block_cursor.setPosition(block.position())
            if dedent:
                text = block.text()
                if text.startswith("\t"):
                    block_cursor.setPosition(block.position() + 1, block_cursor.MoveMode.KeepAnchor)
                    block_cursor.removeSelectedText()
                else:
                    strip = len(text) - len(text.lstrip(" "))
                    strip = min(strip, 4)
                    if strip:
                        block_cursor.setPosition(block.position() + strip, block_cursor.MoveMode.KeepAnchor)
                        block_cursor.removeSelectedText()
            else:
                block_cursor.insertText("\t")
        edit_cursor.endEditBlock()

    def _dedent_current_line(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        line_cursor = self.textCursor()
        line_cursor.setPosition(block.position())
        if text.startswith("\t"):
            line_cursor.setPosition(block.position() + 1, line_cursor.MoveMode.KeepAnchor)
            line_cursor.removeSelectedText()
        else:
            strip = min(len(text) - len(text.lstrip(" ")), 4)
            if strip:
                line_cursor.setPosition(block.position() + strip, line_cursor.MoveMode.KeepAnchor)
                line_cursor.removeSelectedText()

    def _insert_auto_indented_newline(self) -> None:
        cursor = self.textCursor()
        cursor.removeSelectedText()
        line = cursor.block().text()[: cursor.positionInBlock()]
        indent = line[: len(line) - len(line.lstrip(" \t"))]
        code_part = line.split("#", 1)[0].rstrip()
        if code_part.endswith(":"):
            indent += "\t"
        cursor.insertText("\n" + indent)
        self.setTextCursor(cursor)

    def _skip_over_closer(self, cursor, text: str) -> bool:
        doc = self.document()
        pos = cursor.position()
        if doc.characterAt(pos) != text:
            return False
        cursor.movePosition(cursor.MoveOperation.Right)
        self.setTextCursor(cursor)
        return True

    def _insert_auto_close_pair(self, cursor, text: str) -> None:
        closer = _AUTO_CLOSE_PAIRS[text]
        if cursor.hasSelection():
            selected = cursor.selectedText()
            start = cursor.selectionStart()
            cursor.insertText(text + selected + closer)
            cursor.setPosition(start + 1)
            cursor.setPosition(start + 1 + len(selected), cursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            return
        if text in ("'", '"'):
            # Only auto-pair a quote at the start of a new string - not right
            # after a letter/digit/underscore, which is almost always really an
            # apostrophe (e.g. typing "don't") rather than opening a string.
            prev_char = self.document().characterAt(cursor.position() - 1)
            if prev_char and (prev_char.isalnum() or prev_char == "_"):
                cursor.insertText(text)
                self.setTextCursor(cursor)
                return
        cursor.insertText(text + closer)
        cursor.movePosition(cursor.MoveOperation.Left)
        self.setTextCursor(cursor)

    def _delete_auto_close_pair(self, cursor) -> bool:
        pos = cursor.position()
        doc = self.document()
        prev_char = doc.characterAt(pos - 1)
        next_char = doc.characterAt(pos)
        if _AUTO_CLOSE_PAIRS.get(prev_char) != next_char:
            return False
        cursor.setPosition(pos - 1)
        cursor.setPosition(pos + 1, cursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self.setTextCursor(cursor)
        return True


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
    """Configure a Code node: three Python panes sharing one small fixed namespace -
    `variables` (the flow's shared variable dict) plus the helpers `ocr()`, `play()`,
    `log()`, `sleep()`, `telegram()`, `keystroke()`, `skills()`, `sync()`/`save()`,
    and `stop_requested()`. setup() runs once - the first time this block is
    reached in the run - for one-time initialization (counters, constants, state
    that shouldn't reset every lap). functions() and loop() both run every time
    this block is reached (once per lap, including the first), functions() always
    first - it exists purely so helper `def`s have their own place instead of being
    tangled up with loop()'s main logic. Each pane sees whatever the ones before it
    declared. Branching/looping within any of the three is just normal Python - the
    block always continues to its single "out" once loop() finishes; an error is
    logged (with traceback) rather than halting the flow. The collapsed-by-default
    Reference panel below the editors gives each helper its own tab instead of one
    long scrolling block."""

    def __init__(self, parent, graph: Graph, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Code")
        self.resize(560, 760)
        self._node = node

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()

        toggle_btn = QToolButton()
        toggle_btn.setText("Reference")
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(False)
        toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle_btn.setArrowType(Qt.RightArrow)
        toggle_btn.setStyleSheet("QToolButton { border: none; }")
        top_row.addWidget(toggle_btn)

        top_row.addStretch(1)

        format_btn = QPushButton("Format Code")
        format_btn.setToolTip("Reformats each pane with Black (Python's standard formatter). Leaves a pane untouched if it has a syntax error.")
        format_btn.clicked.connect(self._format_code)
        top_row.addWidget(format_btn)

        layout.addLayout(top_row)

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

        suggestions = suggestions_from_graph(graph)

        setup_label = QLabel("setup() - runs once, the first time this block is reached")
        setup_label.setStyleSheet("color:#9aa4b2; font-weight:bold;")
        layout.addWidget(setup_label)

        self.setup_edit = CodeEditor(self, suggestions)
        self.setup_edit.setPlainText(str(node.props.get("setup_code", "")))
        self.setup_edit.setFont(QFont("Menlo", 12))
        self.setup_edit.setTabStopDistance(4 * self.setup_edit.fontMetrics().horizontalAdvance(" "))
        self.setup_edit.setPlaceholderText(SETUP_PLACEHOLDER)
        self._setup_highlighter = PythonHighlighter(self.setup_edit.document())
        layout.addWidget(self.setup_edit, stretch=1)

        functions_label = QLabel("functions() - runs every lap, first (declare helpers here)")
        functions_label.setStyleSheet("color:#9aa4b2; font-weight:bold;")
        layout.addWidget(functions_label)

        self.functions_edit = CodeEditor(self, suggestions)
        self.functions_edit.setPlainText(str(node.props.get("functions_code", "")))
        self.functions_edit.setFont(QFont("Menlo", 12))
        self.functions_edit.setTabStopDistance(4 * self.functions_edit.fontMetrics().horizontalAdvance(" "))
        self.functions_edit.setPlaceholderText(FUNCTIONS_PLACEHOLDER)
        self._functions_highlighter = PythonHighlighter(self.functions_edit.document())
        layout.addWidget(self.functions_edit, stretch=1)

        loop_label = QLabel("loop() - runs every lap, right after functions() (main logic)")
        loop_label.setStyleSheet("color:#9aa4b2; font-weight:bold;")
        layout.addWidget(loop_label)

        self.code_edit = CodeEditor(self, suggestions)
        self.code_edit.setPlainText(str(node.props.get("code", "")))
        self.code_edit.setFont(QFont("Menlo", 12))
        self.code_edit.setTabStopDistance(4 * self.code_edit.fontMetrics().horizontalAdvance(" "))
        self.code_edit.setPlaceholderText(SAMPLE_CODE)
        self._highlighter = PythonHighlighter(self.code_edit.document())
        layout.addWidget(self.code_edit, stretch=2)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _format_code(self) -> None:
        """Reformats each of the three panes independently with Black - matches
        how they're actually run (setup_code/functions_code/code are each their
        own compile()'d unit, see runner.py's _execute_code), so formatting one
        can't be thrown off by another pane's unrelated indentation/brackets. A
        pane with a syntax error is left exactly as typed - Black can't format
        code it can't parse - and reported so it's clear why nothing changed
        there, rather than that pane silently not being touched."""
        panes = (
            ("setup()", self.setup_edit),
            ("functions()", self.functions_edit),
            ("loop()", self.code_edit),
        )
        errors = []
        for label, editor in panes:
            source = editor.toPlainText()
            if not source.strip():
                continue
            try:
                formatted = black.format_str(source, mode=black.Mode())
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                continue
            if formatted != source:
                cursor_pos = editor.textCursor().position()
                editor.setPlainText(formatted)
                new_cursor = editor.textCursor()
                new_cursor.setPosition(min(cursor_pos, len(formatted)))
                editor.setTextCursor(new_cursor)
        if errors:
            QMessageBox.warning(
                self, "Format Code",
                "Couldn't format (syntax error) - left as-is:\n\n" + "\n\n".join(errors),
            )

    def closeEvent(self, event) -> None:
        # Closing the window (the titlebar's close button, or Escape) goes through
        # here, not through accept()/reject() - by default that would discard
        # whatever's been typed, same as Cancel. Treat it as OK instead; only the
        # explicit Cancel button should actually discard.
        self.accept()
        super().closeEvent(event)

    def apply_to_node(self) -> None:
        self._node.props["setup_code"] = self.setup_edit.toPlainText()
        self._node.props["functions_code"] = self.functions_edit.toPlainText()
        self._node.props["code"] = self.code_edit.toPlainText()
