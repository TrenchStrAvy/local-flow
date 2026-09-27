"""Floating recording pill — Wispr-Flow-style visual feedback for flow.py.

A borderless always-on-top card at the bottom-center of the screen showing
an organic waveform: a bundle of thin dark strands, pinched at both ends,
that ripple with the live mic level while recording and settle into a calm
idle ripple while transcribing. Above the wave, a transcript card shows
the words as they are recognized and, after release, the cleanup pass:
removed words struck through, edited words highlighted. Pure Cocoa via
pyobjc (already a pynput dep).

All public Overlay methods are safe to call from any thread; they hop onto
the Cocoa main thread via AppHelper.callAfter.
"""

import math
import random
import time
from collections import deque

import objc
from AppKit import (
    NSApplication,
    NSAttributedString,
    NSMutableAttributedString,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSStrikethroughStyleAttributeName,
    NSStringDrawingUsesLineFragmentOrigin,
    NSUnderlineStyleSingle,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSScreen,
    NSShadow,
    NSStatusWindowLevel,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)
from Foundation import (NSMakePoint, NSMakeRect, NSMakeSize, NSObject,
                        NSString, NSTimer)
from PyObjCTools import AppHelper


PILL_W, PILL_H = 224, 56      # waveform area
CARD_W = 520                  # transcript card (window width)
CARD_PAD = 12
CARD_LINES = 3                # visible lines; older text scrolls away
CARD_FONT = 13.0
CARD_GAP = 6                  # between card and wave
CORNER_RADIUS = 14
WIN_W = CARD_W
WIN_H = PILL_H + CARD_GAP + 2 * CARD_PAD + int(CARD_LINES * CARD_FONT * 1.5)
TICK_SEC = 1 / 60             # animation frame rate
BASE_DT = 1 / 30              # dt at which phase increments are calibrated
STRANDS = 18                  # thin curves in the bundle
SAMPLES = 64                  # x-resolution of each strand
HIST = 56                     # mic-level history mapped across the width
LEVEL_GAIN = 10.0             # scales mic RMS (~0.02-0.3 for speech)
STRAND_ALPHA = 0.42


