#!/usr/bin/env python3
"""local-flow — a free, fully offline Wispr Flow clone for macOS.

Hold Right-Option → speak → release. Your words are transcribed locally
with faster-whisper, optionally cleaned up by a local Ollama model, and
pasted into whatever app has focus.

Pipeline:  hotkey → mic capture → faster-whisper (Stage 1)
           → Ollama cleanup (Stage 2, optional) → clipboard + Cmd+V
"""

import argparse
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import sounddevice as sd
from pynput import keyboard
from pynput.keyboard import Controller, Key

import languages
import settings

# ---------------------------------------------------------------- config

SAMPLE_RATE = 16_000          # what Whisper expects
CHANNELS = 1
HOTKEY = Key.alt_r            # Right-Option: hold to record
# while holding the hotkey, letters (Q/W/E/R/T, user-assignable in the
# menu bar) switch the dictation language; the keystroke is swallowed.
# Read from settings on every press so menu changes apply immediately.
MIN_RECORDING_SEC = 0.4       # ignore accidental taps

DEFAULT_MODEL = "small.en"    # good speed/accuracy balance on CPU
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_TIMEOUT = 8            # seconds; fall back to raw transcript after this

CLEANUP_PROMPT = (
    "Fix punctuation and capitalization, and remove filler words "
    "(um, uh, you know, like, äh, ähm, euh) from this dictated text. Do not "
    "change the meaning, do not add anything, do not answer questions in "
    "the text. The text is in {language}: keep it in {language}, do NOT "
    "translate it. Return ONLY the corrected text.\n\nText: {text}"
)


# ---------------------------------------------------------------- recorder

class Recorder:
    """Captures mic audio between start() and stop()."""

    def __init__(self):
        self._chunks = queue.Queue()
        self._stream = None
        self.level = 0.0        # live mic RMS, read by the overlay

    def _on_audio(self, data, *_):
        self._chunks.put(data.copy())
        self.level = float(np.sqrt((data ** 2).mean()))

    def start(self):
        self._chunks = queue.Queue()
        self.level = 0.0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        self._stream.stop()
        self._stream.close()
        self._stream = None
        chunks = []
        while not self._chunks.empty():
            chunks.append(self._chunks.get())
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).flatten()


# ---------------------------------------------------------------- stage 1: STT

class Transcriber:
    """faster-whisper wrapper that follows the configured language.

    `base_model` is what the user asked for (e.g. small.en). English-only
    models can't decode German/French, so switching language may swap in
    the multilingual sibling (small). Loaded models are cached so flipping
    back and forth is instant after the first load.
    """

    def __init__(self, base_model: str, language: str = "en"):
        self.base_model = base_model
        self.language = language
        self._models = {}
        self._lock = threading.Lock()
        self._switch = threading.RLock()
        self.model_name = settings.model_for_language(base_model, language)
        self.model = self._load(self.model_name)

    def _load(self, name: str):
        with self._lock:
            if name not in self._models:
                from faster_whisper import WhisperModel
                print(f"Loading {name} (first run downloads the model)...",
                      flush=True)
                t0 = time.time()
                self._models[name] = WhisperModel(
                    name, device="cpu", compute_type="int8")
                print(f"Model ready in {time.time() - t0:.1f}s", flush=True)
            return self._models[name]

    def set_language(self, language: str):
        """Switch dictation language, loading a different model if needed.
        Holds the switch lock so a transcription started during the swap
        waits for the right model instead of decoding with the old one."""
        with self._switch:
            name = settings.model_for_language(self.base_model, language)
            if name != self.model_name:
                self.model = self._load(name)
                self.model_name = name
            self.language = language

    def transcribe(self, audio: np.ndarray) -> str:
        with self._switch:
            model, language = self.model, self.language
        segments, _ = model.transcribe(
            audio,
            vad_filter=True,          # trim silence before decoding
            beam_size=1,              # greedy: fastest, fine for dictation
            language=language,
        )
        return " ".join(s.text.strip() for s in segments).strip()


# ---------------------------------------------------------------- stage 2: LLM cleanup

def ollama_cleanup(text: str, language: str = "en") -> str:
    """Ask a local Ollama model to clean up the transcript.

    Returns the raw text unchanged on any failure or timeout — dictation
    must never hang or lose words because the cleanup model is slow.
    """
    language_name = languages.english_name(language)
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": CLEANUP_PROMPT.format(text=text, language=language_name),
        "stream": False,
        "keep_alive": "60m",   # keep the model in RAM between dictations
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            cleaned = json.loads(resp.read())["response"].strip()
            return cleaned if cleaned else text
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError):
        return text


def ollama_warmup():
    """Load the cleanup model into RAM in the background so the first real
    dictation doesn't pay the multi-second cold-start and hit the timeout."""
    def _warm():
        payload = json.dumps(
            {"model": OLLAMA_MODEL, "prompt": "", "keep_alive": "60m"}
        ).encode()
        req = urllib.request.Request(
            OLLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=120).read()
            print(f"({OLLAMA_MODEL} warmed up)")
        except (urllib.error.URLError, TimeoutError):
            print(f"warning: Ollama not reachable at {OLLAMA_URL} — "
                  f"cleanup will fall back to raw transcripts")
    threading.Thread(target=_warm, daemon=True).start()


