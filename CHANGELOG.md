# Changelog

## v0.4.0 — 2026-09-29: hands-free lock

Thank you for the feedback that shaped this release. Every fix below came
from a real dictation someone reported.

### What changed

- **Option+A locks dictation on.** Hold Right-Option, tap A, let go; tap
  Option+A again to finish. Speech is split into sentences on ~0.7 s pauses;
  each is cleaned up and committed, and committed text is never corrected
  again, so anything you type yourself stays intact.
- **Focus-aware delivery.** Switch apps while locked and dictation keeps
  listening; the words are typed the moment the original field has focus
  again, or at the end.
- **A fast model for the preview, the accurate one for the text.** The
  live preview now runs `base.en` (about 0.4 s per update, so words appear
  every half second); every committed or final sentence is decoded by
  `small.en`. Preview corrections are word-based: a comma the preview
  chose differently is left alone rather than retyping the line.
- **No more decoder stalls or loops.** Whisper's temperature-fallback
  ladder (up to six re-decodes on a chunk cut mid-word, 7 s stalls) is
  off; repeated phrases ("the the the") are cut at the first repeat; the
  preview decoder cannot repeat a trigram; an empty preview never deletes
  anything.
- **The app ignores its own keystrokes.** Typed words are tagged so the
  hotkey listener no longer mistakes them for Option+A or a language key.
- **No double paste.** Each dictation owns its typing state and its
  preview loop, and all keystrokes go through one lock, so a quick second
  press while a long dictation is still finalizing can no longer make it
  type the whole section again or interleave stale preview text.
- **Long dictations split at a pause.** Past the 24 s window the buffer is
  cut at the last short pause instead of mid-word, so no sentence gets a
  spurious full stop in the middle.

### Upgrading

```bash
cd local-flow && git pull && ./install.sh
launchctl kickstart -k gui/$(id -u)/com.localflow.dictation
```

The preview model (`base.en`, about 75 MB) downloads on first use.


## v0.3.0 — 2026-09-27: words as you speak

Thank you to everyone using local-flow and sending feedback. This release
comes directly from it.

### What changed

- **Live dictation.** While you hold Right-Option, recognized words are
  typed into your text field about 1.5 s behind your voice and corrected
  in place as more audio arrives. No more waiting for release to see
  anything. `--preview card` shows them in the overlay instead; `--preview
  off` restores the previous wait-then-paste behaviour.
- **Cleanup is now rule-based by default.** Filler words are removed and
  capitalization and end punctuation fixed, instantly and deterministically,
  right in your text. The v0.2.0 default (gemma3:1b) could echo its own
  instructions, so a dictation sometimes came out as "Keep it in English."
  It also occasionally rewrote sentences. Both are gone.
- **LLM cleanup is opt-in and guarded.** `--ollama` uses gemma3:4b again,
  streams its edits into your text as they are produced, and any answer that
  drops or invents content is discarded in favour of the transcript.
  `--ollama-model NAME` picks a different model.
- **Ellipses are pauses.** "keep the same... aesthetics" no longer gets a
  capital after the dots.

### Upgrading

```bash
cd local-flow && git pull && ./install.sh
```

If your service was installed with `--with-cleanup`, re-run with that flag
to keep the LLM pass (it now needs gemma3:4b), or without it to use the
rule-based cleanup. Then restart:

```bash
launchctl kickstart -k gui/$(id -u)/com.localflow.dictation
```

### Known limits

Live typing sends keystrokes to the focused field, so it pauses if you
click elsewhere while speaking and the final text goes back to the original
field on release. A hands-free locked mode is next.


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
