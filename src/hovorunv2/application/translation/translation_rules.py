"""Pure translation response parsing and language presentation shared by both runtimes."""

import html
from typing import NamedTuple

from hovorunv2.application.data.languages import ISO_CODE_LEN, LANGUAGES, REGIONAL_INDICATOR_OFFSET
from hovorunv2.application.jobs.http_ports import HTTPFailure, HTTPTransportError

GOOGLE_TRANSLATION_URL = "https://translate.googleapis.com/translate_a/single"
SOURCE_LANGUAGE_INDEX = 2
MANDATORY_IGNORED_LANGS = ("und",)
DEFAULT_TRANSLATION_FLAG = "🌐"


class TranslationRawResult(NamedTuple):
    """Raw translation and normalized source language without transport state."""

    text: str
    source_lang: str


class TranslationResult(NamedTuple):
    """Translation selected for display with the source-language flag."""

    text: str
    target_lang: str
    flag: str


def google_translation_params(text: str, target_lang: str) -> dict[str, str]:
    """Build Google translation query parameters, preserving aliases and decoding display HTML.

    Args:
        text: Text to translate.
        target_lang: Target language code.

    Returns:
        Query parameters for the Google translation request.
    """
    # Map some common aliases if needed
    return {
        "client": "gtx",
        "sl": "auto",
        "tl": "zh-CN" if target_lang == "zh" else target_lang,
        "dt": "t",
        "q": html.unescape(text),
    }


def parse_google_translation(data: object) -> TranslationRawResult:
    """Require usable translated segments or raise a safe malformed-response failure.

    Args:
        data: Decoded JSON response from the translation provider.

    Returns:
        Raw translation result with normalized source language.

    Raises:
        HTTPTransportError: If the response is missing or lacks usable translated text.
    """
    if not isinstance(data, list) or len(data) <= SOURCE_LANGUAGE_INDEX:
        raise HTTPTransportError(HTTPFailure.MALFORMED)
    source = data[SOURCE_LANGUAGE_INDEX]
    if source is None or source == "":
        source = "und"
    sentences = data[0]
    if not isinstance(source, str) or not isinstance(sentences, list) or not sentences:
        raise HTTPTransportError(HTTPFailure.MALFORMED)
    # Normalize common aliases back to canonical form
    source = {"iw": "he", "ji": "yi"}.get(source, source)
    text = "".join(
        sentence[0]
        for sentence in sentences
        if isinstance(sentence, list) and sentence and isinstance(sentence[0], str)
    )
    if not text.strip():
        raise HTTPTransportError(HTTPFailure.MALFORMED)
    return TranslationRawResult(text, source)


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


def select_translation(
    result: TranslationRawResult, target_lang: str, ignored_langs: tuple[str, ...] | list[str]
) -> TranslationResult | None:
    """Apply ignored-source and mandatory-undetermined-language rules in both runtimes.

    Args:
        result: Raw translation result to select.
        target_lang: Target language code.
        ignored_langs: Additional source languages to ignore.

    Returns:
        Selected translation, or None when the source language is ignored or undetermined.
    """
    # Enforce mandatory ignores and include target_lang
    ignored = {*ignored_langs, target_lang, *MANDATORY_IGNORED_LANGS}
    if result.source_lang in ignored:
        return None
    return TranslationResult(result.text, target_lang, translation_flag(result.source_lang))
