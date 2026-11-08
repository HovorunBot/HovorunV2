"""Provide the development release tool's complete PEP 440 version parser."""

import re

from commitizen.version_schemes import Pep440
from packaging.version import VERSION_PATTERN


class HotfixVersion(Pep440):
    """Parse complete PEP 440 releases in tags and changelog headings.

    Commitizen's default parser truncates four-part releases when reading changelog
    headings. Keep its existing bump policy while using the packaging parser for
    both ordinary releases and explicitly selected hotfix versions.
    """

    parser = re.compile(rf"(?P<version>(?ix:{VERSION_PATTERN}))")
