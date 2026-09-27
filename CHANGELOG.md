# Changelog

## v0.2.0 — 2026-09-27: faster dictation

The time from releasing the key to text appearing in your document drops
from roughly 5–8 seconds to roughly 2–2.5 seconds on an Intel i9. Nothing
about privacy changed: everything still runs on your Mac.

### What was slow

Per-dictation timings from real use showed where the time went:

| Stage                          | Before          |
|--------------------------------|-----------------|
| faster-whisper (speech-to-text)| 1.3–2 s         |
| Ollama cleanup (gemma3:4b)     | 2.5–8 s         |

The optional cleanup pass cost more than the transcription itself, and
about a third of dictations hit its 8-second timeout, so you waited the
full 8 seconds and then got the raw transcript anyway.

### What changed

- **Cleanup model: gemma3:4b → gemma3:1b.** The 1B model fixes
  punctuation, capitalization and filler words just as well for this job
  and runs in about 1.3 s on CPU instead of 2.5–8 s. It is also a
  ~800 MB download instead of ~3 GB.
- **Cleanup timeout: 8 s → 4 s.** With the smaller model the ceiling on a
  bad day is half what it was.
- **Whisper uses all physical cores.** CTranslate2 defaulted to 4 threads;
  local-flow now passes the machine's physical core count.
- **Leaner decoding.** Segments no longer condition on previous text and
  silence trimming is slightly more aggressive. Together with the thread
  change this shaves roughly 10 % off the speech-to-text pass.

### Upgrading

```bash
cd local-flow
git pull
./install.sh --with-cleanup   # re-running is safe; pulls gemma3:1b
```

If you skip the installer, pull the new model yourself, otherwise the
cleanup pass silently falls back to raw transcripts:

```bash
ollama pull gemma3:1b
ollama rm gemma3:4b            # optional: reclaim 3 GB
```

Then restart the service:

```bash
launchctl kickstart -k gui/$(id -u)/com.localflow.dictation
```

### Known limits

Whisper `small.en` has a fixed cost of about 1.3 s per dictation on Intel,
regardless of clip length. `base.en` runs in about 0.5 s but is noticeably
less accurate, especially outside English. A live word-by-word preview
while you speak is under consideration for a future release.

## v0.1.0 — 2026-09-16: initial release

Offline multilingual dictation for macOS: hold Right-Option, speak,
release. faster-whisper on CPU, optional local Ollama cleanup, 99
languages with fast keys to switch mid-sentence, focus restore, menu bar
icon, floating waveform, login service and Launchpad launcher.
