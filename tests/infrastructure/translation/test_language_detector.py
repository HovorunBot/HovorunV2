"""Exercise real local text classification without model inference or network access."""

import pytest

from hovorunv2.infrastructure.translation.language_detector import Py3LangIdDetector

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Це український текст про природу та подорожі Україною.", "uk"),
        ("This is an English sentence about nature and travelling.", "en"),
        ("Bonjour, voici une belle photographie de notre voyage en France.", "fr"),
        ("Das ist ein deutscher Text über die Natur und unsere Reise.", "de"),
        ("これは日本語で書かれた自然と旅行についての文章です。", "ja"),
        ("Esta es una fotografía de nuestro viaje por España.", "es"),
        ("To jest polski tekst o przyrodzie i podróżach po Polsce.", "pl"),
        ("😃 https://example.org #bonjour @person 123", "und"),
        ("a b", "und"),
        ("", "und"),
    ],
)
def test_real_local_detection(text: str, expected: str) -> None:
    """Classify representative captions and abstain when language evidence is insufficient."""
    assert Py3LangIdDetector().detect(text) == expected
