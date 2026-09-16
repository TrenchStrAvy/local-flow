"""Floating recording pill — Wispr-Flow-style visual feedback for flow.py.

A borderless always-on-top card at the bottom-center of the screen showing
an organic waveform: a bundle of thin dark strands, pinched at both ends,
that ripple with the live mic level while recording and settle into a calm
idle ripple while transcribing. Pure Cocoa via pyobjc (already a pynput dep).

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


PILL_W, PILL_H = 224, 56
CORNER_RADIUS = 16
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

    def push_level(self, level):
        """Called every tick: advance the animation by real elapsed time,
        so the wave moves at the same speed even if frames run late."""
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick) if self._last_tick else BASE_DT
        self._last_tick = now
        k = dt / BASE_DT

        if self.state == "transcribing":
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
        W, H = b.size.width, b.size.height

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

        margin, mid = 20.0, H / 2
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

        label = "transcribing…" if self.state == "transcribing" else self.note
        if label:
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(10),
                NSForegroundColorAttributeName:
                    NSColor.colorWithCalibratedWhite_alpha_(ink, 0.7),
            }
            text = NSString.stringWithString_(label)
            size = text.sizeWithAttributes_(attrs)
            text.drawAtPoint_withAttributes_(
                NSMakePoint((W - size.width) / 2, 5), attrs)
        ctx.restoreGraphicsState()


class Overlay(NSObject):
    """Owns the pill window. Create on the main thread via create_overlay()."""

    def initWithLevelSource_(self, level_source):
        self = objc.super(Overlay, self).init()
        if self is None:
            return None
        self.level_source = level_source
        self.timer = None

        rect = NSMakeRect(0, 0, PILL_W, PILL_H)
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
                f.origin.x + (f.size.width - PILL_W) / 2,
                f.origin.y + 140))
        return self

    # -- public API, callable from any thread

    def show_recording(self):
        AppHelper.callAfter(self._show, "recording")

    def show_transcribing(self):
        AppHelper.callAfter(self._show, "transcribing")

    def hide(self):
        AppHelper.callAfter(self._hide)

    def set_note(self, text):
        """Caption under the wave while recording (e.g. 'Deutsch')."""
        AppHelper.callAfter(self._set_note, text)

    # -- main-thread implementations

    @objc.python_method
    def _show(self, state):
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
