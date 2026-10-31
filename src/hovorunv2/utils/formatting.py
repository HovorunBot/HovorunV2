"""Number formatting utilities."""

from typing import Final

BILLION: Final = 1_000_000_000
MILLION: Final = 1_000_000
THOUSAND: Final = 1_000


def format_number(num: int) -> str:
    """Format a large integer as compact human-readable text.

    Args:
        num: Integer value to format.

    Returns:
        Compact B/M/K-suffixed text, or the plain decimal string for values below one thousand.
    """
    if num >= BILLION:
        return f"{num / BILLION:.1f}B".replace(".0B", "B")
    if num >= MILLION:
        return f"{num / MILLION:.1f}M".replace(".0M", "M")
    if num >= THOUSAND:
        return f"{num / THOUSAND:.1f}K".replace(".0K", "K")
    return str(num)
