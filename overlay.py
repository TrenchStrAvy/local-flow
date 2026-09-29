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
import time

import numpy as np

import objc
from AppKit import (
    NSApplication,
    NSEvent,
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
from Foundation import (NSMakePoint, NSMakeRect, NSMakeSize, NSNumber,
                        NSObject, NSString, NSTimer, NSValue)
from Quartz import (CGContextSetLineCap, CGContextSetLineWidth,
                    CGContextSetRGBStrokeColor, CGContextStrokeLineSegments,
                    kCGLineCapRound)
from PyObjCTools import AppHelper

import settings

try:
    import SceneKit as SK
except Exception:                 # no SceneKit bindings → particle fallback
    SK = None


PILL_W, PILL_H = 224, 176     # particle sphere area (bottom of window)
CARD_W = 520                  # transcript card (window width)
CARD_PAD = 12
CARD_LINES = 3                # visible lines; older text scrolls away
CARD_FONT = 13.0
CARD_GAP = 6                  # between card and sphere
CORNER_RADIUS = 14
WIN_W = CARD_W
WIN_H = PILL_H + CARD_GAP + 2 * CARD_PAD + int(CARD_LINES * CARD_FONT * 1.5)
TICK_SEC = 1 / 60             # animation frame rate
LEVEL_GAIN = 16.0             # scales mic RMS (~0.02-0.3 for speech)

# --- particle sphere ("Ripple"): a cloud of points on a sphere. Every
# syllable lands on the surface like a drop and sends a ring outward across
# the sphere; quiet speech makes fine rings, loud speech big slow swells that
# cross each other. Each point sits on a spring so it overshoots and settles.
PARTICLES = 1500
SPHERE_R = 32.0               # rest radius, px
FOCAL = 300.0                 # perspective: larger = flatter
SPRING = 90.0                 # stiffness of each particle's radius
DAMPING = 5.5
IDLE_AMP = 0.02               # gentle background swell when quiet
VOICE_AMP = 0.22              # extra swell at full level
RING_AMP = 0.55               # ring height at full-level impact (fraction of R)
RING_SPEED = 2.4              # radians of arc per second
RING_LIFE = 3.0               # seconds a ring lives
MAX_RINGS = 14
ONSET_LEVEL = 0.07            # a syllable starts above this level...
ONSET_RISE = 1.2              # ...when it rises this much over the previous tick
DOT_MIN, DOT_MAX = 1.0, 2.6   # dot diameter far → near
ALPHA_MIN, ALPHA_MAX = 0.10, 0.95
LIGHT = np.array([-0.45, 0.6, 0.66])   # from upper left, toward viewer
LIGHT /= np.linalg.norm(LIGHT)


def _wave_bank(seed=3):
    """A few random plane waves over the sphere: the quiet background
    swell. Summing sines of (p·k + ωt) is cheap coherent noise."""
    rng = np.random.default_rng(seed)
    d = rng.normal(size=(4, 3))
    d /= np.linalg.norm(d, axis=1)[:, None]
    return (d * 2.0 * rng.uniform(0.8, 1.2, (4, 1)),
            rng.uniform(0.6, 1.4, 4), np.full(4, 0.25))


def _fibonacci_sphere(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi),
                     np.sin(theta) * np.sin(phi),
                     np.cos(phi)], axis=1)


def _appearance_is_dark():
    """Follow the system appearance: white particles on a dark desktop,
    black on a light one."""
    try:
        app = NSApplication.sharedApplication()
        name = app.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"])
        return name == "NSAppearanceNameDarkAqua"
    except Exception:
        return True


