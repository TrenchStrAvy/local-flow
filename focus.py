"""Remember which text field had focus when dictation started, and put focus
back there before pasting.

Without this, clicking somewhere else while speaking (or while the
transcription is still running) sends the paste to wherever the cursor
ended up. We snapshot the focused Accessibility element and its app on
hotkey-press and, right before the Cmd+V, re-activate that app and re-focus
that element if anything changed in the meantime.

Every step is best-effort: apps that don't expose Accessibility focus
(some Electron/Java windows, secure fields) simply get the old behaviour.
"""

import time

from AppKit import NSRunningApplication, NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateSystemWide,
    AXUIElementGetPid,
    AXUIElementSetAttributeValue,
    kAXFocusedAttribute,
    kAXFocusedUIElementAttribute,
    kAXRoleAttribute,
)

NSApplicationActivateIgnoringOtherApps = 1 << 1

_system = AXUIElementCreateSystemWide()


def _focused_element():
    err, element = AXUIElementCopyAttributeValue(
        _system, kAXFocusedUIElementAttribute, None)
    return element if err == 0 else None


def _role(element) -> str:
    err, role = AXUIElementCopyAttributeValue(element, kAXRoleAttribute, None)
    return str(role) if err == 0 and role else ""


class FocusTarget:
    """Snapshot of where the text should land."""

    def __init__(self, element, pid, app_name, role):
        self.element = element
        self.pid = pid
        self.app_name = app_name
        self.role = role

    def __repr__(self):
        return f"<FocusTarget {self.app_name} pid={self.pid} {self.role}>"


def capture():
    """Snapshot the focused element + frontmost app. Returns None if the
    Accessibility API can't tell us (no permission, no text focus)."""
    element = _focused_element()
    if element is None:
        return None
    err, pid = AXUIElementGetPid(element, None)
    if err != 0:
        return None
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    name = app.localizedName() if app is not None else "?"
    return FocusTarget(element, pid, name, _role(element))


def restore(target, settle=0.15) -> bool:
    """Bring `target` back into focus if it isn't already.

    Returns True when the original element is focused again (or never lost
    focus), False when we couldn't get there — the caller pastes anyway.
    """
    if target is None:
        return False
    current = _focused_element()
    if current is not None and current == target.element:
        return True

    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None or front.processIdentifier() != target.pid:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
            target.pid)
        if app is None or app.isTerminated():
            return False
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is not None and front.processIdentifier() == target.pid:
                break
            time.sleep(0.02)

    # focus the exact element (the app may have moved focus to another
    # field or window while it was in the background)
    AXUIElementSetAttributeValue(target.element, kAXFocusedAttribute, True)
    time.sleep(settle)
    current = _focused_element()
    return current is not None and current == target.element