# ---------------------------------------------------------------- injection

_kbd = Controller()

try:
    import focus
except Exception as _exc:  # pyobjc missing → old behaviour (paste wherever)
    focus = None
    print(f"warning: focus tracking unavailable ({_exc})")


def inject_text(text: str, target=None):
    """Paste text into the app that had focus when dictation started
    (falls back to whatever is focused now): clipboard → Cmd+V → restore."""
    if focus is not None and target is not None:
        if not focus.restore(target):
            print(f"  (could not refocus {target.app_name}; "
                  f"pasting into the current app)")
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode())
    time.sleep(0.05)
    with _kbd.pressed(Key.cmd):
        _kbd.press("v")
        _kbd.release("v")
    time.sleep(0.3)  # let the paste land before restoring
    subprocess.run(["pbcopy"], input=old)


# ---------------------------------------------------------------- app

class FlowApp:
    def __init__(self, model_name: str, use_ollama: bool, overlay=None,
                 language: str = "en", progress=None):
        report = progress or (lambda caption, frac: None)
        if use_ollama:
            report("warming up cleanup model", 0.2)
            ollama_warmup()
        self.language = language
        report(f"loading speech model "
               f"({settings.model_for_language(model_name, language)})", 0.3)
        self.transcriber = Transcriber(model_name, language)
        report("almost there", 0.9)
        self.recorder = Recorder()
        self.use_ollama = use_ollama
        self.overlay = overlay
        self.menubar = None
        self.recording = False
        self.record_started = 0.0
        self.target = None          # where the text should land
        self.worker = queue.Queue()
        threading.Thread(target=self._process_loop, daemon=True).start()

    def status_text(self) -> str:
        return (f"model {self.transcriber.model_name} · cleanup "
                f"{OLLAMA_MODEL if self.use_ollama else 'off'}")

    # -- language (called from the menu bar, on the main thread)

    def set_language(self, code: str):
        """Switch language; safe from the hotkey thread or the menu."""
        if code == self.language:
            return
        settings.set_language(code)
        self.language = code
        if self.overlay:
            self.overlay.set_note(languages.native_name(code))
        if self.menubar:
            self.menubar.set_language(code)
            self.menubar.set_status("loading model…")

        def _switch():
            try:
                self.transcriber.set_language(code)
                print(f"\nlanguage → {languages.label(code)} "
                      f"({self.transcriber.model_name})", flush=True)
            except Exception as exc:
                print(f"warning: could not switch language: {exc}",
                      flush=True)
            if self.menubar:
                self.menubar.set_status(self.status_text())
        threading.Thread(target=_switch, daemon=True).start()

    # -- hotkey handlers (must return fast; heavy work goes to the worker)

    def on_press(self, key):
        if self.recording and getattr(key, "vk", None) is not None:
            code = settings.quick_keys_by_keycode().get(key.vk)
            if code:
                self.set_language(code)
                return
        if key == HOTKEY and not self.recording:
            self.recording = True
            self.record_started = time.time()
            self.target = focus.capture() if focus is not None else None
            self.recorder.start()
            if self.overlay:
                self.overlay.set_note("")
            if self.overlay:
                self.overlay.show_recording()
            print("● recording... (release to transcribe)", flush=True)

    def on_release(self, key):
        if key == HOTKEY and self.recording:
            self.recording = False
            audio = self.recorder.stop()
            if time.time() - self.record_started < MIN_RECORDING_SEC:
                if self.overlay:
                    self.overlay.hide()
                print("  (too short, ignored)")
                return
            if self.overlay:
                self.overlay.show_transcribing()
            self.worker.put((audio, self.target))

    # -- background pipeline

    def _process_loop(self):
        while True:
            audio, target = self.worker.get()
            try:
                t0 = time.time()
                text = self.transcriber.transcribe(audio)
                if not text:
                    print("  (heard nothing)")
                    continue
                stt_ms = (time.time() - t0) * 1000
                if self.use_ollama:
                    t1 = time.time()
                    text = ollama_cleanup(text, self.transcriber.language)
                    print(f"→ {text}   [stt {stt_ms:.0f}ms, cleanup "
                          f"{(time.time() - t1) * 1000:.0f}ms]")
                else:
                    print(f"→ {text}   [stt {stt_ms:.0f}ms]")
                inject_text(text, target)
            finally:
                if self.overlay:
                    self.overlay.hide()

    def start_listener(self):
        print(f"Hold Right-Option to dictate. Cleanup: "
              f"{'ollama/' + OLLAMA_MODEL if self.use_ollama else 'off'}. "
              f"Language: {languages.label(self.language)}. Ctrl+C to quit.",
              flush=True)
        self.listener = keyboard.Listener(on_press=self.on_press,
                                          on_release=self.on_release,
                                          darwin_intercept=self._intercept)
        self.listener.start()
        return self.listener

    def _intercept(self, event_type, event):
        """Swallow Q/W/E while the hotkey is held so Option+Q doesn't
        also type 'œ' into the target app. Everything else passes."""
        if self.recording:
            try:
                from Quartz import (CGEventGetIntegerValueField,
                                    kCGKeyboardEventKeycode)
                if CGEventGetIntegerValueField(
                        event, kCGKeyboardEventKeycode) in \
                        settings.quick_keys_by_keycode():
                    return None
            except Exception:
                pass
        return event

    def run(self):
        """Terminal-only mode (--no-pill): block on the hotkey listener."""
        self.start_listener().join()