class PillView(NSView):

    def initWithFrame_(self, frame):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.state = "recording"
        self.note = ""           # e.g. language name after a Q/W/E switch
        self.words = []          # [(word, style)] for the transcript card
        self._card_text = None   # cached, trimmed NSAttributedString
        self.history = deque([0.0] * HIST, maxlen=HIST)
        self.smoothed = 0.0
        self.phase1 = 0.0
        self.phase2 = 0.0
        self._last_tick = 0.0
        self.setWantsLayer_(True)   # composite via a backing layer
        rng = random.Random(42)
        # per-strand personality: (spread offset, phase offset)
        self.strands = [(rng.uniform(-1.0, 1.0), rng.uniform(0.0, 6.28))
                        for _ in range(STRANDS)]
        return self

    def set_state(self, state):
        if state == "recording" and self.state != "recording":
            self.history = deque([0.0] * HIST, maxlen=HIST)
            self.smoothed = 0.0
        self.state = state
        self.setNeedsDisplay_(True)

    def set_words(self, words):
        self.words = list(words)
        self._card_text = None
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _card_attrs(self, style, ink):
        para = NSMutableParagraphStyle.alloc().init()
        para.setLineBreakMode_(0)           # word wrap
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(CARD_FONT),
            NSParagraphStyleAttributeName: para,
        }
        if style == "removed":
            attrs[NSForegroundColorAttributeName] = \
                NSColor.colorWithCalibratedWhite_alpha_(ink, 0.35)
            attrs[NSStrikethroughStyleAttributeName] = NSUnderlineStyleSingle
        elif style == "changed":
            attrs[NSForegroundColorAttributeName] = \
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    1.0, 0.85, 0.45, 1.0)        # warm highlight
        elif style == "dim":
            attrs[NSForegroundColorAttributeName] = \
                NSColor.colorWithCalibratedWhite_alpha_(ink, 0.45)
        else:
            attrs[NSForegroundColorAttributeName] = \
                NSColor.colorWithCalibratedWhite_alpha_(ink, 0.95)
        return attrs

    @objc.python_method
    def _build_card_text(self, width, max_h, ink):
        """Attributed transcript, dropping leading words until the last
        CARD_LINES lines fit. Cached until the words change."""
        if self._card_text is not None:
            return self._card_text
        words = self.words
        size = NSMakeSize(width, 10_000)
        opts = NSStringDrawingUsesLineFragmentOrigin

        def build(start):
            text = NSMutableAttributedString.alloc().init()
            for k, (word, style) in enumerate(words[start:]):
                piece = (" " if k else "") + word
                text.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_(
                        piece, self._card_attrs(style, ink)))
            return text

        def fits(start):
            return build(start).boundingRectWithSize_options_(
                size, opts).size.height <= max_h

        # smallest start (most words) that still fits: binary search
        lo, hi = 0, max(0, len(words) - 1)
        if not fits(0):
            while lo < hi:
                mid = (lo + hi) // 2
                if fits(mid):
                    hi = mid
                else:
                    lo = mid + 1
        text = build(lo)
        self._card_text = text
        return text

    def push_level(self, level):
        """Called every tick: advance the animation by real elapsed time,
        so the wave moves at the same speed even if frames run late."""
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick) if self._last_tick else BASE_DT
        self._last_tick = now
        k = dt / BASE_DT

        if self.state in ("transcribing", "cleaning"):
            # calm breathing ripple while we wait for the transcript
            target = 0.03 + 0.015 * math.sin(self.phase1 * 0.35)
        else:
            target = level
        # instant attack, quick decay — syllables punch, gaps collapse
        decay = 0.70 ** k
        self.smoothed = max(target,
                            self.smoothed * decay + target * (1 - decay))
        self.history.append(self.smoothed)
        # the wave travels faster the louder you speak
        speed = 1.0 + 2.5 * math.tanh(self.smoothed * LEVEL_GAIN)
        self.phase1 += 0.30 * speed * k
        self.phase2 += 0.19 * speed * k
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        W, H = b.size.width, PILL_H
        if self.words:
            self._draw_card(b.size.width, b.size.height)

        # no card: strands and caption float on a transparent window, each
        # with a soft shadow so they read on any background
        ink = 0.97   # white ink + dark shadow reads on any background
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.7))
        shadow.setShadowBlurRadius_(6)
        shadow.setShadowOffset_(NSMakeSize(0, -1.5))
        shadow.set()

        # the wave keeps its original PILL_W footprint, centred
        x_off = (W - PILL_W) / 2
        W = PILL_W
        margin, mid = 20.0 + x_off, H / 2
        span = W - 2 * margin
        max_amp = H / 2 - 10
        hist = list(self.history)
        m = len(hist)

        # base curve: level history (old→left, new→right) shapes the
        # amplitude; two drifting sines give it organic motion; a sin^0.85
        # envelope pinches both ends to a point
        loud = math.tanh(hist[-1] * LEVEL_GAIN)   # current loudness 0..1
        base = []
        for j in range(SAMPLES + 1):
            t = j / SAMPLES
            hpos = t * (m - 1)
            i0 = int(hpos)
            f = hpos - i0
            lv = hist[i0] * (1 - f) + hist[min(i0 + 1, m - 1)] * f
            env = math.sin(math.pi * t) ** 0.85
            amp = env * math.tanh(lv * LEVEL_GAIN) * max_amp
            # third, higher-frequency harmonic only kicks in when loud
            y = amp * (0.52 * math.sin(t * 9.5 + self.phase1)
                       + 0.30 * math.sin(t * 17.0 - self.phase2)
                       + 0.28 * loud * math.sin(t * 27.0 + self.phase1 * 1.6))
            base.append((margin + t * span, y, amp, env))

        # the bundle: each strand follows the base curve with its own
        # slight scale and a fan-out that grows where the wave is loud,
        # so strands converge at the pinched ends and splay at the peaks.
        # All strands go into ONE path stroked once — per-segment lineTo
        # calls cross the ObjC bridge and are far too slow at 60fps.
        NSColor.colorWithCalibratedWhite_alpha_(ink, STRAND_ALPHA).setStroke()
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(0.8)
        sin = math.sin
        fan_phase = self.phase2 * 0.6
        for u, w in self.strands:
            scale = 1.0 + 0.20 * u
            pts = [
                (x, mid + y * scale
                    + u * env * (0.8 + 0.55 * amp)
                    * sin((j / SAMPLES) * 5.5 + w + fan_phase))
                for j, (x, y, amp, env) in enumerate(base)
            ]
            path.moveToPoint_(pts[0])
            path.appendBezierPathWithPoints_count_(pts[1:], len(pts) - 1)
        path.stroke()

        label = {"transcribing": "transcribing…",
                 "cleaning": "cleaning up…"}.get(self.state, self.note)
        if label:
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(10),
                NSForegroundColorAttributeName:
                    NSColor.colorWithCalibratedWhite_alpha_(ink, 0.7),
            }
            text = NSString.stringWithString_(label)
            size = text.sizeWithAttributes_(attrs)
            text.drawAtPoint_withAttributes_(
                NSMakePoint(x_off + (W - size.width) / 2, 5), attrs)
        ctx.restoreGraphicsState()

    @objc.python_method
    def _draw_card(self, W, H):
        """Translucent dark card with the transcript, bottom-aligned so the
        newest words sit just above the wave."""
        ink = 0.97
        inner_w = CARD_W - 2 * CARD_PAD
        max_h = H - PILL_H - CARD_GAP - 2 * CARD_PAD
        text = self._build_card_text(inner_w, max_h, ink)
        th = min(max_h, text.boundingRectWithSize_options_(
            NSMakeSize(inner_w, 10_000),
            NSStringDrawingUsesLineFragmentOrigin).size.height)
        card_h = th + 2 * CARD_PAD
        y0 = PILL_H + CARD_GAP
        card = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect((W - CARD_W) / 2, y0, CARD_W, card_h),
            CORNER_RADIUS, CORNER_RADIUS)
        NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.78).setFill()
        card.fill()
        text.drawWithRect_options_(
            NSMakeRect((W - CARD_W) / 2 + CARD_PAD, y0 + CARD_PAD,
                       inner_w, th),
            NSStringDrawingUsesLineFragmentOrigin)


