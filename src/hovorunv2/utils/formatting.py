"""Number formatting utilities."""

from typing import Final

BILLION: Final = 1_000_000_000
MILLION: Final = 1_000_000
THOUSAND: Final = 1_000


def format_number(num: int) -> str:
    """Format large numbers into readable text (e.g. 1.2K, 3.4M)."""
    if num >= BILLION:
        return f"{num / BILLION:.1f}B".replace(".0B", "B")
    if num >= MILLION:
        return f"{num / MILLION:.1f}M".replace(".0M", "M")
    if num >= THOUSAND:
        return f"{num / THOUSAND:.1f}K".replace(".0K", "K")
    return str(num)
