"""Pure caption detection, translation eligibility, and model language mapping."""

import html
import re
import unicodedata
from collections.abc import Iterable
from typing import Final, NamedTuple

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
# Keep M2M100-418M vocabulary support and default script choices explicit.
_M2M100_SCRIPT_LANGUAGES: Final[dict[str, str]] = {
    "Arab": "ar fa ps sd ur",
    "Armn": "hy",
    "Beng": "bn",
    "Cyrl": "ba be bg kk mk mn ru sr uk",
    "Deva": "hi mr ne",
    "Ethi": "am",
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
        "af ast az br bs ca ceb cs cy da de en es et ff fi fr fy ga gd gl ha hr ht hu id ig ilo "
        "is it jv lb lg ln lt lv mg ms nl no ns oc om pl pt ro sk sl so sq ss su sv sw tl tn tr "
        "uz vi wo xh yo zu"
    ),
    "Mlym": "ml",
    "Mymr": "my",
    "Orya": "or",
    "Sinh": "si",
    "Taml": "ta",
    "Telu": "te",
    "Thai": "th",
}
_M2M100_ALLOWED_SCRIPTS: Final[dict[str, frozenset[str]]] = {
    "zh": frozenset({"hans", "hant"}),
}
_ZH_SCRIPT_REGIONS: Final[dict[str, str]] = {
    "cn": "hans",
    "sg": "hans",
    "tw": "hant",
    "hk": "hant",
    "mo": "hant",
}


def _build_m2m100_codes() -> tuple[dict[str, str], dict[str, str]]:
    """Build M2M100 vocabulary tokens and their expected script tags.

    Returns:
        Tuple of (iso_code -> model_token, iso_code -> lowercase_default_script).
    """
    tokens: dict[str, str] = {}
    scripts: dict[str, str] = {}
    for script, identifiers in _M2M100_SCRIPT_LANGUAGES.items():
        for identifier in identifiers.split():
            tokens[identifier] = f"__{identifier}__"
            scripts[identifier] = script.lower()
    return tokens, scripts


M2M100_CODES: Final[dict[str, str]]
_M2M100_DEFAULT_SCRIPTS: Final[dict[str, str]]
M2M100_CODES, _M2M100_DEFAULT_SCRIPTS = _build_m2m100_codes()


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


def resolve_model_language(lang_code: str) -> str | None:
    """Resolve a supported language variety without replacing an explicit unsupported script.

    Args:
        lang_code: ISO language identifier with optional region and script subtags.

    Returns:
        M2M100 model vocabulary tag, or None when the language or requested script is unsupported.
    """
    if not lang_code.strip():
        return None
    code = normalize_language_code(lang_code).casefold()
    language, *subtags = code.split("-")
    token = M2M100_CODES.get(language)
    if token is None:
        return None
    script = next((tag for tag in subtags if len(tag) == _SCRIPT_SUBTAG_LENGTH), None)
    if script is not None:
        allowed = _M2M100_ALLOWED_SCRIPTS.get(language)
        if allowed is not None:
            return token if script in allowed else None
        if _M2M100_DEFAULT_SCRIPTS.get(language) != script:
            return None
    return token


_SCRIPT_SUBTAG_LENGTH: Final = 4


def _language_identity(lang_code: str) -> str:
    if not lang_code.strip():
        return "und"
    code = normalize_language_code(lang_code).casefold()
    language, *subtags = code.split("-")
    if language == "zh":
        script = next((tag for tag in subtags if len(tag) == _SCRIPT_SUBTAG_LENGTH), None)
        if script in {"hans", "hant"}:
            return f"zh-{script}"
        region = next((tag for tag in subtags if tag in _ZH_SCRIPT_REGIONS), None)
        return f"zh-{_ZH_SCRIPT_REGIONS[region]}" if region is not None else "zh-hans"
    return resolve_model_language(code) or language


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
