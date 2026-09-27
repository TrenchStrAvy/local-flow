#!/usr/bin/env python3
"""local-flow — a free, fully offline Wispr Flow clone for macOS.

Hold Right-Option → speak → release. Your words are transcribed locally
with faster-whisper, optionally cleaned up by a local Ollama model, and
pasted into whatever app has focus.

Pipeline:  hotkey → mic capture → faster-whisper (Stage 1)
           → cleanup (Stage 2: rules, or a local Ollama model) → clipboard + Cmd+V

While the key is held, a preview loop re-transcribes the audio so far about
once a second and types the words straight into the field you are dictating
into, correcting them in place as later audio refines them; after release
the cleanup pass corrects the text the same way. (--preview card shows the
words in the overlay instead and pastes at the end; --preview off waits.)
"""

import argparse
import os
import queue
import socket
import subprocess
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from pynput import keyboard
from pynput.keyboard import Controller, Key

import cleanup
import languages
import settings

try:
    import typer
except Exception as _exc:  # Quartz missing → no inline typing
    typer = None
    print(f"warning: inline typing unavailable ({_exc})")

# ---------------------------------------------------------------- config

SAMPLE_RATE = 16_000          # what Whisper expects
CHANNELS = 1
HOTKEY = Key.alt_r            # Right-Option: hold to record
# while holding the hotkey, letters (Q/W/E/R/T, user-assignable in the
# menu bar) switch the dictation language; the keystroke is swallowed.
# Read from settings on every press so menu changes apply immediately.
MIN_RECORDING_SEC = 0.4       # ignore accidental taps

DEFAULT_MODEL = "small.en"    # good speed/accuracy balance on CPU
OLLAMA_MODEL = "gemma3:4b"    # only opt-in; smaller models rewrite sentences
CPU_THREADS = max(1, (os.cpu_count() or 8) // 2)   # physical cores (i9: 8)

PREVIEW_MIN_SEC = 0.8         # don't preview until this much audio exists
PREVIEW_STEP_SEC = 0.5        # new audio needed before re-transcribing
PREVIEW_WINDOW_SEC = 24       # commit text older than this; decode the tail
REUSE_TAIL_SEC = 0.6          # skip the final pass if the un-previewed tail
REUSE_TAIL_RMS = 0.01         #   is shorter than this and this quiet
SHOW_CORRECTIONS_SEC = 0.7    # how long the diff stays visible before paste


# ---------------------------------------------------------------- recorder

class Recorder:
    """Captures mic audio between start() and stop()."""

    def __init__(self):
        self._chunks = []
        self._lock = threading.Lock()
        self._stream = None
        self.level = 0.0        # live mic RMS, read by the overlay

    def _on_audio(self, data, *_):
        with self._lock:
            self._chunks.append(data.copy())
        self.level = float(np.sqrt((data ** 2).mean()))

    def snapshot(self) -> np.ndarray:
        """Everything captured so far (recording continues)."""
        with self._lock:
            chunks = list(self._chunks)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).flatten()

    def start(self):
        with self._lock:
            self._chunks = []
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
        return self.snapshot()


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
                    name, device="cpu", compute_type="int8",
                    cpu_threads=CPU_THREADS)
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
            vad_parameters={"min_silence_duration_ms": 300},
            beam_size=1,              # greedy: fastest, fine for dictation
            condition_on_previous_text=False,  # no cross-segment context
            language=language,
        )
        return " ".join(s.text.strip() for s in segments).strip()


# ---------------------------------------------------------------- stage 2: cleanup

