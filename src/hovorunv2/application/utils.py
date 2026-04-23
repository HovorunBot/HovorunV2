"""Utility functions for the application."""

from typing import Any, Final, NoReturn


class Undefined:
    """Sentinel for uninitialized services that raises AttributeError on access."""

    def __getattr__(self, name: str) -> NoReturn:
        """Raise exception on any access to these object attributes."""
        msg = (
            f"Access to '{name}' failed. Service not initialized. "
            "Ensure container.init() was called and awaited before use."
        )
        raise AttributeError(msg)

    def __bool__(self) -> bool:
        """Indicate that this object represents an undefined state."""
        return False


UNDEFINED: Final[Any] = Undefined()


def format_number(num: int) -> str:
    """Format large numbers into readable text (e.g. 1.2K, 3.4M)."""
    billion_threshold = 1_000_000_000
    million_threshold = 1_000_000
    thousand_threshold = 1_000

    if num >= billion_threshold:
        return f"{num / billion_threshold:.1f}B".replace(".0B", "B")
    if num >= million_threshold:
        return f"{num / million_threshold:.1f}M".replace(".0M", "M")
    if num >= thousand_threshold:
        return f"{num / thousand_threshold:.1f}K".replace(".0K", "K")
    return str(num)