class Overlay(NSObject):
    """Owns the pill window. Create on the main thread via create_overlay()."""

    def initWithLevelSource_(self, level_source):
        self = objc.super(Overlay, self).init()
        if self is None:
            return None
        self.level_source = level_source
        self.timer = None

        rect = NSMakeRect(0, 0, WIN_W, WIN_H)
        self.view = PillView.alloc().initWithFrame_(rect)
        self.window = NSWindow.alloc(
        ).initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(False)   # elements draw their own
        self.window.setLevel_(NSStatusWindowLevel)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary)
        self.window.setContentView_(self.view)

        screen = NSScreen.mainScreen()
        if screen is not None:
            f = screen.frame()
            self.window.setFrameOrigin_(NSMakePoint(
                f.origin.x + (f.size.width - WIN_W) / 2,
                f.origin.y + 140))
        return self

    # -- public API, callable from any thread

    def show_recording(self):
        AppHelper.callAfter(self._show, "recording")

    def show_transcribing(self):
        AppHelper.callAfter(self._show, "transcribing")

    def show_cleaning(self):
        AppHelper.callAfter(self._show, "cleaning")

    def set_words(self, words):
        """Transcript card content: [(word, style)] with style in
        same | changed | removed | dim. Empty list hides the card."""
        AppHelper.callAfter(self.view.set_words, words)

    def set_text(self, text, style="same"):
        self.set_words([(w, style) for w in text.split()])

    def hide(self):
        AppHelper.callAfter(self._hide)

    def set_note(self, text):
        """Caption under the wave while recording (e.g. 'Deutsch')."""
        AppHelper.callAfter(self._set_note, text)

    # -- main-thread implementations

    @objc.python_method
    def _show(self, state):
        if state == "recording":
            self.view.set_words([])
        self.view.set_state(state)
        if self.timer is None:
            self.timer = (
                NSTimer.
                scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    TICK_SEC, self, b"tick:", None, True))
        self.window.orderFrontRegardless()

    @objc.python_method
    def _set_note(self, text):
        self.view.note = text
        self.view.setNeedsDisplay_(True)

    def _hide(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.window.orderOut_(None)

    def tick_(self, _timer):
        self.view.push_level(float(self.level_source()))


def create_overlay(level_source):
    """Set up the (dock-less) Cocoa app and return an Overlay.

    Must be called on the main thread; the caller must then run
    AppHelper.runEventLoop() on that thread for the pill to appear.
    """
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return Overlay.alloc().initWithLevelSource_(level_source)