# --- energy sphere: a ball of liquid glass with three ribbons of light
# flowing inside, rendered by a single fragment shader on a quad (SceneKit,
# Metal). Tuned in the browser mock-up; ENERGY holds that snapshot.
ENERGY = {
    "hues": (236.0, 184.0, 290.0),  # ribbon colours, degrees (picked in the browser)
    "sat": 0.23,
    "glass": 1.00,                  # refraction / reflection / rim strength
    "level_gain": 4.2,              # floor mapping (mic RMS → level) for the first second
    "level_cap": 0.48,
    "speech_level": 0.26,           # where your normal speaking volume lands
    "ref_floor": 0.006,             # quietest "normal voice" the tracker assumes
    "ref_decay_sec": 12.0,          # how long the loudness memory lasts
    "attack_sec": 0.28,             # level rises over this
    "release_sec": 1.2,             # ...and falls over this
    "pulse_min": 0.09,              # syllable onset threshold (level)
    "pulse_sec": 1.0,               # pulse fade
}

ENERGY_SHADER = """
#pragma arguments
float u_t;
float u_level;
float u_pulse;
float u_dark;
float u_glass;
float u_lock;
float3 u_c1;
float3 u_c2;
float3 u_c3;
#pragma declaration
float lf_ph(float t, float k) { return fmod(t * k, 6.2831853); }
float3x3 lf_rotY(float a) { float c = cos(a), s = sin(a); return float3x3(float3(c, 0.0, -s), float3(0.0, 1.0, 0.0), float3(s, 0.0, c)); }
float3x3 lf_rotX(float a) { float c = cos(a), s = sin(a); return float3x3(float3(1.0, 0.0, 0.0), float3(0.0, c, s), float3(0.0, -s, c)); }
float lf_hash(float2 p) { return fract(sin(dot(p, float2(127.1, 311.7))) * 43758.5453); }
float lf_ribbon(float3 p, float3 A, float3 B, float3 C, float3 D, float ph, float amp, float spd, float width, float t) {
    float f = dot(p, A) + amp * sin(dot(p, B) * 2.6 + lf_ph(t, 0.9 * spd) + ph) + amp * 0.55 * sin(dot(p, C) * 4.1 - lf_ph(t, 1.4 * spd));
    float g = dot(p, D) + 0.35 * sin(dot(p, B) * 1.7 - lf_ph(t, 0.6 * spd) + ph * 0.5);
    float core = exp(-f * f / (width * width));
    float halo = exp(-f * f / (width * width * 6.0)) * 0.035;
    float strip = exp(-g * g / 0.16);
    return (core + halo) * strip;
}
float3 lf_wobble(float3 n, float lv, float t, float glass) {
    float a = (0.012 + 0.04 * lv) * (0.3 + 1.4 * glass);
    float3 w = float3(sin(n.y * 5.0 + lf_ph(t, 1.3)) * cos(n.z * 4.0 - lf_ph(t, 0.9)),
                      sin(n.z * 6.0 - lf_ph(t, 1.1)) * cos(n.x * 3.0 + lf_ph(t, 0.7)),
                      sin(n.x * 4.5 + lf_ph(t, 1.7)) * cos(n.y * 5.5 - lf_ph(t, 1.2)));
    return normalize(n + a * w);
}
float3 lf_env(float3 d) {
    float up = d.y * 0.5 + 0.5;
    float3 base = mix(float3(0.02, 0.02, 0.035), float3(0.09, 0.10, 0.14), up);
    float soft = pow(max(0.0, dot(d, normalize(float3(-0.55, 0.75, 0.5)))), 6.0);
    float panel = smoothstep(0.55, 0.95, dot(d, normalize(float3(0.6, 0.35, 0.7)))) * 0.35;
    return base + float3(1.0, 0.98, 0.95) * soft * 0.35 + float3(0.8, 0.85, 1.0) * panel * 0.5;
}
float3 lf_march(float3 ro, float3 rd, float tExit, float amp, float spd, float width, float3x3 M, float R, float dither, float t, float3 c1, float3 c2, float3 c3) {
    float3 col = float3(0.0);
    const int STEPS = 48;
    float dt = tExit / float(STEPS);
    for (int i = 0; i < STEPS; i++) {
        float3 p = ro + rd * ((float(i) + dither) * dt);
        float3 q = M * (p / R);
        float r1 = lf_ribbon(q, float3(0.0, 1.0, 0.0), float3(1.0, 0.2, 0.3), float3(0.4, 0.0, 1.0), float3(1.0, 0.0, 0.0), 0.0, amp, spd, width, t);
        float r2 = lf_ribbon(q, float3(0.3, 0.8, 0.5), float3(0.2, 1.0, -0.4), float3(1.0, 0.3, 0.0), float3(0.0, 0.0, 1.0), 2.1, amp * 0.9, spd * 0.85, width, t);
        float r3 = lf_ribbon(q, float3(-0.6, 0.5, 0.6), float3(0.5, -0.3, 1.0), float3(0.0, 1.0, 0.4), float3(0.8, 0.6, 0.0), 4.2, amp * 1.1, spd * 1.15, width * 0.9, t);
        float depth = clamp(length(q), 0.0, 1.0);
        float3 tint = mix(float3(0.92, 0.94, 1.0), float3(1.0), depth);
        col += ((c1 + 0.7 * r1) * r1 + (c2 + 0.7 * r2) * r2 + (c3 + 0.8 * r3) * r3) * tint;
    }
    return col * dt;
}
float3 lf_aces(float3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }
#pragma body
float2 uv = _surface.diffuseTexcoord - 0.5;
uv.y = -uv.y;
float3 ro = float3(0.0, 0.0, 3.2);
float3 rd = normalize(float3(uv * 1.05, -1.0));
float R = 0.62;
float b = dot(ro, rd);
float c = dot(ro, ro) - R * R;
float h = b * b - c;
if (h < 0.0) {
    _output.color = float4(0.0);
} else {
    float sq = sqrt(h);
    float t0 = -b - sq;
    float3 pIn = ro + rd * t0;
    float3 n = lf_wobble(normalize(pIn), u_level, u_t, u_glass);
    float cosI = max(0.0, dot(n, -rd));
    float fres = pow(1.0 - cosI, 4.0);
    float F = 0.04 + 0.96 * fres;
    float lv = u_level;
    float amp = 0.16 + 0.26 * lv;
    float spd = 0.8 + 1.0 * lv;
    float width = 0.042 + 0.012 * lv;
    float bright = 1.1 + 1.1 * lv + 0.9 * u_pulse;
    float3x3 M = lf_rotY(lf_ph(u_t, 0.18)) * lf_rotX(0.35 + 0.15 * sin(lf_ph(u_t, 0.3)));
    float dither = lf_hash(uv * 512.0 + fract(u_t) * 17.0);
    float3 inner = float3(0.0);
    for (int k = 0; k < 3; k++) {
        float eta = 1.0 / (1.0 + (0.42 + 0.02 * float(k)) * u_glass * 1.4);
        float3 rr = refract(rd, n, eta);
        float bb = dot(pIn, rr);
        float tOut = -bb + sqrt(max(0.0, bb * bb - (dot(pIn, pIn) - R * R)));
        float3 sm = lf_march(pIn, rr, tOut, amp, spd, width, M, R, dither, u_t, u_c1, u_c2, u_c3);
        inner[k] = sm[k];
    }
    inner *= bright * 2.6;
    float body = 0.30 + 0.30 * fres;             // translucent: desktop shows through
    float3 glass = float3(0.012, 0.013, 0.03) * body;
    float3 refl = reflect(rd, n);
    float3 reflection = lf_env(refl) * F * (0.2 + 1.3 * u_glass);
    float3 L = normalize(float3(-0.5, 0.8, 0.6));
    float3 hv = normalize(L - rd);
    float spec = (pow(max(0.0, dot(n, hv)), 220.0) * 0.9 + pow(max(0.0, dot(n, hv)), 30.0) * 0.07) * (0.2 + 1.3 * u_glass);
    float rimR = pow(1.0 - max(0.0, dot(lf_wobble(normalize(pIn), u_level * 1.02, u_t, u_glass), -rd)), 5.0);
    float rimB = pow(1.0 - cosI, 3.6);
    float3 rimCol = normalize(u_c1 + u_c2 + 0.001) * 1.1;
    rimCol = mix(rimCol, float3(1.0, 0.72, 0.3), u_lock);
    float3 rim = float3(rimCol.r * rimR, rimCol.g * fres, rimCol.b * rimB) * (0.7 + 0.8 * u_pulse + 0.4 * lv) * (0.4 + 1.0 * u_glass);
    float edgeDark = smoothstep(0.55, 0.85, fres) * (1.0 - smoothstep(0.85, 1.0, fres)) * u_glass;
    float3 rgb = glass + inner * (1.0 - edgeDark * 0.6) + reflection + rim + float3(spec);
    rgb = lf_aces(rgb);
    float alpha = clamp(body + dot(inner, float3(0.55)) + fres * 0.5 + spec, 0.0, 1.0);
    if (u_dark < 0.5) { alpha = min(1.0, alpha + 0.18); }
    _output.color = float4(rgb * alpha, alpha);
}
"""


