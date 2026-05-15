"""Language data definitions dynamically populated from pycountry."""

from dataclasses import dataclass
from typing import Final

import pycountry

ISO_CODE_LEN: Final = 2


@dataclass(frozen=True)
class Language:
    """Information about a supported language."""

    code: str
    name: str
    flag: str
    bot_translation_available: bool = False

    @property
    def display_name(self) -> str:
        """Get name with flag for UI."""
        return f"{self.flag} {self.name}"


def _get_flag_emoji(country_code: str) -> str:
    """Convert country code to flag emoji with validation."""
    if len(country_code) != ISO_CODE_LEN:
        return "🌐"

    # Regional indicator symbols for A-Z
    code = country_code.upper()
    if not all("A" <= c <= "Z" for c in code):
        return "🌐"

    return "".join(chr(127397 + ord(c)) for c in code)


def _generate_languages() -> dict[str, Language]:
    """Dynamically generate language data."""
    # Common mappings for flag generation (language code to country code)
    lang_to_country = {
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
    non_country_langs = {"eo", "la", "sa", "ia", "ie", "vo"}

    languages: dict[str, Language] = {}

    # ISO 639-1 (2-letter) codes
    for lang in pycountry.languages:
        code = getattr(lang, "alpha_2", None)
        if not code:
            continue

        name = getattr(lang, "name", "Unknown")

        # Security/Policy: Russia danger symbol
        if code == "ru":
            f = "🩸"
        elif code in non_country_langs:
            f = "🌐"
        else:
            country_code = lang_to_country.get(code, code.upper())
            f = _get_flag_emoji(country_code)

        languages[code] = Language(code=code, name=name, flag=f, bot_translation_available=(code == "en"))

    # Add special Google Translate variants
    if "zh" in languages:
        languages["zh-CN"] = Language(
            code="zh-CN", name="Chinese (Simplified)", flag=_get_flag_emoji("CN"), bot_translation_available=False
        )
        languages["zh-TW"] = Language(
            code="zh-TW", name="Chinese (Traditional)", flag=_get_flag_emoji("TW"), bot_translation_available=False
        )

    # Add common aliases for Google Translate
    if "he" in languages:
        languages["iw"] = languages["he"]
    if "yi" in languages:
        languages["ji"] = languages["yi"]

    return languages


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
