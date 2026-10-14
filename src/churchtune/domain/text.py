"""Plain-text helpers shared by name matching and file names."""

import re

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
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def transliterate(text: str) -> str:
    """Latin letters for Cyrillic and accented ones; ``"Відродження"`` gives ``"vidrodzhennya"``."""
    return text.translate(_TRANSLIT)


def slugify(text: str, fallback: str = "venue") -> str:
    """A file-name-safe id: lowercase Latin letters, digits and dashes."""
    slug = _SLUG_RE.sub("-", transliterate(text.strip().lower())).strip("-")
    return slug or fallback