def _hsl(h, s, l):
    def f(n):
        k = (n + h / 30.0) % 12
        a = s * min(l, 1 - l)
        return l - a * max(-1.0, min(k - 3, min(9 - k, 1.0)))
    return f(0), f(8), f(4)


class EnergySphere:
    """A quad filling the SCNView; the material's fragment modifier ray-
    marches the glass ball. Uniforms: time, level, pulse, appearance,
    glass strength, lock, and the three ribbon colours."""

    def __init__(self, parent, frame, config):
        self.view = SK.SCNView.alloc().initWithFrame_options_(frame, None)
        self.view.setBackgroundColor_(NSColor.clearColor())
        self.view.setAllowsCameraControl_(False)
        self.view.setAntialiasingMode_(SK.SCNAntialiasingModeNone)
        self.view.setAutoresizingMask_(0)
        scene = SK.SCNScene.scene()
        self.view.setScene_(scene)
        cam = SK.SCNNode.node()
        cam.setCamera_(SK.SCNCamera.camera())
        cam.camera().setUsesOrthographicProjection_(True)
        cam.camera().setOrthographicScale_(0.5)
        cam.setPosition_((0.0, 0.0, 2.0))
        scene.rootNode().addChildNode_(cam)
        plane = SK.SCNPlane.planeWithWidth_height_(1.0, 1.0)
        self.mat = plane.firstMaterial()
        self.mat.setLightingModelName_(SK.SCNLightingModelConstant)
        self.mat.setBlendMode_(SK.SCNBlendModeAlpha)
        self.mat.setWritesToDepthBuffer_(False)
        self.mat.setDoubleSided_(True)
        self.mat.diffuse().setContents_(NSColor.whiteColor())
        self.mat.setShaderModifiers_(
            {SK.SCNShaderModifierEntryPointFragment: ENERGY_SHADER})
        scene.rootNode().addChildNode_(SK.SCNNode.nodeWithGeometry_(plane))
        self.config = config
        self.set_colors(config["hues"], config["sat"])
        self._f("u_glass", config["glass"])
        self._f("u_dark", 1.0)
        self._f("u_lock", 0.0)
        parent.addSubview_(self.view)

    def _f(self, key, value):
        self.mat.setValue_forKey_(NSNumber.numberWithFloat_(float(value)), key)

    def set_colors(self, hues, sat):
        for i, hue in enumerate(hues[:3]):
            r, g, b = _hsl(hue, sat, 0.62)
            self.mat.setValue_forKey_(
                NSValue.valueWithSCNVector3_(SK.SCNVector3(r, g, b)),
                f"u_c{i + 1}")

    def set_dark(self, dark):
        self._f("u_dark", 1.0 if dark else 0.0)

    def update(self, t, level, pulse, locked):
        self._f("u_t", t)
        self._f("u_level", level)
        self._f("u_pulse", pulse)
        self._f("u_lock", 1.0 if locked else 0.0)

    def set_playing(self, on):
        self.view.setRendersContinuously_(bool(on))


