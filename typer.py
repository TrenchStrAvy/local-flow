"""Inline typing for local-flow: put words into the focused field as they
are recognized, and correct them in place.

The overlay is not involved. LiveTyper remembers what it has typed so far;
each update() computes the difference to the new text, deletes the tail
that changed with Backspace events and types the new tail. That is how a
preview that said "how it work" becomes "how it works and" a second later,
and how the cleanup pass visibly fixes the text after release.

Keystrokes are raw Quartz keyboard events carrying a Unicode string and no
modifier flags, so they type correctly even while the dictation hotkey
(Right-Option) is physically held, and they never look like the Q/W/E
language quick keys to flow.py's own listener (key code 0 / 51 only).
"""

from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    CGEventSetFlags,
    kCGHIDEventTap,
)

BACKSPACE_VK = 51
CHUNK = 16                    # unicode chars per synthetic key event


def plan(typed: str, new: str):
    """(backspaces, suffix) that turn `typed` into `new`, at a character
    level. Pure, for testing."""
    n = 0
    for a, b in zip(typed, new):
        if a != b:
            break
        n += 1
    return len(typed) - n, new[n:]


def _post(vk, text=None):
    for is_press in (True, False):
        ev = CGEventCreateKeyboardEvent(None, vk, is_press)
        CGEventSetFlags(ev, 0)
        if text:
            CGEventKeyboardSetUnicodeString(ev, len(text), text)
        CGEventPost(kCGHIDEventTap, ev)


def type_text(text: str):
    for i in range(0, len(text), CHUNK):
        _post(0, text[i:i + CHUNK])


def backspace(n: int):
    for _ in range(n):
        _post(BACKSPACE_VK)


class LiveTyper:
    def __init__(self):
        self.typed = ""

    def reset(self):
        self.typed = ""

    def update(self, new: str):
        """Make the field read `new`; returns (backspaces, chars typed)."""
        n_del, suffix = plan(self.typed, new)
        if n_del:
            backspace(n_del)
        if suffix:
            type_text(suffix)
        self.typed = new
        return n_del, len(suffix)
