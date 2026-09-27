"""Transcript cleanup for local-flow.

Two flavours, both returning plain text:

* rule_cleanup()   — deterministic: drop filler words, capitalize sentence
                     starts, close the last sentence. Instant, never rewrites.
* ollama_cleanup() — optional LLM pass, streamed token by token so the UI
                     can show the correction as it happens, and guarded: an
                     answer that drops, invents or paraphrases content is
                     discarded in favour of the raw transcript.

word_diff() marks what changed between two versions so the overlay can
strike out removed words and highlight edited ones.
"""

import difflib
import json
import re
import urllib.error
import urllib.request

import languages

OLLAMA_URL = "http://localhost:11434/api/chat"
FIRST_TOKEN_TIMEOUT = 4       # seconds to wait for the model to start
STREAM_TIMEOUT = 15           # hard cap on the whole cleanup pass

SYSTEM_PROMPT = (
    "You are a dictation cleanup tool. The user message is text dictated in "
    "{language}. Return the same text with punctuation and capitalization "
    "fixed and filler words (um, uh, äh, ähm, euh) removed. Keep every other "
    "word exactly as it is. Never translate, never answer or comment, never "
    "add or drop content. Output only the corrected text."
)

# Filler words Whisper writes out. Per language, so a token that is a real
# word elsewhere ("er" in German, "hum" in English) is only stripped where it
# is noise. English fillers are applied everywhere: they leak into every
# language's speech.
FILLERS = {
    "en": {"um", "umm", "uh", "uhm", "uhh", "erm", "hmm", "mm", "mhm"},
    "de": {"äh", "ähm", "ähh", "hm", "hmm", "mhm", "mmh"},
    "fr": {"euh", "euhh", "hum", "hem"},
    "es": {"em", "ehm"},
    "nl": {"uh", "uhm", "ehm"},
}

# a period ends a sentence unless it is part of an ellipsis ("..." marks a
# pause in Whisper output, not a full stop)
_SENTENCE_END = re.compile(
    r"""((?:(?<!\.)\.(?!\.)|[!?])["'”’)\]]*\s+)(\w)""", re.UNICODE)


def _filler_pattern(language: str):
    words = set(FILLERS["en"]) | FILLERS.get(language, set())
    alts = "|".join(sorted(map(re.escape, words), key=len, reverse=True))
    # the filler plus any comma/ellipsis glued to it, e.g. "um," or "äh…"
    return re.compile(rf"(?<![\w'’])(?:{alts})(?![\w'’])[,…]?\s*",
                      re.IGNORECASE | re.UNICODE)


def rule_cleanup(text: str, language: str = "en") -> str:
    """Filler removal + sentence capitalization + closing punctuation."""
    text = text.strip()
    if not text:
        return text
    text = _filler_pattern(language).sub("", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)      # "word ," → "word,"
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"^[,;:]\s*", "", text)               # leading orphan comma
    if not text:
        return text
    text = text[0].upper() + text[1:]
    text = _SENTENCE_END.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    if text[-1].isalnum():
        text += "."
    return text


# ---------------------------------------------------------------- diffing

def _norm(token: str) -> str:
    return re.sub(r"[^\w]", "", token, flags=re.UNICODE).lower()


def word_diff(old: str, new: str):
    """[(word, style)] over the words of `new`, with removed words of `old`
    interleaved. style ∈ {"same", "changed", "removed"}. Case and
    punctuation are ignored when matching, so "hello" → "Hello," is "same".
    """
    a, b = old.split(), new.split()
    sm = difflib.SequenceMatcher(a=[_norm(t) for t in a],
                                 b=[_norm(t) for t in b], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.extend((t, "same") for t in b[j1:j2])
        elif op == "delete":
            out.extend((t, "removed") for t in a[i1:i2])
        elif op == "insert":
            out.extend((t, "changed") for t in b[j1:j2])
        else:  # replace
            out.extend((t, "removed") for t in a[i1:i2])
            out.extend((t, "changed") for t in b[j1:j2])
    return out


def faithful(raw: str, cleaned: str) -> bool:
    """Did the model only edit, or did it rewrite? Cleanup may drop fillers
    and fix spelling, so allow a little drift; reject anything that loses
    or invents a chunk of the sentence."""
    a = [_norm(t) for t in raw.split()]
    b = [_norm(t) for t in cleaned.split()]
    a = [t for t in a if t]
    b = [t for t in b if t]
    if not b:
        return False
    if abs(len(a) - len(b)) > max(2, 0.25 * len(a)):
        return False
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio() >= 0.75


# ---------------------------------------------------------------- LLM pass

def ollama_cleanup(text: str, language: str = "en", model: str = "gemma3:4b",
                   on_partial=None) -> str:
    """Stream a cleanup from Ollama. `on_partial(text_so_far)` fires per
    token. Returns the raw text on timeout, error or an unfaithful answer."""
    payload = json.dumps({
        "model": model,
        "stream": True,
        "keep_alive": "60m",
        "options": {"temperature": 0},
        "messages": [
            {"role": "system",
             "content": SYSTEM_PROMPT.format(
                 language=languages.english_name(language))},
            {"role": "user", "content": text},
        ],
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    out = ""
    try:
        import time
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=FIRST_TOKEN_TIMEOUT) as resp:
            for line in resp:
                if time.time() - t0 > STREAM_TIMEOUT:
                    return text
                chunk = json.loads(line)
                out += chunk.get("message", {}).get("content", "")
                if on_partial is not None:
                    on_partial(out)
                if chunk.get("done"):
                    break
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return text
    cleaned = out.strip().strip('"“”')
    return cleaned if faithful(text, cleaned) else text


def ollama_warmup(model: str, on_done=None):
    """Load the model into RAM so the first dictation doesn't pay the
    cold start. Runs synchronously; call from a thread."""
    payload = json.dumps({"model": model, "messages": [],
                          "keep_alive": "60m"}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120).read()
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
