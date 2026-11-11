"""Verify shared language identity without loading either detection backend."""

import pytest

from hovorunv2.domain.languages import normalize_language_code

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("UK", "uk"),
        (" en ", "en"),
        ("iw", "he"),
        ("ji", "yi"),
        ("in_ID", "id-ID"),
        ("zh_cn", "zh-CN"),
        ("zh_TW", "zh-TW"),
        ("zh_hant_tw", "zh-Hant-TW"),
        ("und", "und"),
    ],
)
def test_language_identity_normalizes_without_losing_script_or_region(source: str, expected: str) -> None:
    """Keep shared identities independent of model support and presentation flags."""
    assert normalize_language_code(source) == expected


@pytest.mark.parametrize("source", ["", "auto", "uk--UA", "uk/UA", "uk-Cyrl-ua-extra", "українська"])
def test_invalid_language_shape_has_a_safe_error(source: str) -> None:
    """Reject invalid metadata without including its rejected value in the error."""
    with pytest.raises(ValueError, match="Language code must") as failure:
        normalize_language_code(source)
    assert source not in str(failure.value) or not source
