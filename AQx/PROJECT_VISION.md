# AQx — Project Vision and Development Prompt

## Project description

AQx is a macOS desktop automation application for creating, recording, editing, and running
repeatable interactions with other applications. It combines mouse and keyboard recording,
screen-region observation, OCR, image recognition, and a visual node-graph programming system.

AQx is intended for people who want to automate repetitive desktop work without writing a full
program. A user can record an interaction, represent it as a reusable block, combine it with
conditions and data-processing nodes, and run the resulting workflow over another application.
Advanced users can construct more sophisticated flows with variables, loops, calculations,
comparisons, reusable sections, and screen-driven decisions.

The application should remain unobtrusive during recording and execution. Its normal operating
view is a small floating controller placed over or beside the target application. The complete
node graph is shown only when the user chooses to edit a workflow.

## Expected user experience

A typical user should be able to:

1. Open AQx as a small floating controller.
2. Choose whether to record mouse input, keyboard input, or both.
3. Start a configurable preparation countdown so they have time to focus and position the target
   application.
4. Perform the desired actions in the target application.
5. Stop recording with a global emergency key.
6. See the recording represented as a reusable block in a visual automation graph.
7. Add decisions, loops, values, variables, OCR checks, image checks, and other actions around the
   recorded block.
8. Save the complete automation as a portable, human-readable flow.
9. Load and run a saved automation once, a specified number of times, or indefinitely.
10. Continue interacting with the target application while AQx runs as a transparent,
    click-through overlay.

## Core recording and playback features

AQx should support separate recording modes for:

- Mouse movement, clicks, button presses, releases, and scrolling.
- Keyboard presses, releases, shortcuts, and modifier combinations.
- Combined mouse and keyboard interaction with accurate relative timing.

Recordings should preserve the order and timing of input events. Users should be able to replay a
recording at its original speed and eventually adjust its timing, speed, and individual actions.
A recording should be usable as a single graph block while still allowing users to inspect or
expand it when detailed editing is needed.

Playback should support:

- Absolute screen coordinates.
- Coordinates relative to a selected application window.
- Repositioned or resized target windows when relative coordinates are used.
- Configurable delays before and between actions.
- A repeat count where `0` means repeat indefinitely.
- Immediate interruption through a global emergency-stop key.

## Visual programming system

The node graph is the main workflow-authoring environment. It should make execution order and data
flow understandable at a glance and allow connections to be created visually.

The graph should provide nodes or equivalent constructs for:

- Start, stop, pause, and delay.
- Recorded input blocks.
- Individual mouse and keyboard actions.
- Constants and user-defined values.
- Variables with local, flow, and persistent scopes.
- Assignment and variable updates.
- Arithmetic operations.
- Text operations.
- Boolean and logical operations.
- Comparisons.
- `if`, `if/else`, and multi-branch decisions.
- `for`, `while`, repeat, and retry loops.
- Loop controls such as break and continue.
- Reusable subflows or callable graph sections.
- Errors, timeouts, fallbacks, and recovery paths.
- Logging, notifications, and debugging output.

Nodes should expose typed input and output ports where useful. The editor should prevent invalid
connections, highlight validation problems, support undo and redo, and clearly identify the
currently executing node during testing.

## Screen regions and visual observation

Users should be able to draw and name rectangular regions over any display or target window.
Regions may use absolute screen coordinates or remain anchored to a selected window. A region can
then be referenced by multiple graph nodes.

Visual observation features should include:

- Capturing a screen region on demand or at an interval.
- OCR for plain text.
- OCR specialized for integers, decimals, percentages, currencies, and signed values.
- Text cleanup and confidence thresholds.
- Exact, contains, prefix, suffix, and regular-expression matching.
- Image and template matching.
- Pixel color and color-range detection.
- Detection of visible, hidden, enabled, disabled, selected, or changed states.
- Change detection between successive captures.
- Waiting until a visual condition becomes true.
- Waiting until a visual condition remains stable for a specified duration.
- Timeouts and alternate branches when a condition is not found.