def acquire_single_instance_lock():
    """Bind a localhost port as a cross-process mutex. Returns the socket
    (keep it alive!) or None if another local-flow is already running."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", 47611))
        lock.listen(1)
        return lock
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="faster-whisper model (tiny.en/small.en/medium.en)")
    parser.add_argument("--ollama", action="store_true",
                        help=f"clean up transcripts with {OLLAMA_MODEL} via Ollama")
    parser.add_argument("--transcribe", metavar="WAV",
                        help="transcribe an audio file and exit (pipeline test)")
    parser.add_argument("--no-pill", action="store_true",
                        help="disable the floating recording indicator")
    parser.add_argument("--language", metavar="CODE",
                        help="dictation language code, e.g. de (default: "
                             "last choice from the menu bar, else en)")
    parser.add_argument("--list-languages", action="store_true",
                        help="print every supported language code and exit")
    parser.add_argument("--add-language", metavar="CODE", action="append",
                        help="add a language to the menu bar (repeatable)")
    parser.add_argument("--quick-key", metavar="LETTER=CODE", action="append",
                        help="assign a quick key, e.g. r=es (Q/W/E/R/T)")
    args = parser.parse_args()

    if args.list_languages:
        enabled = set(settings.get_languages())
        for code in languages.sorted_codes():
            mark = "*" if code in enabled else " "
            print(f"{mark} {code:4} {languages.english_name(code):22} "
                  f"{languages.native_name(code)}")
        print("\n* = shown in the menu bar")
        return
    if args.add_language or args.quick_key:
        for code in args.add_language or []:
            settings.add_language(code)
            print(f"added {languages.label(code)}")
        for spec in args.quick_key or []:
            slot, _, code = spec.partition("=")
            settings.add_language(code)
            settings.set_quick_key(slot.lower(), code)
            print(f"quick key {slot.upper()} → {languages.label(code)}")
        print("restart local-flow (or reopen the menu) to see the change")
        return

    language = args.language or settings.get_language()
    if language not in languages.CATALOG:
        parser.error(f"unknown language code {language!r}; "
                     f"see --list-languages")

    if args.transcribe:
        transcriber = Transcriber(args.model, language)
        t0 = time.time()
        text = transcriber.transcribe(args.transcribe)
        print(f"[{(time.time() - t0) * 1000:.0f}ms] {text}")
        if args.ollama:
            t1 = time.time()
            cleaned = ollama_cleanup(text, language)
            print(f"[cleanup {(time.time() - t1) * 1000:.0f}ms] {cleaned}")
        return

    lock = acquire_single_instance_lock()
    if lock is None:
        print("another local-flow instance is already running — exiting")
        return

    try:
        if args.no_pill:
            raise RuntimeError("--no-pill")
        run_with_ui(args, language, lock)
    except KeyboardInterrupt:
        print("\nbye")
    except Exception as exc:  # UI is cosmetic — never block dictation
        if not args.no_pill:
            print(f"warning: overlay unavailable ({exc}); "
                  f"terminal feedback only")
        app = FlowApp(args.model, args.ollama, language=language)
        app._lock = lock
        try:
            app.run()
        except KeyboardInterrupt:
            print("\nbye")


def run_with_ui(args, language, lock):
    """Cocoa mode: splash while loading, then menu bar + pill + hotkey.

    The event loop must own the main thread, so the (slow) model load runs
    on a worker and hands the finished FlowApp back via callAfter.
    """
    from PyObjCTools import AppHelper
    from overlay import create_overlay
    from menubar import create_menubar
    from splash import create_splash

    overlay = create_overlay(lambda: 0.0)   # also sets up NSApplication
    splash = create_splash()
    splash.show()
    state = {}

    def _ready(app):
        app._lock = lock
        app.overlay = overlay
        overlay.level_source = lambda: app.recorder.level
        app.menubar = create_menubar(
            app.status_text(), app.language, app.set_language)
        app.start_listener()
        splash.finish()

    def _failed(exc):
        splash.finish(f"failed to start: {exc}")
        print(f"fatal: {exc}", flush=True)
        AppHelper.callLater(3.0, AppHelper.stopEventLoop)
        state["error"] = exc

    def _load():
        try:
            app = FlowApp(args.model, args.ollama, language=language,
                          progress=splash.set_stage)
        except Exception as exc:
            AppHelper.callAfter(_failed, exc)
            return
        AppHelper.callAfter(_ready, app)

    threading.Thread(target=_load, daemon=True).start()
    AppHelper.runEventLoop(installInterrupt=True)
    if "error" in state:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
