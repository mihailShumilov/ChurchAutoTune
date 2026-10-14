"""Multilingual dictionary of speaker names on scribble strips (SPEC S4).

Names are lowercased and transliterated (Cyrillic, Spanish and Portuguese accents), so ``Саб``,
``sab`` and ``SUB`` look alike. Scribble strips hold 12 characters, so glued names are split:
letters apart from digits (``Mon1``), and a trailing side letter apart when the rest is a known
word (``MainL``, ``ПорталЛ``).

A stem matches the start of a word; a stem ending in ``$`` must match the whole word. Stems are in
transliterated form and cover English, Russian, Ukrainian, Spanish and Portuguese church usage.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from churchtune.domain.topology import SpeakerRole

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "є": "ye",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
        "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        "á": "a", "à": "a", "â": "a", "ã": "a", "é": "e", "ê": "e", "í": "i", "ó": "o",
        "ô": "o", "õ": "o", "ú": "u", "ü": "u", "ç": "c", "ñ": "n",
    }
)  # fmt: skip
_WORD_RE = re.compile(r"[a-z]+|[0-9]+")
_MIN_GLUED_CHARS = 3

# Groups, subgroups and VCAs: never speakers, checked first ("Submix" is no sub).
_GROUP_STEMS = ("submix", "subgr", "subg$", "grp", "group", "grupo", "gruppa", "vca$", "dca$")
# Words that make a path a stage monitor whatever else the name says ("Bass Mon", "Choir").
_MONITOR_STEMS = (
    "mon$", "monit", "moni$", "wedge", "iem", "inear", "ear$", "ears$", "retorn", "ret$",
    "ponto", "fone", "auricul", "ushi", "pm$", "stage", "stsen", "scen", "choir", "coro$",
)  # fmt: skip
# "Drum fill", "Side fill", "Drum sub": stage monitors in concert practice.
_STAGE_WORDS = frozenset({"drum", "drums", "side", "sidefill", "sidefil"})
# Feeds that are not loudspeakers: effects, streams, recordings, broadcast, translation, loops.
_NON_SPEAKER_STEMS = (
    "fx", "efx", "effect", "efekt", "efect", "rev$", "reverb", "plate$", "echo", "stream",
    "strim", "rec$", "record", "zapis", "live$", "livestr", "broadcast", "trans$", "transl",
    "transm",
    "video", "cam$", "kamer", "online", "youtube", "air$", "radio", "click", "talkback", "tb$",
    "usb", "obs$", "zoom", "web", "cast", "podcast", "feed", "traduc", "tradu", "perevod",
    "pereklad", "cabina", "booth", "hearing", "loop", "als$", "assist", "petl",
)  # fmt: skip
_SUB_STEMS = (
    "sub", "sab", "bass", "bas$", "basy", "lfe", "nizk", "nyzk", "nizy", "niz$", "nch$", "grave",
    "bajo",
)  # fmt: skip
# "Bass" alone on a bus is usually the bass player's monitor; the other sub words are not.
_AMBIGUOUS_SUB_STEMS = frozenset({"bass"})
# Role stems in priority order, after groups and monitors.
_ROLE_STEMS: tuple[tuple[SpeakerRole, tuple[str, ...]], ...] = (
    (SpeakerRole.SUB, _SUB_STEMS),
    (SpeakerRole.ZONE, (
        "lobby", "foyer", "foye", "holl", "narthex", "vestib", "pritvor", "overflow", "kids",
        "nursery", "cry$", "detsk", "dityach", "nino", "infant", "crian", "bercar", "cafe",
        "fellowship", "gym$", "trapezn", "outside", "exterior", "extern", "patio", "dvor",
        "ulits", "vulits", "zone", "zona", "recepc",
    )),
    (SpeakerRole.DELAY, (
        "delay", "dly", "del$", "dl$", "ub$", "retard", "retras", "atras", "traser", "fundo",
        "rear", "back$", "zaderzh", "mezz", "loft", "anfiteat", "balc", "balk", "gallery",
        "galer", "hory", "hori", "zadn",
    )),
    (SpeakerRole.FILL, (
        "fill", "frontfil", "lipfil", "infil", "outfil", "ff$", "lateral", "relleno", "bokov",
        "apron", "lip$", "near",
    )),
    (SpeakerRole.TOP_MONO, (
        "main", "top", "pa$", "lr$", "portal", "front", "zal$", "zala", "osnov", "kolon",
        "mono$", "center", "centre", "centr", "tsentr", "cluster", "cnt$", "ctr$", "sala",
        "altavo", "bafle", "cornet", "caixas$", "igrej", "iglesi", "templo", "nave$", "sanctuar",
        "santuar", "auditor", "platea", "plateia", "hall$", "hram", "tserkv", "foh$", "princip",
    )),
)  # fmt: skip

_LEFT_WORDS = frozenset({
    "l", "lt", "lft", "left", "lev", "levyy", "levyi", "levy", "leviy", "liv", "livyy", "liviy",
    "liva", "sleva", "zliva", "izq", "izqda", "izquierdo", "izquierda", "esq", "esquerdo",
    "esquerda",
})  # fmt: skip
_RIGHT_WORDS = frozenset({
    "r", "rt", "rgt", "right", "prav", "pravyy", "pravyi", "pravy", "praviy", "prava",
    "sprava", "der", "dcha", "derecho", "derecha", "dir", "direito", "direita",
})  # fmt: skip
_LEFT_PREFIXES = ("izq", "esq")
_RIGHT_PREFIXES = ("prav", "derech", "direit")
# A bare "П" (правий / правый) is a side only next to a word for the main speakers.
_PORTAL_STEMS = ("portal", "zal$", "zala", "kolon", "osnov")
_SIDE_LETTERS = frozenset({"l", "r"})


class Side(StrEnum):
    """Side of a stereo speaker."""

    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class NameHint:
    """What a name says about a path.

    Attributes:
        role: Suggested role, or None.
        ambiguous: True when the only clue is a word with two common meanings ("Bass").
        non_speaker: True for effects sends, groups, streams and recordings.
    """

    role: SpeakerRole | None
    ambiguous: bool = False
    non_speaker: bool = False


def normalize(name: str) -> str:
    """Lowercase and transliterate a name: ``"Саб буфер"`` gives ``"sab bufer"``."""
    return name.strip().lower().translate(_TRANSLIT)


def _matches(word: str, stem: str) -> bool:
    return word == stem[:-1] if stem.endswith("$") else word.startswith(stem)


def _any(parts: list[str], stems: tuple[str, ...]) -> bool:
    return any(_matches(word, stem) for word in parts for stem in stems)


# Base words that take a glued side letter ("MainL", "SubR", "ПорталЛ"). Exact words only: a stem
# would also split "Central", "Monitor", "Subwoofer" or "Infill".
_GLUE_BASES = frozenset({
    "main", "mains", "top", "tops", "sub", "subs", "sab", "saby", "pa", "portal", "zal", "mon",
    "mtx", "delay", "dly", "fill", "ff", "balkon", "balcon", "front", "kolon",
})  # fmt: skip


def words(name: str) -> list[str]:
    """Words of a name with glued forms split: ``"MainL"`` gives ``["main", "l"]``."""
    found: list[str] = []
    for word in _WORD_RE.findall(normalize(name)):
        glued = (
            len(word) >= _MIN_GLUED_CHARS and word[-1] in _SIDE_LETTERS and word[:-1] in _GLUE_BASES
        )
        found += [word[:-1], word[-1]] if glued else [word]
    return found


def _role_from_stems(parts: list[str]) -> NameHint | None:
    for role, stems in _ROLE_STEMS:
        if not _any(parts, stems):
            continue
        if role in (SpeakerRole.FILL, SpeakerRole.SUB) and _STAGE_WORDS & set(parts):
            return NameHint(SpeakerRole.MONITOR)
        if role is SpeakerRole.SUB:
            specific = tuple(s for s in _SUB_STEMS if s not in _AMBIGUOUS_SUB_STEMS)
            return NameHint(role, ambiguous=not _any(parts, specific))
        return NameHint(role)
    return None


def name_hint(name: str) -> NameHint:
    """Everything a name says: role, ambiguity, not-a-speaker."""
    parts = words(name)
    if _any(parts, _GROUP_STEMS):
        return NameHint(None, non_speaker=True)
    if _any(parts, _MONITOR_STEMS):
        return NameHint(SpeakerRole.MONITOR)
    found = _role_from_stems(parts)
    if found is not None:
        return found
    return NameHint(None, non_speaker=_any(parts, _NON_SPEAKER_STEMS))


def role_hint(name: str) -> SpeakerRole | None:
    """Speaker role suggested by a name, or None when the name says nothing."""
    return name_hint(name).role


def is_non_speaker(name: str) -> bool:
    """True when a name says the path feeds effects, a group, a stream or a recording."""
    return name_hint(name).non_speaker


def side_hint(name: str) -> Side | None:
    """Stereo side suggested by a name (``Main L``, ``Портал правий``, ``Izquierdo``), or None.

    Both sides at once (``L/R``, ``LR``) mean a stereo path without a side.
    """
    parts = words(name)
    left = any(w in _LEFT_WORDS or w.startswith(_LEFT_PREFIXES) for w in parts)
    right = any(w in _RIGHT_WORDS or w.startswith(_RIGHT_PREFIXES) for w in parts)
    if not right and "p" in parts and _any(parts, _PORTAL_STEMS):
        right = True
    if left == right:
        return None
    return Side.LEFT if left else Side.RIGHT