Captured values should be assignable to variables and usable by comparison, arithmetic,
conditional, and logging nodes.

## Compact controller and editor

The compact controller should prioritize large, recognizable icons and minimal visual clutter. It
should provide quick access to:

- Recording-mode selection.
- Start recording.
- Run automation.
- Emergency stop.
- Open graph editor.
- Settings.
- Current status and countdown or recording indicator.

During execution, the controller should become partially transparent and click-through so it does
not block the target application. It should remain visible enough to communicate status. The user
must always retain a reliable global way to stop recording or execution.

The full editor should provide:

- A searchable node palette.
- A large pan-and-zoom graph canvas.
- Visual connections between nodes.
- Node property editing.
- Flow settings such as repeat count and execution speed.
- Recording-block inspection and editing.
- Region management and visual region selection.
- Validation results and execution logs.
- Save, load, duplicate, import, and export controls.

## Flow persistence

Automation flows should use a versioned, human-readable JSON format. A saved flow should contain
the graph, nodes, connections, variables, recording blocks, screen-region definitions, execution
settings, and references to associated image assets.

The format should support migration as AQx evolves. Loading an older flow should preserve its
meaning whenever possible, and unsupported or damaged data should produce a clear explanation
without silently changing automation behavior.

Flows may contain sensitive keyboard input, text, coordinates, screenshots, or application state.
AQx should make this clear before a flow is shared or exported.

## Safety and reliability

Desktop automation must remain under the user's control. AQx should provide:

- A configurable global emergency-stop key, with BACKSPACE as the default.
- Stop checks between actions, waits, graph nodes, and loop iterations.
- A visible preparation countdown before recording begins.
- Clear recording and running indicators.
- Prevention of accidental playback of the emergency-stop key.
- Automatic stop when the system sleeps, the display configuration changes unexpectedly, or the
  required target application becomes unavailable.
- No automatic resumption after wake unless the user explicitly requests it.
- Optional limits for maximum runtime, loop iterations, clicks, and keystrokes.
- Validation before execution.
- Permission checks and guidance for macOS Accessibility, Input Monitoring, and Screen Recording.
- Dry-run and step-through modes for testing potentially destructive automations.

## Settings and customization

Settings should include:

- Recording preparation delay, defaulting to five seconds.
- Emergency-stop key.
- Mouse-movement sampling and simplification.
- Playback speed.
- Default coordinate mode.
- OCR language and confidence threshold.
- Image-match threshold.
- Visual change sensitivity.
- Default timeout and retry behavior.
- Overlay opacity and always-on-top behavior.
- Theme and editor appearance.
- Logging detail and retention.

Settings that belong to a particular automation should be saved with that flow. User-interface and
machine-specific preferences should remain local to the AQx installation.

## Development prompt

Design and build AQx as a safe, visual macOS desktop automation application. Center the product on
a compact overlay controller and a separate node-graph editor. Let users record mouse input,
keyboard input, or both after a configurable preparation countdown. Store each recording as a
reusable timed block that can be combined with actions, variables, arithmetic, logical operations,
conditions, loops, retries, and subflows.

Allow users to define screen regions and use OCR, numeric extraction, image matching, pixel checks,
and state-change detection to drive graph decisions. Support absolute and window-relative
coordinates. Save flows in a versioned, readable JSON format with external assets where needed.

Make automation execution observable, interruptible, and conservative. BACKSPACE should be the
default global emergency stop. Stop immediately between actions and during waits, never resume
unexpectedly after system sleep, validate flows before execution, and explain missing macOS
permissions clearly. While a flow runs, keep the compact controller partially transparent and
click-through so the user can continue working with the underlying application.

Favor a clear, icon-led interface, typed graph connections, reusable components, inspectable state,
and helpful validation. Treat recordings and captured screen data as potentially sensitive. Build
the system so automation services, graph execution, persistence, and the desktop interface remain
separable and can evolve independently.
