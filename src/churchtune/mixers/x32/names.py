"""Multilingual dictionary of speaker names on scribble strips (SPEC S4).

Names are lowercased and Cyrillic is transliterated, so ``Саб``, ``sab`` and ``SUB`` look alike.
A name gives a role hint and, for stereo pairs, a side.
"""

import re
from enum import StrEnum

from churchtune.domain.topology import SpeakerRole

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "є": "ye",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
        "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)  # fmt: skip

# Whole words that settle the role on their own.
_ROLE_WORDS: dict[str, SpeakerRole] = {
    "mono": SpeakerRole.TOP_MONO,
    "center": SpeakerRole.TOP_MONO,
    "centre": SpeakerRole.TOP_MONO,
    "tsentr": SpeakerRole.TOP_MONO,
    "pa": SpeakerRole.TOP_MONO,
    "lr": SpeakerRole.TOP_MONO,
}
# Stems, matched at the start of a word of the transliterated name. Order matters: the first role
# whose stem matches wins, so "sub" beats "main" in "main sub" and "frontfill" is a fill. A stem
# ending in "$" must match the whole word.
_ROLE_STEMS: tuple[tuple[SpeakerRole, tuple[str, ...]], ...] = (
    (SpeakerRole.SUB, ("sub", "sab", "bass", "lfe", "nizk", "nyzk")),
    (SpeakerRole.MONITOR, ("mon$", "monit", "wedge", "iem", "inear", "stage", "stsen", "scen")),
    (SpeakerRole.DELAY, ("delay", "balc", "balk", "gallery", "galer", "hory", "hori", "zadn")),
    (SpeakerRole.FILL, ("fill", "frontfil", "lipfil", "near")),
    (SpeakerRole.TOP_MONO, ("main", "top", "portal", "front", "zal", "osnov", "kolon")),
)
# Names of buses and matrices that carry something other than speakers: effects sends, streams,
# recordings, broadcast and translation feeds.
_NON_SPEAKER_STEMS = (
    "fx", "efx", "effect", "efekt", "efect", "rev", "reverb", "plate", "echo", "stream",
    "strim", "rec", "zapis", "live", "broadcast", "trans", "video", "cam", "kamer", "online",
    "youtube", "air", "radio",
)  # fmt: skip
_WORD_RE = re.compile(r"[a-z0-9]+")


class Side(StrEnum):
    """Side of a stereo speaker."""

    LEFT = "left"
    RIGHT = "right"


_LEFT = ("l", "left", "lev", "liv", "izq", "lft")
_RIGHT = ("r", "right", "prav", "der", "rgt")


def normalize(name: str) -> str:
    """Lowercase and transliterate a name: ``"Саб буфер"`` gives ``"sab bufer"``."""
    return name.strip().lower().translate(_TRANSLIT)


def _words(name: str) -> list[str]:
    return _WORD_RE.findall(normalize(name))


def role_hint(name: str) -> SpeakerRole | None:
    """Speaker role suggested by a name, or None when the name says nothing."""
    words = _words(name)
    for role, stems in _ROLE_STEMS:
        if role is SpeakerRole.TOP_MONO:
            # Whole words such as "pa" or "mono" are tried before the generic top stems.
            for word in words:
                if word in _ROLE_WORDS:
                    return _ROLE_WORDS[word]
        if any(_matches(word, stem) for word in words for stem in stems):
            return role
    return None


def _matches(word: str, stem: str) -> bool:
    return word == stem[:-1] if stem.endswith("$") else word.startswith(stem)


def side_hint(name: str) -> Side | None:
    """Stereo side suggested by a name (``Main L``, ``портал правый``), or None."""
    words = _words(name)
    if any(w in _LEFT or (len(w) > 1 and w.startswith(_LEFT[1:])) for w in words):
        return Side.LEFT
    if any(w in _RIGHT or (len(w) > 1 and w.startswith(_RIGHT[1:])) for w in words):
        return Side.RIGHT
    return None


def is_non_speaker(name: str) -> bool:
    """True when a name says the path feeds effects, a stream or a recording, not speakers."""
    words = _words(name)
    return any(word.startswith(stem) for word in words for stem in _NON_SPEAKER_STEMS)
