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
    CGEventSetIntegerValueField,
    kCGEventSourceUserData,
    kCGHIDEventTap,
)

BACKSPACE_VK = 51
CHUNK = 16                    # unicode chars per synthetic key event
TAG = 0x4C0CA1F1              # marks our events so flow.py's listener
                              # never mistakes them for Option+A / Q / W / E


import re

_WORD = re.compile(r"\S+")


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word).lower()


def plan(typed: str, new: str, floor: int = 0, soft: bool = False):
    """(backspaces, suffix) that turn `typed` into `new`.

    Exact mode compares characters. Soft mode (live preview) compares
    words ignoring case and punctuation, and keeps the typed spelling of
    the matching prefix: the preview model's comma or capital may differ
    from the final model's, and retyping a whole sentence for a comma is
    flicker for nothing. The final pass runs exact and settles it.

    Characters before `floor` are committed and never deleted, so a later
    correction can only touch the current utterance. Pure."""
    if soft:
        tw = list(_WORD.finditer(typed))
        nw = list(_WORD.finditer(new))
        k = 0
        for a, b in zip(tw, nw):
            if _norm(a.group()) != _norm(b.group()):
                break
            k += 1
        # the last matched word is re-synced when text continues past it:
        # a preview ends "test." but the sentence goes on "test how it"
        if k and k < len(nw) and tw[k - 1].group() != nw[k - 1].group():
            k -= 1
        keep_t = tw[k - 1].end() if k else 0
        keep_n = nw[k - 1].end() if k else 0
        n_t, n_n = keep_t, keep_n
    else:
        n = 0
        for a, b in zip(typed, new):
            if a != b:
                break
            n += 1
        n_t = n_n = n
    f = min(floor, len(typed))
    if n_t < f:
        # committed text stays; resume after the same *word* in `new`, since
        # its spelling of those words ("coast." vs "coast") may differ
        j = len(_WORD.findall(typed[:f]))
        nw = list(_WORD.finditer(new))
        n_t = f
        n_n = nw[j - 1].end() if 0 < j <= len(nw) else (0 if j == 0 else len(new))
    return len(typed) - n_t, new[n_n:]


def _post(vk, text=None):
    for is_press in (True, False):
        ev = CGEventCreateKeyboardEvent(None, vk, is_press)
        CGEventSetFlags(ev, 0)
        CGEventSetIntegerValueField(ev, kCGEventSourceUserData, TAG)
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
        self.floor = 0

    def reset(self):
        self.typed = ""
        self.floor = 0

    def commit(self):
        """Everything typed so far is final: later updates only append or
        correct text beyond this point."""
        self.floor = len(self.typed)

    def update(self, new: str, soft: bool = False):
        """Make the field read `new` (or, in soft mode, the same words);
        returns (backspaces, chars typed)."""
        n_del, suffix = plan(self.typed, new, self.floor, soft)
        if n_del:
            backspace(n_del)
        if suffix:
            type_text(suffix)
        self.typed = self.typed[: len(self.typed) - n_del] + suffix
        return n_del, len(suffix)
