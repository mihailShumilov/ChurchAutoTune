"""Translations: Fluent catalogues in ``locales/<lang>/*.ftl`` (SPEC §11, skill wizard-ux).

English is the source language; a key missing in another language falls back to English, and a
key missing everywhere renders as the key itself so a gap is visible, never a crash. Numbers in
arguments are formatted for the locale (decimal comma in ru/uk).
"""

import os
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from fluent.runtime import FluentBundle, FluentResource

LANGUAGES = ("en", "ru", "uk")
SOURCE_LANGUAGE = "en"
LANG_VAR = "CTUNE_LANG"
Args = Mapping[str, str | int | float]


LOCALES = Path(__file__).resolve().parent / "locales"


def locale_dir(lang: str) -> Path:
    """Directory of one language's ``.ftl`` files."""
    return LOCALES / lang


def catalogue_files(lang: str) -> list[Path]:
    """The ``.ftl`` files of a language, sorted."""
    return sorted(locale_dir(lang).glob("*.ftl"))


@cache
def _bundle(lang: str) -> FluentBundle:
    bundle = FluentBundle([lang], use_isolating=False)
    for path in catalogue_files(lang):
        bundle.add_resource(FluentResource(path.read_text(encoding="utf-8")))
    return bundle


def detect_language(environ: Mapping[str, str] | None = None) -> str:
    """Language from ``CTUNE_LANG``, then ``LC_ALL``, ``LC_MESSAGES``, ``LANG``; else English."""
    env = os.environ if environ is None else environ
    for var in (LANG_VAR, "LC_ALL", "LC_MESSAGES", "LANG"):
        code = env.get(var, "").split(".")[0].split("_")[0].lower()
        if code in LANGUAGES:
            return code
    return SOURCE_LANGUAGE


class Translator:
    """Renders message keys in one language."""

    def __init__(self, lang: str = SOURCE_LANGUAGE) -> None:
        """Pick a language.

        Raises:
            ValueError: The language has no catalogue.
        """
        if lang not in LANGUAGES:
            raise ValueError(f"unknown language {lang!r}; available: {', '.join(LANGUAGES)}")
        self.lang = lang

    def has(self, key: str) -> bool:
        """True when the key exists in this language or in English."""
        return _bundle(self.lang).has_message(key) or _bundle(SOURCE_LANGUAGE).has_message(key)

    def __call__(self, key: str, args: Args | None = None) -> str:
        """The text of ``key`` with ``args`` filled in."""
        for lang in (self.lang, SOURCE_LANGUAGE):
            bundle = _bundle(lang)
            if bundle.has_message(key):
                message = bundle.get_message(key)
                if message.value is None:
                    break
                text, _errors = bundle.format_pattern(message.value, dict(args or {}))
                return str(text)
        return key