class PillView(NSView):

    def initWithFrame_(self, frame):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.state = "recording"
        self.note = ""           # e.g. language name after a Q/W/E switch
        self.words = []          # [(word, style)] for the transcript card
        self._card_text = None   # cached, trimmed NSAttributedString
        self.smoothed = 0.0
        self.t = 0.0
        self.rot_y = 0.0
        self.rot_x = 0.0
        self._last_tick = 0.0
        self.dark = True
        self.moving = False
        self._drag = None
        self.setWantsLayer_(True)   # composite via a backing layer
        self.dirs = _fibonacci_sphere(PARTICLES)          # unit vectors
        self.radius = np.full(PARTICLES, SPHERE_R)        # spring state
        self.vel = np.zeros(PARTICLES)
        self.ks, self.ws, self.amps = _wave_bank()
        self.rings = []          # (t0, amplitude, unit direction)
        self.prev_level = 0.0
        self.rng = np.random.default_rng(7)
        self.surface = None
        self.energy = 0.0        # slow voice level for the energy sphere
        self.pulse = 0.0
        self._prev_raw = 0.0
        self.ref = 0.02          # running estimate of the speaker's loudness
        if SK is not None:
            try:
                side = PILL_H
                self.surface = EnergySphere(self, NSMakeRect(
                    (frame.size.width - side) / 2, 0, side, side), ENERGY)
            except Exception as exc:
                print(f"warning: energy sphere unavailable ({exc}); "
                      f"using particles", flush=True)
                self.surface = None
        return self

    @objc.python_method
    def _drop(self, amp):
        """A syllable hits the front of the sphere at a random spot."""
        z = self.rng.uniform(-0.2, 1.0)
        a = self.rng.uniform(0, 2 * np.pi)
        r = math.sqrt(max(0.0, 1 - z * z))
        self.rings.append((self.t, amp, np.array([r * math.cos(a),
                                                  r * math.sin(a), z])))
        del self.rings[:-MAX_RINGS]

    @objc.python_method
    def _field(self, lv):
        """Height over the sphere: background swell plus every live ring."""
        d = self.dirs
        ph = d @ self.ks.T + self.ws * self.t
        h = (np.sin(ph) @ self.amps) * (IDLE_AMP + VOICE_AMP * lv)
        for t0, amp, centre in self.rings:
            age = self.t - t0
            if age <= 0:
                continue
            ang = np.arccos(np.clip(d @ centre, -1, 1))     # arc distance
            front = age * RING_SPEED
            width = 0.35 + age * 0.15
            g = np.exp(-((ang - front) ** 2) / (width * width))
            h += (RING_AMP * amp * g * np.sin((ang - front) * 9)
                  * math.exp(-age * 1.3))
        return h

    # -- dragging (only while the overlay is in move mode)

    def mouseDown_(self, event):
        self._drag = (NSEvent.mouseLocation(), self.window().frame().origin)

    def mouseDragged_(self, event):
        if self._drag is None:
            return
        start, origin = self._drag
        now = NSEvent.mouseLocation()
        self.window().setFrameOrigin_(NSMakePoint(
            origin.x + now.x - start.x, origin.y + now.y - start.y))

    def mouseUp_(self, event):
        self._drag = None

    def set_state(self, state):
        if state == "recording" and self.state != "recording":
            self.smoothed = 0.0
            self.dark = _appearance_is_dark()
            if self.surface is not None:
                self.surface.set_dark(self.dark)
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
        """Called every tick: advance the physics by real elapsed time."""
        now = time.monotonic()
        dt = min(0.05, now - self._last_tick) if self._last_tick else 1 / 60
        self._last_tick = now
        self.t += dt

        if self.state in ("transcribing", "cleaning"):
            target = 0.05 + 0.03 * math.sin(self.t * 2.2)   # calm breathing
        else:
            target = math.tanh(level * LEVEL_GAIN)           # 0..1
        # instant attack, quick decay: syllables punch, gaps collapse
        decay = 0.02 ** dt
        self.smoothed = max(target, self.smoothed * decay
                            + target * (1 - decay))
        lv = self.smoothed

        # syllable onset → a drop; quiet speech gives fine rings
        if (target > ONSET_LEVEL and target > self.prev_level * ONSET_RISE
                and self.state not in ("transcribing", "cleaning")):
            self._drop(target)
        self.prev_level = target
        self.rings = [r for r in self.rings if self.t - r[0] < RING_LIFE]

        if self.surface is not None:
            cfg = ENERGY
            if self.state in ("transcribing", "cleaning"):
                raw = 0.08 + 0.03 * math.sin(self.t * 1.5)
            else:
                # adaptive gain: track the speaker's own loudness so a quiet
                # voice moves the sphere as much as a loud one
                if level > self.ref:
                    self.ref += (level - self.ref) * 0.25
                else:
                    self.ref = max(cfg["ref_floor"],
                                   self.ref * 0.5 ** (dt / cfg["ref_decay_sec"]))
                rel = level / self.ref * cfg["speech_level"]
                raw = min(cfg["level_cap"], max(rel, level * cfg["level_gain"]))
            tau = cfg["attack_sec"] if raw > self.energy else cfg["release_sec"]
            self.energy += (raw - self.energy) * (1 - 0.02 ** (dt / tau))
            if (raw > cfg["pulse_min"] and raw > self._prev_raw * 1.35
                    and self.state not in ("transcribing", "cleaning")):
                self.pulse = max(self.pulse, min(1.0, raw / cfg["level_cap"]) * 0.9)
            self._prev_raw = raw
            self.pulse *= 0.02 ** (dt / cfg["pulse_sec"])
            self.surface.update(self.t, self.energy, self.pulse,
                                self.note.startswith("🔒"))
        else:
            h = self._field(lv)
            rest = SPHERE_R * (1 + h)
            acc = SPRING * (rest - self.radius) - DAMPING * self.vel
            self.vel += acc * dt
            self.radius += self.vel * dt

        self.rot_y += dt * (0.14 + 1.6 * lv)
        self.rot_x = 0.35 * math.sin(self.t * 0.4)
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        W, H = b.size.width, PILL_H
        if self.words:
            self._draw_card(b.size.width, b.size.height)

        ink = 1.0 if self.dark else 0.0
        ctx = NSGraphicsContext.currentContext()
        cg = ctx.CGContext()
        ctx.saveGraphicsState()
        if self.surface is not None:
            self._draw_chrome(W, H, ink, ctx)
            return

        # rotate + project
        cy, sy = math.cos(self.rot_y), math.sin(self.rot_y)
        cx_, sx = math.cos(self.rot_x), math.sin(self.rot_x)
        p = self.dirs * self.radius[:, None]
        x = p[:, 0] * cy + p[:, 2] * sy
        z = -p[:, 0] * sy + p[:, 2] * cy
        y = p[:, 1] * cx_ - z * sx
        z = p[:, 1] * sx + z * cx_
        scale = FOCAL / (FOCAL - z)                 # nearer = larger
        sx_ = W / 2 + x * scale
        sy_ = H / 2 + 14 + y * scale
        rmax = SPHERE_R * (1 + IDLE_AMP + VOICE_AMP + RING_AMP)
        depth = np.clip((z + rmax) / (2 * rmax), 0, 1)     # 0 far .. 1 near
        # lighting: the rotated direction is the surface normal
        n = np.stack([x, y, z], axis=1) / np.maximum(
            np.linalg.norm(np.stack([x, y, z], axis=1), axis=1), 1e-6)[:, None]
        lit = np.clip(n @ LIGHT, 0, 1)
        tone = 0.35 * depth + 0.65 * lit                    # 0 dim .. 1 bright

        # one stroke call per tone bucket: zero-length round-capped
        # segments render as dots, thousands per call
        CGContextSetLineCap(cg, kCGLineCapRound)
        order = np.argsort(tone)
        buckets = np.array_split(order, 6)
        for k, idx in enumerate(buckets):
            if len(idx) == 0:
                continue
            frac = (k + 0.5) / len(buckets)
            dfrac = float(depth[idx].mean())
            CGContextSetLineWidth(cg, DOT_MIN + (DOT_MAX - DOT_MIN) * dfrac)
            CGContextSetRGBStrokeColor(
                cg, ink, ink, ink, ALPHA_MIN + (ALPHA_MAX - ALPHA_MIN) * frac)
            pts = np.repeat(np.stack([sx_[idx], sy_[idx]], axis=1), 2, axis=0)
            CGContextStrokeLineSegments(cg, pts.tolist(), len(pts))

        self._draw_chrome(W, H, ink, ctx)

    @objc.python_method
    def _draw_chrome(self, W, H, ink, ctx):
        """Caption and the move-mode ring (shared by both renderers)."""
        if self.moving:
            ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(
                W / 2 - SPHERE_R - 14, H / 2 + 14 - SPHERE_R - 14,
                2 * SPHERE_R + 28, 2 * SPHERE_R + 28))
            ring.setLineWidth_(1.0)
            ring.setLineDash_count_phase_([4.0, 4.0], 2, 0.0)
            NSColor.colorWithCalibratedWhite_alpha_(ink, 0.5).setStroke()
            ring.stroke()

        label = {"transcribing": "transcribing…",
                 "cleaning": "cleaning up…"}.get(self.state, self.note)
        if label:
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(10),
                NSForegroundColorAttributeName:
                    NSColor.colorWithCalibratedWhite_alpha_(ink, 0.75),
            }
            text = NSString.stringWithString_(label)
            size = text.sizeWithAttributes_(attrs)
            text.drawAtPoint_withAttributes_(
                NSMakePoint((W - size.width) / 2, 6), attrs)
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
        self.moving = False

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

        self._place(settings.get_position())
        return self

    @objc.python_method
    def _place(self, position):
        """Put the window in a screen corner (or bottom-centre), clear of
        the Dock and menu bar, or wherever it was dragged ("custom"). The
        sphere sits in the bottom PILL_H of the window and the transcript
        card above it."""
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        f = screen.visibleFrame()
        margin = 24
        if position == settings.CUSTOM:
            xy = settings.get_position_xy()
            if xy is not None:
                x = min(max(xy[0], f.origin.x - WIN_W + 80),
                        f.origin.x + f.size.width - 80)
                y = min(max(xy[1], f.origin.y - PILL_H + 40),
                        f.origin.y + f.size.height - 40)
                self.window.setFrameOrigin_(NSMakePoint(x, y))
                return
            position = settings.DEFAULT_POSITION
        x = {"left": f.origin.x + margin,
             "center": f.origin.x + (f.size.width - WIN_W) / 2,
             "right": f.origin.x + f.size.width - WIN_W - margin}[
                 position.split("-")[1]]
        if position.startswith("top"):
            y = f.origin.y + f.size.height - WIN_H - margin
        else:
            y = f.origin.y + margin
        self.window.setFrameOrigin_(NSMakePoint(x, y))

    def set_position(self, position):
        """Move the sphere; safe from any thread."""
        AppHelper.callAfter(self._place, position)

    def move_mode(self, active):
        """Show the sphere and let it be dragged anywhere (it is normally
        click-through). Leaving move mode saves the spot."""
        AppHelper.callAfter(self._move_mode, active)

    @objc.python_method
    def _move_mode(self, active):
        self.moving = active
        self.view.moving = active
        if active:
            self.window.setIgnoresMouseEvents_(False)
            self._show("recording")
            self.view.note = "drag me · then choose “Done moving”"
        else:
            self.window.setIgnoresMouseEvents_(True)
            o = self.window.frame().origin
            settings.set_position_xy(o.x, o.y)
            self.view.note = ""
            self._hide()

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
        if self.view.surface is not None:
            self.view.surface.set_playing(True)
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
        if self.moving:
            return                   # stay visible while being dragged
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        if self.view.surface is not None:
            self.view.surface.set_playing(False)
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
