"""Spell checking for the input line: offline, word-list based (pyspellchecker), with a personal dictionary."""

import re

from .config import CONFIG_DIR

WORDS_FILE = CONFIG_DIR / "words.txt"
LANGUAGES = ("en", "es", "fr", "pt", "de", "it", "ru", "ar", "lv", "eu", "nl", "fa")

# Words the dictionaries don't know but mesh chat is full of.
BUILTIN = """
meshcore meshtastic lora lorawan repeater repeaters advert adverts telemetry airtime colinear collinear dipole yagi
antenna antennas dbi dbm snr rssi rx tx sf cr bw khz mhz ghz ism heltec rak xiao seeed tdeck t-deck lilygo wio
nrf esp esp32 ble bluetooth wifi usb uart gps firmware reflash flasher companion companions multihop hop hops
flood floods flooded flooding noise ack acks nack mqtt api cli ota pcb sma ipex ufl uhf vhf ham callsign qth qsl
config configs repo repos github emoji emojis lol lmao rofl omg brb afk btw imo imho tbh idk ikr fyi np ty thx pls
plz gonna wanna gotta kinda sorta dunno yeah yep yup nah nope ok okay heya hiya arvo servo brekkie reckon mate
ta cheers howdy selfie online offline internet app apps txt msg msgs dm dms ping pong traceroute
"""


def tokens(text: str):
    """(start, end, word) for each word worth checking: letters and apostrophes, 2+ letters."""
    skip = []
    for m in re.finditer(r"https?://\S+|www\.\S+|@\[[^\]]*\]|:[a-z0-9_+\-]+:|\S*\d\S*|\S+@\S+|/\S+", text):
        skip.append((m.start(), m.end()))
    for m in re.finditer(r"[^\W\d_]+(?:'[^\W\d_]+)*", text):
        if any(a <= m.start() < b for a, b in skip):
            continue
        word = m.group(0)
        if len(word) < 2 or (word.isupper() and len(word) <= 5):  # acronyms: SNR, RSSI, OK
            continue
        yield m.start(), m.end(), word


class Speller:
    def __init__(self, language: str = "en"):
        from spellchecker import SpellChecker

        self.language = language if language in LANGUAGES else "en"
        self.checker = SpellChecker(language=self.language, distance=2)
        self.checker.word_frequency.load_words(BUILTIN.split())
        self.personal: set[str] = set()
        if WORDS_FILE.exists():
            self.personal = {w.strip().lower() for w in WORDS_FILE.read_text(encoding="utf-8").splitlines() if w.strip()}
            self.checker.word_frequency.load_words(self.personal)
        self.extra: set[str] = set()  # names from the mesh, refreshed by the app

    def set_names(self, names) -> None:
        words = set()
        for n in names:
            words.update(w.lower() for _, _, w in tokens(n))
        self.extra = words

    def known(self, word: str) -> bool:
        w = word.lower().strip("'")
        if w in self.extra or w in self.personal:
            return True
        if w.endswith("'s") and (w[:-2] in self.extra or not self.checker.unknown([w[:-2]])):
            return True
        return not self.checker.unknown([w])

    def misspelled(self, text: str, cursor: int | None = None) -> list[tuple[int, int, str]]:
        """Misspelled words, except one still being typed (the cursor right at its end)."""
        out = []
        for start, end, word in tokens(text):
            if cursor is not None and end == cursor and end == len(text):
                continue
            if not self.known(word):
                out.append((start, end, word))
        return out

    def suggestions(self, word: str, limit: int = 5) -> list[str]:
        w = word.lower()
        if len(w) > 12:  # distance-2 search gets slow on long words; one edit is plenty there
            self.checker.distance = 1
        try:
            cands = self.checker.candidates(w) or set()
        finally:
            self.checker.distance = 2
        ranked = sorted(cands, key=lambda c: -self.checker.word_frequency[c])[:limit]
        if word[:1].isupper():
            ranked = [c[:1].upper() + c[1:] for c in ranked]
        return [c for c in ranked if c.lower() != w]

    def learn(self, word: str) -> None:
        w = word.lower()
        self.personal.add(w)
        self.checker.word_frequency.load_words([w])
        WORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        WORDS_FILE.write_text("\n".join(sorted(self.personal)) + "\n", encoding="utf-8")

    def forget(self, word: str) -> bool:
        w = word.lower()
        if w not in self.personal:
            return False
        self.personal.discard(w)
        WORDS_FILE.write_text("\n".join(sorted(self.personal)) + ("\n" if self.personal else ""), encoding="utf-8")
        self.checker = type(self.checker)(language=self.language, distance=2)
        self.checker.word_frequency.load_words(BUILTIN.split())
        self.checker.word_frequency.load_words(self.personal)
        return True
