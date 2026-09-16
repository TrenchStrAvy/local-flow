# local-flow

**Free, private, multilingual dictation for macOS.** Hold a key, speak,
release. Your words appear in the text field you were typing in.

Everything runs on your Mac. No account, no API key, no credits, no cloud:
nothing you say ever leaves the machine.

```
Hold Right-Option → mic capture
    → faster-whisper (speech-to-text on your CPU, 99 languages)
    → Ollama gemma3:4b (optional local cleanup: punctuation, filler words)
    → pasted into the text field you started in
```

## Key features

- **100% offline.** Speech-to-text runs locally with
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper). The optional
  cleanup pass runs on a local Ollama model. There is no server, no
  telemetry, and nothing to pay for.
- **99 languages.** English, Deutsch, Français, Español, 日本語, العربية and
  every other language Whisper knows. One multilingual model covers them
  all, so adding a language is a menu click, not a download.
- **Fast keys: switch language without stopping.** While holding
  Right-Option, tap **Q** for German, **W** for French, **E** for English
  (defaults). Assign Q, W, E, R, and T to any languages you like from the
  menu bar. The letter never reaches your document; the current sentence
  is transcribed in the new language.
- **Text lands where you started.** If you click somewhere else while
  speaking or while transcription is running, local-flow brings the
  original app and text field back into focus before pasting.
- **Optional local cleanup.** Fixes punctuation and capitalization and
  removes "um", "äh", "euh". It never translates, and it is time-boxed so
  a slow model can never eat your words.
- **Works on Intel Macs.** Uses the int8 CPU path, not Apple-Silicon-only
  frameworks. A 6-second dictation lands in about 2 seconds on an i9.
- **Native feel.** A mic icon in the menu bar, a floating waveform that
  ripples with your voice, a startup splash while the model loads, a
  LocalFlow icon in Launchpad, and a service that starts at login.
- **Clipboard-safe.** Whatever you had copied is restored after the paste.

## Install

macOS 13 or later, Python 3.10 or later. One command after cloning:

```bash
git clone https://github.com/TrenchStrAvy/local-flow.git
cd local-flow
./install.sh
```

The installer creates a virtual environment, installs dependencies,
downloads the speech model, builds a named **LocalFlow** runtime app (so
macOS privacy settings show "LocalFlow" instead of "Python"), places a
**LocalFlow** launcher in `/Applications` and Launchpad, and installs a
login service. Re-running it is safe.

| Flag             | Effect                                                            |
|------------------|-------------------------------------------------------------------|
| `--with-cleanup` | install Ollama and gemma3:4b via Homebrew and enable the cleanup pass |
| `--multilingual` | pre-download the multilingual model now (~460 MB) instead of on first switch |
| `--model NAME`   | pre-download a different Whisper model (`tiny.en`, `medium.en`)    |
| `--no-service`   | set up the environment only; run `flow.py` from a terminal yourself |

### Permissions (once)

macOS will not let any program listen for a global hotkey or paste into
other apps without these. In **System Settings → Privacy & Security**,
grant them to **LocalFlow**:

1. **Microphone**: macOS asks automatically on the first recording.
2. **Accessibility**: needed to paste and to re-focus the original field.
3. **Input Monitoring**: needed for the hotkey and the fast keys.

Then click **LocalFlow** in Launchpad (or restart the service, below).

## Use

Hold **Right-Option** while speaking and release when done. A thin waveform
appears at the bottom of the screen while recording, settles into a calm
ripple with "transcribing…" after release, and the text is pasted where your
cursor was when you pressed the key.

Click the **mic icon** in the menu bar for everything else:

| Menu item             | What it does                                                    |
|-----------------------|-----------------------------------------------------------------|
| **Language ▸**        | switch between your enabled languages; each shows its fast key   |
| **Quick keys ▸**      | assign Q / W / E / R / T to any enabled language, or clear them |
| **Add language ▸**    | browse the full catalog (grouped A–Z) and add one to the menu   |
| **Remove language ▸** | take one out of the menu and off its fast key                   |
| **Quit local-flow**   | stop the service until you start it again from Launchpad        |

Defaults: English, Deutsch, and Français enabled; **Q** = Deutsch,
**W** = Français, **E** = English. Choices persist in
`~/Library/Application Support/LocalFlow/settings.json`.

### Switching language mid-dictation

While holding Right-Option, tap a fast key. The language name appears under
the waveform, the recording in progress is transcribed in that language,
and the choice stays until you change it. The first switch away from
English swaps the English-only `small.en` model for the multilingual
`small` (downloaded once, ~460 MB); both stay loaded afterwards, so later
switches are instant.

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

Restart the service by hand:

```bash
launchctl kickstart -k gui/$(id -u)/com.localflow.dictation
```

Logs go to `~/Library/Logs/local-flow.log`.

## Models

| Model       | Languages | Speed on CPU | Use when                          |
|-------------|-----------|--------------|-----------------------------------|
| `tiny.en`   | English   | fastest      | latency over everything           |
| `small.en`  | English   | fast         | **default**: best balance         |
| `small`     | all 99    | fast         | auto-selected for non-English     |
| `medium.en` | English   | ~3× slower   | accuracy matters more than speed  |
| `medium`    | all 99    | ~3× slower   | accuracy in other languages       |

Models download on first use to `~/.cache/huggingface`.

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.localflow.dictation
rm ~/Library/LaunchAgents/com.localflow.dictation.plist
rm -rf /Applications/LocalFlow.app "$HOME/Library/Application Support/LocalFlow"
rm -rf local-flow   # the cloned folder
```

## Development

```bash
.venv/bin/python -m unittest discover -s tests
```

Layout: `flow.py` (pipeline, hotkey, paste), `focus.py` (remember and
restore the target field), `overlay.py` (waveform), `splash.py` (startup
card), `menubar.py` (status menu), `settings.py` and `languages.py`
(persistent choices and the language catalog), `install.sh` (installer),
`launcher/` and `scripts/` (the Launchpad launcher, plus an advanced
installer with fingerprinting and rollback for updating an existing setup).

local-flow is macOS-only by design: the overlays, hotkey interception, and
focus tracking are Cocoa, Quartz, and Accessibility code.

## License

MIT. Whisper models are MIT-licensed by OpenAI; Gemma is distributed under
Google's Gemma terms.
