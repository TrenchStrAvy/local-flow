"""Startup splash — a centered card with a thin progress bar shown while
local-flow loads its speech model, then fades away once dictation is ready.

Gives the launcher click immediate visible feedback (think FL Studio's
splash) instead of several silent seconds before the mic icon appears.
Pure Cocoa via pyobjc, same look as the recording pill in overlay.py.

Public methods are safe to call from any thread.
"""

import time

import objc
from AppKit import (
    NSAffineTransform,
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


CARD_W, CARD_H = 320, 112
CORNER_RADIUS = 18
PAD = 28                     # transparent margin so the shadow isn't clipped
BAR_W, BAR_H = 240, 4
TICK_SEC = 1 / 60
FADE_SEC = 0.35
HOLD_SEC = 0.45              # show "ready" briefly before fading
CREEP = 0.06                 # per-second drift toward the stage target


class SplashView(NSView):

    def initWithFrame_(self, frame):
        self = objc.super(SplashView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.progress = 0.0
        self.target = 0.08
        self.caption = "starting…"
        self.alpha = 1.0
        return self

    def isFlipped(self):
        return False

    def drawRect_(self, rect):
        W, H = CARD_W, CARD_H
        # draw the card inset by PAD; everything below is in card coordinates
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        xf = NSAffineTransform.transform()
        xf.translateXBy_yBy_(PAD, PAD)
        xf.concat()

        # no card: title, caption and bar float with a soft shadow each
        ink = 0.97   # white ink + dark shadow reads on any background
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.7))
        shadow.setShadowBlurRadius_(8)
        shadow.setShadowOffset_(NSMakeSize(0, -2))
        shadow.set()

        title_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(17, 0.4),
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedWhite_alpha_(ink, 1.0),
        }
        title = NSString.stringWithString_("local-flow")
        ts = title.sizeWithAttributes_(title_attrs)
        title.drawAtPoint_withAttributes_(
            NSMakePoint((W - ts.width) / 2, H - 22 - ts.height / 2 - 6),
            title_attrs)

        cap_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(11.5),
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedWhite_alpha_(ink, 0.75),
        }
        cap = NSString.stringWithString_(self.caption)
        cs = cap.sizeWithAttributes_(cap_attrs)
        cap.drawAtPoint_withAttributes_(
            NSMakePoint((W - cs.width) / 2, 46 - cs.height / 2), cap_attrs)

        # progress track + fill
        x0, y0 = (W - BAR_W) / 2, 22
        track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x0, y0, BAR_W, BAR_H), BAR_H / 2, BAR_H / 2)
        NSColor.colorWithCalibratedWhite_alpha_(ink, 0.18).setFill()
        track.fill()
        fw = max(BAR_H, BAR_W * min(1.0, self.progress))
        fill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x0, y0, fw, BAR_H), BAR_H / 2, BAR_H / 2)
        NSColor.colorWithCalibratedWhite_alpha_(ink, 1.0).setFill()
        fill.fill()
        ctx.restoreGraphicsState()


class Splash(NSObject):

    def init(self):
        self = objc.super(Splash, self).init()
        if self is None:
            return None
        rect = NSMakeRect(0, 0, CARD_W + 2 * PAD, CARD_H + 2 * PAD)
        self.view = SplashView.alloc().initWithFrame_(rect)
        self.window = NSWindow.alloc(
        ).initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(False)   # we draw our own, softer one
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
                f.origin.x + (f.size.width - CARD_W) / 2 - PAD,
                f.origin.y + (f.size.height - CARD_H) / 2 + 40 - PAD))

        self.timer = None
        self.last_tick = time.time()
        self.fade_started = None
        self.done_at = None
        return self

    # -- public, any thread

    def show(self):
        AppHelper.callAfter(self._show)

    def set_stage(self, caption, target):
        """Update caption and the progress the bar should approach (0-1)."""
        AppHelper.callAfter(self._set_stage, caption, float(target))

    def finish(self, caption="ready — hold Right-Option to dictate"):
        """Fill the bar, hold briefly, fade out, close."""
        AppHelper.callAfter(self._finish, caption)

    # -- main thread

    def _show(self):
        self.window.setAlphaValue_(0.0)
        self.window.orderFrontRegardless()
        self.fade_in_started = time.time()
        self._ensure_timer()

    @objc.python_method
    def _set_stage(self, caption, target):
        self.view.caption = caption
        self.view.target = max(self.view.target, target)
        self.view.setNeedsDisplay_(True)

    @objc.python_method
    def _finish(self, caption):
        self.view.caption = caption
        self.view.target = 1.0
        self.done_at = time.time()
        self.view.setNeedsDisplay_(True)

    def _ensure_timer(self):
        if self.timer is None:
            self.timer = (
                NSTimer.
                scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    TICK_SEC, self, b"tick:", None, True))

    def tick_(self, _timer):
        now = time.time()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now
        v = self.view

        # progress eases toward its target; between stages it creeps a
        # little so the bar never looks frozen during a long model load
        gap = v.target - v.progress
        if self.done_at is not None:
            v.progress = min(1.0, v.progress + max(gap * 8.0, 1.2) * dt)
        elif gap > 0.001:
            v.progress += gap * 4.0 * dt
        else:
            v.target = min(v.target + CREEP * dt, 0.93)

        # fade in
        alpha = min(1.0, (now - self.fade_in_started) / 0.2)
        # hold on "ready", then fade out and close
        if self.done_at is not None and v.progress >= 0.999:
            if now - self.done_at >= HOLD_SEC:
                if self.fade_started is None:
                    self.fade_started = now
                alpha = max(0.0, 1.0 - (now - self.fade_started) / FADE_SEC)
                if alpha <= 0.0:
                    self.timer.invalidate()
                    self.timer = None
                    self.window.orderOut_(None)
                    return
        self.window.setAlphaValue_(alpha)
        v.setNeedsDisplay_(True)


def create_splash():
    """Main thread only; the caller must run AppHelper.runEventLoop()."""
    return Splash.alloc().init()
