"""Unit tests for Telegram interaction Fluent catalogs and i18n core configuration."""

from pathlib import Path

import pytest
from aiogram_i18n.exceptions import KeyNotFoundError

from hovorunv2.interface.telegram.i18n import (
    DEFAULT_LOCALE,
    LOCALE_NAMES,
    SECONDARY_FALLBACK_LOCALE,
    SUPPORTED_LOCALES,
    create_i18n_core,
)


@pytest.mark.unit
def test_supported_locales_constants() -> None:
    """Verify supported locales and default fallback configuration."""
    assert DEFAULT_LOCALE == "uk"
    assert SECONDARY_FALLBACK_LOCALE == "en"
    assert SUPPORTED_LOCALES == ("uk", "en")
    assert LOCALE_NAMES["uk"] == "Українська"
    assert LOCALE_NAMES["en"] == "English"


@pytest.mark.unit
def test_i18n_core_loads_catalogs() -> None:
    """Verify FluentRuntimeCore discovers and initializes both en and uk catalogs."""
    core = create_i18n_core(load_now=True)
    assert set(core.locales) >= {"en", "uk"}


@pytest.mark.unit
def test_i18n_core_formats_basic_messages() -> None:
    """Verify basic message retrieval and named variable substitution in English and Ukrainian."""
    core = create_i18n_core(load_now=True)

    assert core.get("welcome", "en") == "Welcome to Hovorun!"
    assert core.get("welcome", "uk") == "Ласкаво просимо до Говоруна!"

    assert core.get("greeting", "en", name="Alice") == "Hello, Alice!"
    assert core.get("greeting", "uk", name="Олена") == "Привіт, Олена!"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("count", "expected_en", "expected_uk"),
    [
        (0, "0 items", "0 елементів"),
        (1, "1 item", "1 елемент"),
        (2, "2 items", "2 елементи"),
        (5, "5 items", "5 елементів"),
        (11, "11 items", "11 елементів"),
        (21, "21 items", "21 елемент"),
        (22, "22 items", "22 елементи"),
        (25, "25 items", "25 елементів"),
    ],
)
def test_i18n_core_ukrainian_and_english_plurals(count: int, expected_en: str, expected_uk: str) -> None:
    """Verify CLDR plural rules for English and all 8 required Ukrainian boundary cases."""
    core = create_i18n_core(load_now=True)

    assert core.get("item-count", "en", count=count) == expected_en
    assert core.get("item-count", "uk", count=count) == expected_uk


@pytest.mark.unit
def test_i18n_core_isolating_disabled_for_html() -> None:
    """Verify isolating control characters are omitted so Telegram HTML parsing succeeds."""
    core = create_i18n_core(load_now=True)

    rendered = core.get("status-html", "en", status="Active")
    assert rendered == "<b>Status:</b> Active"
    assert "\u2068" not in rendered
    assert "\u2069" not in rendered


@pytest.mark.unit
def test_i18n_core_missing_key_raises_error() -> None:
    """Verify missing message keys raise KeyNotFoundError with raise_key_error enabled."""
    core = create_i18n_core(load_now=True)

    with pytest.raises(KeyNotFoundError):
        core.get("nonexistent.message.key", "en")


@pytest.mark.unit
def test_i18n_core_extensible_with_third_locale(tmp_path: Path) -> None:
    """Verify core loads a synthetic third catalog without changing application logic."""
    test_locales = tmp_path / "locales"
    pl_dir = test_locales / "pl"
    pl_dir.mkdir(parents=True)
    (pl_dir / "messages.ftl").write_text("welcome = Witaj w Hovorun!\n", encoding="utf-8")

    core = create_i18n_core(locales_dir=test_locales, default_locale="pl", load_now=True)

    assert "pl" in core.locales
    assert core.get("welcome", "pl") == "Witaj w Hovorun!"


@pytest.mark.unit
def test_i18n_core_falls_back_from_uk_to_english_when_key_missing(tmp_path: Path) -> None:
    """Verify missing Ukrainian keys fall back to English as secondary fallback."""
    test_locales = tmp_path / "locales"
    uk_dir = test_locales / "uk"
    en_dir = test_locales / "en"
    uk_dir.mkdir(parents=True)
    en_dir.mkdir(parents=True)
    (uk_dir / "messages.ftl").write_text("welcome = Вітаємо!\n", encoding="utf-8")
    (en_dir / "messages.ftl").write_text("welcome = Welcome!\nonly-en = English fallback\n", encoding="utf-8")

    core = create_i18n_core(locales_dir=test_locales, default_locale="uk", load_now=True)

    assert core.get("welcome", "uk") == "Вітаємо!"
    assert core.get("only-en", "uk") == "English fallback"
    with pytest.raises(KeyNotFoundError):
        core.get("missing-everywhere", "uk")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("count", "expected_en", "expected_uk"),
    [
        (0, "0 more parts available", "Доступно ще 0 частин"),
        (1, "1 more part available", "Доступна ще 1 частина"),
        (2, "2 more parts available", "Доступні ще 2 частини"),
        (5, "5 more parts available", "Доступно ще 5 частин"),
        (11, "11 more parts available", "Доступно ще 11 частин"),
        (21, "21 more parts available", "Доступна ще 21 частина"),
        (22, "22 more parts available", "Доступні ще 22 частини"),
        (25, "25 more parts available", "Доступно ще 25 частин"),
    ],
)
def test_translation_catalog_keys_and_pluralization(count: int, expected_en: str, expected_uk: str) -> None:
    """Verify translation UI keys, interpolation, and Ukrainian/English plural boundaries."""
    core = create_i18n_core(load_now=True)

    assert core.get("translation-btn-translate", "uk") == "Перекласти"
    assert core.get("translation-btn-translate", "en") == "Translate"
    assert core.get("translation-btn-continue", "uk") == "Продовжити переклад"
    assert core.get("translation-btn-continue", "en") == "Continue translation"
    assert core.get("translation-progress", "uk")
    assert core.get("translation-help", "uk")
    assert core.get("translation-reply-required", "uk")
    assert core.get("translation-empty-source", "uk")
    assert core.get("translation-unavailable", "uk")
    assert core.get("translation-expired", "uk")
    assert core.get("translation-denied", "uk")
    assert core.get("translation-already-complete", "uk")
    assert "xx" in core.get("translation-invalid-target", "uk", language="xx")
    assert core.get("translation-remaining-parts", "en", count=count) == expected_en
    assert core.get("translation-remaining-parts", "uk", count=count) == expected_uk
