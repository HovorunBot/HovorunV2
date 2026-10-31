"""Language data definitions dynamically populated from pycountry."""

from dataclasses import dataclass
from typing import Final

import pycountry

ISO_CODE_LEN: Final = 2
REGIONAL_INDICATOR_OFFSET: Final = 127397


@dataclass(frozen=True)
class Language:
    """Information about a supported language."""

    code: str
    name: str
    flag: str
    bot_translation_available: bool = False

    @property
    def display_name(self) -> str:
        """Return the display name with its flag."""
        return f"{self.flag} {self.name}"


def _get_flag_emoji(country_code: str) -> str:
    """Return the flag emoji for a country code, falling back to a globe.

    Args:
        country_code: Two-letter country code.

    Returns:
        The regional-indicator flag emoji, or a globe for invalid codes.
    """
    if len(country_code) != ISO_CODE_LEN:
        return "🌐"

    # Regional indicator symbols for A-Z
    code = country_code.upper()
    if not all("A" <= c <= "Z" for c in code):
        return "🌐"

    return "".join(chr(REGIONAL_INDICATOR_OFFSET + ord(c)) for c in code)


# Common mappings for flag generation (language code to country code)
_LANGUAGE_COUNTRIES: Final = {
    "en": "US",
    "uk": "UA",
    "ja": "JP",
    "zh": "CN",
    "ko": "KR",
    "el": "GR",
    "hi": "IN",
    "he": "IL",
    "ar": "SA",
    "vi": "VN",
    "cs": "CZ",
    "da": "DK",
    "sv": "SE",
    "fa": "IR",
    "sr": "RS",
    "sq": "AL",
    "sw": "KE",
    "de": "DE",
    "fr": "FR",
    "it": "IT",
    "es": "ES",
    "pl": "PL",
    "pt": "PT",
    "nl": "NL",
    "tr": "TR",
    "nb": "NO",
    "nn": "NO",
    "no": "NO",
    "hy": "AM",  # Armenian
    "ka": "GE",  # Georgian
    "kk": "KZ",  # Kazakh
    "uz": "UZ",  # Uzbek
    "az": "AZ",  # Azerbaijani
    "be": "BY",  # Belarusian
}

# Languages that definitely don't have a specific country flag
_NON_COUNTRY_LANGUAGES: Final = frozenset({"eo", "la", "sa", "ia", "ie", "vo"})


def _generate_languages() -> dict[str, Language]:
    """Return the language catalog with provider variants and canonical aliases.

    Returns:
        Mapping of language code to language definition.
    """
    languages: dict[str, Language] = {}

    # ISO 639-1 (2-letter) codes
    for lang in pycountry.languages:
        code = getattr(lang, "alpha_2", None)
        if not code:
            continue

        name = getattr(lang, "name", "Unknown")

        flag = _language_flag(code)

        languages[code] = Language(code=code, name=name, flag=flag, bot_translation_available=(code == "en"))

    _add_google_variants(languages)
    _add_language_aliases(languages)

    return languages


def _language_flag(code: str) -> str:
    """Return the flag symbol for a language code, including policy overrides.

    Args:
        code: Language code to render.

    Returns:
        The flag emoji or policy symbol.
    """
    # Security/Policy: Russia danger symbol
    if code == "ru":
        return "🩸"
    if code in _NON_COUNTRY_LANGUAGES:
        return "🌐"
    return _get_flag_emoji(_LANGUAGE_COUNTRIES.get(code, code.upper()))


def _add_google_variants(languages: dict[str, Language]) -> None:
    """Add Google Translate Chinese variants to the catalog.

    Args:
        languages: Language catalog to extend.
    """
    # Add special Google Translate variants
    if "zh" in languages:
        languages["zh-CN"] = Language(
            code="zh-CN", name="Chinese (Simplified)", flag=_get_flag_emoji("CN"), bot_translation_available=False
        )
        languages["zh-TW"] = Language(
            code="zh-TW", name="Chinese (Traditional)", flag=_get_flag_emoji("TW"), bot_translation_available=False
        )


def _add_language_aliases(languages: dict[str, Language]) -> None:
    """Add common Google Translate aliases to the catalog.

    Args:
        languages: Language catalog to extend.
    """
    # Add common aliases for Google Translate
    if "he" in languages:
        languages["iw"] = languages["he"]
    if "yi" in languages:
        languages["ji"] = languages["yi"]


LANGUAGES: Final[dict[str, Language]] = _generate_languages()

# Reasonable popularity sorting: Ukrainian (dev priority), then English, Spanish, French, German, etc.
POPULAR_LANGUAGES: Final[list[str]] = [
    "uk",  # Ukrainian
    "en",  # English
    "es",  # Spanish
    "fr",  # French
    "de",  # German
    "it",  # Italian
    "pl",  # Polish
    "pt",  # Portuguese
    "zh",  # Chinese
    "ja",  # Japanese
    "ko",  # Korean
]
