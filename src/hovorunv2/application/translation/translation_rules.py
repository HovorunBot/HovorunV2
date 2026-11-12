"""Pure caption detection, translation eligibility, and model language mapping."""

import html
import re
import unicodedata
from collections.abc import Iterable
from typing import Final, NamedTuple

import pycountry

from hovorunv2.application.data.languages import ISO_CODE_LEN, LANGUAGES, REGIONAL_INDICATOR_OFFSET
from hovorunv2.domain.languages import normalize_language_code

DEFAULT_TRANSLATION_FLAG = "🌐"


class TranslationResult(NamedTuple):
    """Translation selected for display with the source-language flag."""

    text: str
    target_lang: str
    flag: str


def translation_flag(lang_code: str) -> str:
    """Return the flag emoji for a language code with the regional-code fallback.

    Args:
        lang_code: Language code to resolve.

    Returns:
        Flag emoji for the language.
    """
    # Normalize and check centralized data
    code = lang_code.lower().split("-")[0].split("_")[0]
    if code in LANGUAGES:
        return LANGUAGES[code].flag
    # Fallback for regional or missing codes
    country = code.upper()
    if len(country) != ISO_CODE_LEN:
        return DEFAULT_TRANSLATION_FLAG
    return "".join(chr(REGIONAL_INDICATOR_OFFSET + ord(letter)) for letter in country)


MIN_DETECTABLE_CHARS: Final = 3
_DETECTION_NOISE: Final = re.compile(r"https?://\S+|www\.\S+|[@#]\w+", re.IGNORECASE)
# Keep NLLB support and script choices explicit; derive standard ISO identifiers from pycountry.
_FLORES_SCRIPT_LANGUAGES: Final[dict[str, str]] = {
    "Arab": "ar fa ks ps sd ug ur",
    "Armn": "hy",
    "Beng": "as bn",
    "Cyrl": "ba be bg kk ky mk mn ru sr tg tt uk",
    "Deva": "hi mr ne sa",
    "Ethi": "am ti",
    "Geor": "ka",
    "Grek": "el",
    "Gujr": "gu",
    "Guru": "pa",
    "Hang": "ko",
    "Hans": "zh",
    "Hebr": "he yi",
    "Jpan": "ja",
    "Khmr": "km",
    "Knda": "kn",
    "Laoo": "lo",
    "Latn": (
        "af ay az bm bs ca cs cy da de en eo es et eu fi fj fo fr ga gd gl gn ha hr ht hu id ig "
        "is it jv kg ki ku lb lg li ln lt lv mg mi ms mt nb nl nn no ny oc om pl pt qu ro rn rw "
        "sg sk sl sm sn so sq ss st su sv sw tk tl tn tr ts tw uz vi wo xh yo zu"
    ),
    "Mlym": "ml",
    "Mymr": "my",
    "Orya": "or",
    "Sinh": "si",
    "Taml": "ta",
    "Telu": "te",
    "Thai": "th",
    "Tibt": "bo dz",
}
# NLLB selects a specific variety for these ISO macrolanguages.
_FLORES_LANGUAGE_VARIANTS: Final[dict[str, str]] = {
    "ar": "arb",
    "ay": "ayr",
    "az": "azj",
    "fa": "pes",
    "ku": "kmr",
    "lv": "lvs",
    "mg": "plt",
    "mn": "khk",
    "ms": "zsm",
    "ne": "npi",
    "no": "nob",
    "om": "gaz",
    "or": "ory",
    "ps": "pbt",
    "qu": "quy",
    "sq": "als",
    "sw": "swh",
    "uz": "uzn",
    "yi": "ydd",
}
_FLORES_SCRIPT_VARIANTS: Final[dict[str, str]] = {
    "zh-hans": "Hans",
    "zh-hant": "Hant",
    "zh-cn": "Hans",
    "zh-sg": "Hans",
    "zh-tw": "Hant",
    "zh-hk": "Hant",
    "zh-mo": "Hant",
    "ks-deva": "Deva",
}


def _build_flores_codes() -> dict[str, str]:
    """Derive ISO identifiers while preserving explicit NLLB support and script selections.

    Returns:
        Supported language identifiers mapped to model vocabulary tags.

    Raises:
        ValueError: If a configured model language has no ISO entry.
    """
    codes: dict[str, str] = {}
    for script, identifiers in _FLORES_SCRIPT_LANGUAGES.items():
        for identifier in identifiers.split():
            language = pycountry.languages.get(alpha_2=identifier)
            if language is None:
                msg = "NLLB language has no ISO entry"
                raise ValueError(msg)
            model_language = _FLORES_LANGUAGE_VARIANTS.get(identifier, language.alpha_3)
            codes[identifier] = f"{model_language}_{script}"
    for identifier, script in _FLORES_SCRIPT_VARIANTS.items():
        base = identifier.partition("-")[0]
        model_language = codes[base].partition("_")[0]
        codes[identifier] = f"{model_language}_{script}"
    return codes


FLORES_200_CODES: Final[dict[str, str]] = _build_flores_codes()


def sanitize_for_detection(text: str) -> str:
    """Remove social noise while retaining Unicode letters and their combining marks.

    Args:
        text: Caption containing possible HTML entities and social metadata.

    Returns:
        Whitespace-normalized language evidence without URLs, handles, or hashtags.
    """
    decoded = unicodedata.normalize("NFC", html.unescape(text))
    clean = _DETECTION_NOISE.sub(" ", decoded)
    letters = "".join(
        character if character.isalpha() or unicodedata.category(character).startswith("M") else " "
        for character in clean
    )
    return " ".join(letters.split())


def resolve_flores_code(lang_code: str) -> str | None:
    """Resolve a supported language variety without replacing an explicit unsupported script.

    Args:
        lang_code: ISO language identifier with optional region and script subtags.

    Returns:
        Model vocabulary tag, or None when the language or requested script is unsupported.
    """
    if not lang_code.strip():
        return None
    code = normalize_language_code(lang_code).casefold()
    if code in FLORES_200_CODES:
        return FLORES_200_CODES[code]
    language, *subtags = code.split("-")
    script = next((tag for tag in subtags if len(tag) == _SCRIPT_SUBTAG_LENGTH), None)
    if script is not None:
        specific = FLORES_200_CODES.get(f"{language}-{script}")
        if specific is not None:
            return specific
        default = FLORES_200_CODES.get(language)
        if default is None or default.split("_")[1].lower() != script:
            return None
        return default
    return FLORES_200_CODES.get(language)


_SCRIPT_SUBTAG_LENGTH: Final = 4


def _language_identity(lang_code: str) -> str:
    if not lang_code.strip():
        return "und"
    code = normalize_language_code(lang_code)
    return resolve_flores_code(code) or code.split("-")[0]


def should_translate_language(source_lang: str, target_lang: str, ignored_langs: Iterable[str]) -> bool:
    """Gate translation using normalized source, target, and ignored language identities.

    Args:
        source_lang: Detected source language identifier.
        target_lang: Effective target language preference.
        ignored_langs: Effective source language preferences to skip.

    Returns:
        Whether a determined source differs from the target and all ignored languages.
    """
    source = _language_identity(source_lang)
    target = _language_identity(target_lang)
    if source == "und" or target in ("und", source):
        return False
    source_base = normalize_language_code(source_lang).split("-")[0]
    for ignored in ignored_langs:
        code = normalize_language_code(ignored)
        if code == source_base or _language_identity(code) == source:
            return False
    return True
