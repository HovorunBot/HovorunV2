"""Verify caption detection policy and backend language identity without infrastructure."""

import pytest

from hovorunv2.application.data.languages import LANGUAGES
from hovorunv2.application.translation import translation_rules as rules
from hovorunv2.application.translation.ports import (
    TranslationBudget,
    TranslationDocument,
    TranslationIntent,
    TranslationScope,
)
from hovorunv2.domain import languages

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bonjour https://example.org/fr @person #vacances 123! 🌞", "Bonjour"),
        ("www.example.org @person #旅行 😃 123", ""),
        ("&lt;Bonjour&gt; &amp; bienvenue&#33;", "Bonjour bienvenue"),
        ("Привіт, світе!", "Привіт світе"),
        ("你好\uff0c世界\uff01", "你好 世界"),
        ("नमस्ते दुनिया", "नमस्ते दुनिया"),
        ("cafe\u0301 déjà", "café déjà"),
        ("hello\nworld\t42", "hello world"),
        ("#café @імя https://example.org?q=bonjour", ""),
        ("go", "go"),
    ],
)
def test_detection_text_removes_noise_without_losing_unicode(raw: str, expected: str) -> None:
    """Keep natural language evidence while excluding social metadata and numeric noise."""
    assert rules.sanitize_for_detection(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" IW ", "he"),
        ("ji", "yi"),
        ("in-ID", "id-ID"),
        ("ZH_hANT_TW", "zh-Hant-TW"),
        ("uk_UA", "uk-UA"),
        ("pt-BR", "pt-BR"),
    ],
)
def test_language_identity_preserves_regions_and_scripts(raw: str, expected: str) -> None:
    """Canonicalize aliases and separators without discarding regional identity."""
    assert languages.normalize_language_code(raw) == expected


@pytest.mark.parametrize(
    ("source", "target", "ignored", "expected"),
    [
        ("fr", "uk", (), True),
        ("und", "uk", (), False),
        ("", "uk", (), False),
        ("UK", "uk", (), False),
        ("iw", "he", (), False),
        ("fr-FR", "uk", ("FR",), False),
        ("en-US", "en", (), False),
        ("en", "uk", ("en-US",), False),
        ("zh-TW", "zh-CN", (), True),
        ("zh-Hant", "zh-TW", (), False),
        ("zh-TW", "uk", ("zh",), False),
        ("fr", "und", (), False),
    ],
)
def test_translation_gate_honors_language_preferences(
    source: str, target: str, ignored: tuple[str, ...], expected: bool
) -> None:
    """Skip unknown, equivalent target, and ignored sources before any engine work."""
    assert rules.should_translate_language(source, target, iter(ignored)) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("uk", "__uk__"),
        ("EN-us", "__en__"),
        ("fr", "__fr__"),
        ("de", "__de__"),
        ("ja", "__ja__"),
        ("es", "__es__"),
        ("pl", "__pl__"),
        ("iw", "__he__"),
        ("ji", "__yi__"),
        ("in", "__id__"),
        ("zh", "__zh__"),
        ("zh-CN", "__zh__"),
        ("zh-TW", "__zh__"),
        ("zh_Hant_HK", "__zh__"),
        ("zh-HK", "__zh__"),
        ("sr-Latn", None),
        ("sr-Cyrl", "__sr__"),
        ("ar", "__ar__"),
        ("fa", "__fa__"),
        ("ms", "__ms__"),
        ("no", "__no__"),
        ("sq", "__sq__"),
        ("sw", "__sw__"),
        ("la", None),
        ("en-Cyrl", None),
        ("zh-Latn", None),
        ("und", None),
        ("xx", None),
    ],
)
def test_model_language_resolution_respects_supported_scripts(code: str, expected: str | None) -> None:
    """Resolve supported tags without silently substituting an unsupported explicit script."""
    assert rules.resolve_model_language(code) == expected


@pytest.mark.parametrize(("alias", "canonical"), [("iw", "he"), ("ji", "yi")])
def test_legacy_catalog_entries_use_canonical_language_metadata(alias: str, canonical: str) -> None:
    """Preserve legacy settings identifiers without duplicating their display metadata."""
    assert LANGUAGES[alias] == LANGUAGES[canonical]


def test_redacted_representations_do_not_expose_text_contents() -> None:
    """Ensure translation documents and results never leak message contents in repr."""
    private_source = "Sensitive source text"
    private_translated = "Sensitive translated text"
    document = TranslationDocument(
        text=private_source,
        chat_id=123,
        intent=TranslationIntent.EXPLICIT,
        scope=TranslationScope.SELECTED_QUOTE,
        source_message_id=456,
        target_lang="uk",
    )
    result = rules.TranslationResult(private_translated, "uk", "🇬🇧")

    assert private_source not in repr(document)
    assert private_translated not in repr(result)
    assert f"text_len={len(private_source)}" in repr(document)
    assert f"text_len={len(private_translated)}" in repr(result)


def test_explicit_intent_bypasses_ignored_languages_without_allowing_same_language() -> None:
    """Allow explicit requests for ignored languages while still suppressing same-language no-ops."""
    assert not rules.should_translate_for_intent("fr", "uk", ("fr",), TranslationIntent.AUTOMATIC)
    assert rules.should_translate_for_intent("fr", "uk", ("fr",), TranslationIntent.EXPLICIT)
    assert not rules.should_translate_for_intent("uk", "uk", ("fr",), TranslationIntent.EXPLICIT)
    assert not rules.should_translate_for_intent("und", "uk", (), TranslationIntent.EXPLICIT)


def test_target_precedence_and_source_scope_selection() -> None:
    """Apply per-request target precedence and explicit quote scope over full source."""
    assert rules.resolve_target_language("de", "uk", "en") == "de"
    assert rules.resolve_target_language(None, "uk", "en") == "uk"
    assert rules.resolve_target_language("  ", None, "en") == "en"
    with pytest.raises(ValueError, match="target language"):
        rules.resolve_target_language(None, None, "und")

    text, scope = rules.resolve_translation_source("Full caption text", "Selected quote")
    assert text == "Selected quote"
    assert scope == TranslationScope.SELECTED_QUOTE

    full_text, full_scope = rules.resolve_translation_source("Full caption text")
    assert full_text == "Full caption text"
    assert full_scope == TranslationScope.FULL_SOURCE

    with pytest.raises(ValueError, match="quote"):
        rules.resolve_translation_source("Full caption text", "   ")


def test_translation_budget_tracks_monotonic_deadline() -> None:
    """Validate finite positive budgets and report remaining time and expiration."""
    with pytest.raises(ValueError, match="finite and positive"):
        TranslationBudget.from_timeout(0.0)
    with pytest.raises(ValueError, match="finite and positive"):
        TranslationBudget.from_timeout(float("inf"))

    budget = TranslationBudget.from_timeout(15.0, now_monotonic=100.0)
    assert budget.remaining_seconds(now_monotonic=110.0) == 5.0
    assert not budget.is_expired(now_monotonic=114.9)
    assert budget.is_expired(now_monotonic=115.0)