def ollama_warmup():
    """Load the cleanup model into RAM in the background so the first real
    dictation doesn't pay the multi-second cold start."""
    def _warm():
        if cleanup.ollama_warmup(OLLAMA_MODEL):
            print(f"({OLLAMA_MODEL} warmed up)")
        else:
            print(f"warning: Ollama not reachable at {cleanup.OLLAMA_URL} — "
                  f"cleanup will fall back to rules only")
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
                 language: str = "en", progress=None,
                 preview: str = "inline"):
        report = progress or (lambda caption, frac: None)
        if use_ollama:
            report("warming up cleanup model", 0.2)
            ollama_warmup()
        if preview == "inline" and typer is None:
            preview = "card"
        self.preview = preview          # inline | card | off
        self.typer = typer.LiveTyper() if typer is not None else None
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
        self._preview_thread = None
        self._preview_state = {}    # filled by the preview loop
        self.worker = queue.Queue()
        threading.Thread(target=self._process_loop, daemon=True).start()

    def status_text(self) -> str:
        return (f"model {self.transcriber.model_name} · cleanup "
                f"{OLLAMA_MODEL if self.use_ollama else 'rules'}")

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
            self._preview_state = {"text": "", "committed": "",
                                   "committed_n": 0, "seen_n": 0,
                                   "target": self.target}
            if self.typer:
                self.typer.reset()
            if self.preview != "off":
                self._preview_thread = threading.Thread(
                    target=self._preview_loop, daemon=True)
                self._preview_thread.start()
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
            self.worker.put((audio, self.target, self._preview_thread,
                             self._preview_state))

    # -- live preview (runs while the key is held)

    def _preview_loop(self):
        """Re-transcribe the captured audio about once a second and show the
        words. Text older than PREVIEW_WINDOW_SEC is committed and only the
        tail is decoded, so long dictations stay cheap."""
        st = self._preview_state
        while self.recording:
            audio = self.recorder.snapshot()
            n = len(audio)
            if (n < PREVIEW_MIN_SEC * SAMPLE_RATE
                    or n - st["seen_n"] < PREVIEW_STEP_SEC * SAMPLE_RATE):
                time.sleep(0.05)
                continue
            tail = audio[st["committed_n"]:]
            try:
                text = self.transcriber.transcribe(tail)
            except Exception as exc:       # never let preview kill dictation
                print(f"warning: preview failed: {exc}", flush=True)
                return
            # even if the key was released mid-decode, the result covers
            # audio up to `n`; the final pass may reuse it
            st["seen_n"] = n
            st["text"] = (st["committed"] + " " + text).strip()
            self._show(st["text"], self._preview_state.get("target"))
            if len(tail) > PREVIEW_WINDOW_SEC * SAMPLE_RATE:
                st["committed"] = st["text"]
                st["committed_n"] = n

    def _show(self, text, target, words=None):
        """Put `text` where the user can see it: typed into the target
        field (inline) or in the overlay card. Inline typing is skipped
        while focus is away from the target; the final pass restores it."""
        if self.preview == "inline" and self.typer:
            if focus is not None and target is not None:
                current = focus._focused_element()
                if current is not None and current != target.element:
                    return
            self.typer.update(text)
        elif self.overlay and self.preview == "card":
            if words is not None:
                self.overlay.set_words(words)
            else:
                self.overlay.set_text(text)

    # -- background pipeline

    def _final_text(self, audio, preview_thread, st) -> str:
        """Transcript of the whole recording. Reuses the last preview when
        it already covers everything but a short, silent tail."""
        if preview_thread is not None:
            preview_thread.join()
        tail = audio[st.get("seen_n", 0):]
        if (st.get("text") and len(tail) < REUSE_TAIL_SEC * SAMPLE_RATE
                and (len(tail) == 0
                     or float(np.sqrt((tail ** 2).mean())) < REUSE_TAIL_RMS)):
            return st["text"]
        committed_n = st.get("committed_n", 0)
        text = self.transcriber.transcribe(audio[committed_n:])
        return (st.get("committed", "") + " " + text).strip()

    def _process_loop(self):
        while True:
            audio, target, preview_thread, st = self.worker.get()
            try:
                t0 = time.time()
                raw = self._final_text(audio, preview_thread, st)
                if not raw:
                    print("  (heard nothing)")
                    continue
                stt_ms = (time.time() - t0) * 1000
                inline = self.preview == "inline" and self.typer
                if inline and focus is not None and target is not None:
                    if not focus.restore(target):
                        print(f"  (could not refocus {target.app_name}; "
                              f"typing into the current app)")
                self._show(raw, None)
                if self.overlay:
                    self.overlay.show_cleaning()

                t1 = time.time()
                language = self.transcriber.language
                text = cleanup.rule_cleanup(raw, language)
                if self.use_ollama:
                    text = cleanup.ollama_cleanup(
                        text, language, OLLAMA_MODEL,
                        on_partial=self._show_partial(raw))
                    text = cleanup.rule_cleanup(text, language)
                clean_ms = (time.time() - t1) * 1000

                if text != raw:
                    print(f"  raw: {raw}")
                    if inline:
                        time.sleep(0.25)      # let the raw text register
                        self._show(text, None)
                    elif self.overlay and self.preview == "card":
                        self.overlay.set_words(cleanup.word_diff(raw, text))
                        time.sleep(SHOW_CORRECTIONS_SEC)
                print(f"→ {text}   [stt {stt_ms:.0f}ms, cleanup "
                      f"{clean_ms:.0f}ms]", flush=True)
                if inline:
                    self.typer.update(text)   # no-op if already there
                else:
                    inject_text(text, target)
            finally:
                if self.overlay:
                    self.overlay.hide()

    def _show_partial(self, raw):
        """While the LLM streams, paint its output over the raw text: the
        cleaned prefix bright, the not-yet-reached remainder dim."""
        raw_words = raw.split()

        def _on_partial(partial):
            done = partial.split()
            rest = raw_words[len(done):]
            self._show(" ".join(done + rest), None,
                       words=[(w, "same") for w in done]
                       + [(w, "dim") for w in rest])
        return _on_partial

    def start_listener(self):
        print(f"Hold Right-Option to dictate. Cleanup: "
              f"{'ollama/' + OLLAMA_MODEL if self.use_ollama else 'rules'}. "
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
    global OLLAMA_MODEL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="faster-whisper model (tiny.en/small.en/medium.en)")
    parser.add_argument("--ollama", action="store_true",
                        help=f"also clean up transcripts with an Ollama model "
                             f"(default {OLLAMA_MODEL}); rules-only otherwise")
    parser.add_argument("--ollama-model", metavar="NAME",
                        help="Ollama model for --ollama")
    parser.add_argument("--preview", choices=["inline", "card", "off"],
                        default="inline",
                        help="while recording: type words into the field "
                             "(inline, default), show them in the overlay "
                             "(card), or wait for release (off)")
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
    if args.ollama_model:
        OLLAMA_MODEL = args.ollama_model

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
        t1 = time.time()
        cleaned = cleanup.rule_cleanup(text, language)
        if args.ollama:
            cleaned = cleanup.ollama_cleanup(cleaned, language, OLLAMA_MODEL)
            cleaned = cleanup.rule_cleanup(cleaned, language)
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
        app = FlowApp(args.model, args.ollama, language=language,
                      preview=args.preview)
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
                          progress=splash.set_stage,
                          preview=args.preview)
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
