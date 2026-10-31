"""Narrow untrusted provider JSON without leaking dynamic types into parsing code."""

from collections.abc import Mapping, Sequence


def mapping(value: object) -> dict[str, object]:
    """Return a string-keyed copy of a mapping or an empty mapping."""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def sequence(value: object) -> list[object]:
    """Return a list of items or an empty list."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return list(value)


def text(value: object, default: str = "") -> str:
    """Return a string value or the supplied default."""
    return value if isinstance(value, str) else default
