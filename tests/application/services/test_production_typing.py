"""Production annotations avoid the unrestricted Any sentinel."""

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "hovorunv2"
ANY_PATTERN = re.compile(r"\bAny\b")


def test_no_typing_any_in_production_modules() -> None:
    """Every production module uses bounded object or generic annotations."""
    offenders = [
        str(path.relative_to(SOURCE_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if ANY_PATTERN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
