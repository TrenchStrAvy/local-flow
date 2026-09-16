# local-flow

**Free, fully offline, multilingual dictation for macOS.**

Hold **Right-Option**, speak, release. Your words are transcribed on your
own CPU and pasted into whatever text field you were in. No account, no API
key, no credits, no network: nothing you say leaves your machine.

```
Hold Right-Option → mic capture
    → faster-whisper (speech-to-text, local CPU, 99 languages)
    → Ollama gemma3:4b (optional cleanup: punctuation, filler words, local)
    → pasted into the text field you started in
```

## Features

- **Offline speech-to-text** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
  Runs on the CPU (int8), so it works on Intel Macs, not only Apple Silicon.
- **99 languages.** English, German, French, Spanish, … the whole Whisper
  catalog. Pick the ones you use from the menu bar; one multilingual model
  covers them all, so there is nothing extra to download per language.
- **Quick keys while dictating.** Hold Right-Option and tap **Q**, **W**, or
  **E** (also R and T) to switch language mid-sentence. The letters are
  assignable in the menu; the keystroke never reaches your document.
- **Pastes into the field you started in.** If you click somewhere else while
  speaking or while transcription is running, local-flow re-focuses the
  original text field before pasting (via the Accessibility API).
- **Optional local cleanup** with Ollama: fixes punctuation and
  capitalization, drops "um / äh / euh", never translates, and is time-boxed
  so a slow model can never eat your words.
- **Menu-bar app** with language picker, quick-key configuration, add or
  remove languages, and a floating waveform that ripples with your voice.
- **Starts at login** as a LaunchAgent, with a startup splash that shows the
  model loading and fades away when the hotkey is live.
- **Clipboard-safe.** Whatever you had copied is restored after the paste.

## Install

Requires macOS 13+ and Python 3.10+.

```bash
git clone https://github.com/TrenchStrAvy/local-flow.git
cd local-flow
./install.sh
```

Useful flags:

| Flag             | Effect                                                         |
|------------------|----------------------------------------------------------------|
| `--multilingual` | pre-download the multilingual model too (~460 MB)               |
| `--with-cleanup` | install Ollama + gemma3:4b via Homebrew and enable cleanup      |
| `--no-service`   | set up the venv and models only; run `flow.py` yourself         |
| `--model NAME`   | pre-download a different whisper model (`tiny.en`, `medium.en`) |

The installer creates `.venv`, installs dependencies, downloads the speech
model, and installs a LaunchAgent that starts local-flow at login. Your
Homebrew, Python, and Ollama installs are otherwise untouched.

### Permissions (one time)

macOS will not let a program listen to a global hotkey or paste into other
apps without these. Grant them to **Python** (the `.venv/bin/python` the
service runs) in **System Settings → Privacy & Security**:

1. **Microphone**: prompted automatically on the first recording.
2. **Accessibility**: needed to paste and to re-focus the original field.
3. **Input Monitoring**: needed for the global hotkey and quick keys.

Then restart the service:

```bash
launchctl kickstart -k gui/$(id -u)/com.localflow.dictation
```

## Use

Hold **Right-Option** while speaking and release when done. A thin waveform
appears at the bottom of the screen while recording, settles into a calm
ripple with "transcribing…" after release, and the text is pasted where your
cursor was when you pressed the key.

Click the **mic icon** in the menu bar for everything else:

| Menu item           | What it does                                                    |
|---------------------|-----------------------------------------------------------------|
| **Language ▸**      | switch between your enabled languages (shows each quick key)    |
| **Quick keys ▸**    | assign Q / W / E / R / T to any enabled language, or clear them |
| **Add language ▸**  | browse the full catalog (grouped A–Z) and add one to the menu   |
| **Remove language ▸** | take one out of the menu (and off its quick key)              |
| **Quit local-flow** | stop the service until you start it again                       |

Defaults: English, Deutsch, and Français enabled; **Q** = Deutsch,
**W** = Français, **E** = English. All choices persist in
`~/Library/Application Support/LocalFlow/settings.json`.

### Switching language mid-dictation

While holding Right-Option, tap a quick key. The language name appears under
the waveform, the current recording is transcribed in that language, and the
choice stays until you change it again. Switching from English to any other
language swaps the English-only `small.en` model for its multilingual
sibling `small`; it downloads once, then both stay loaded so later switches
are instant.

### Command line

```bash
.venv/bin/python flow.py                     # run in a terminal (no service)
.venv/bin/python flow.py --ollama            # with the cleanup pass
.venv/bin/python flow.py --language de       # start in German for this run
.venv/bin/python flow.py --list-languages    # every supported code
.venv/bin/python flow.py --add-language es   # add Spanish to the menu
.venv/bin/python flow.py --quick-key r=es    # assign R → Spanish
.venv/bin/python flow.py --model medium.en   # more accurate, ~3× slower
.venv/bin/python flow.py --transcribe clip.aiff   # test the pipeline
```

Try the pipeline without touching the mic:

```bash
say -o test.aiff "testing one two three"
.venv/bin/python flow.py --transcribe test.aiff
say -v Anna -o de.aiff "Guten Morgen, das ist ein Test"
.venv/bin/python flow.py --transcribe de.aiff --language de
```

## Models

| Model       | Languages | Speed on CPU | Use when                          |
|-------------|-----------|--------------|-----------------------------------|
| `tiny.en`   | English   | fastest      | latency over everything           |
| `small.en`  | English   | fast         | **default**: best balance         |
| `small`     | all 99    | fast         | auto-selected for non-English     |
| `medium.en` | English   | ~3× slower   | accuracy matters more than speed  |
| `medium`    | all 99    | ~3× slower   | accuracy in other languages       |

Models download on first use to `~/.cache/huggingface`. On an Intel i9,
`small` transcribes a 6-second clip in about 2 seconds; the optional cleanup
adds 1–3 seconds.

## How it stays private

Everything runs as a local process on your Mac: the speech model is a file
on disk, the cleanup model is served by Ollama on `localhost`, and the paste
goes through the system clipboard. There is no telemetry, no update check,
and no external service to sign up for.

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.localflow.dictation
rm ~/Library/LaunchAgents/com.localflow.dictation.plist
rm -rf "~/Library/Application Support/LocalFlow"
rm -rf local-flow   # the cloned folder
```

## Development

```bash
.venv/bin/python -m unittest discover -s tests
```

Layout: `flow.py` (pipeline, hotkey, paste), `focus.py` (remember and
restore the target field), `overlay.py` (waveform), `splash.py` (startup
card), `menubar.py` (status menu), `settings.py` + `languages.py`
(persistent choices and the language catalog). The `launcher/` and
`scripts/` folders hold an optional native Launchpad launcher with signed
bundles and rollback, documented in `scripts/install-launcher.sh`.

macOS only for now: the overlays, hotkey interception, and focus tracking
are Cocoa, Quartz, and Accessibility code. The pipeline itself (mic →
whisper → Ollama → clipboard) is portable; contributions for Linux and
Windows front-ends are welcome.

## License

MIT. Whisper models are MIT-licensed by OpenAI; Gemma is distributed under
Google's Gemma terms.
