"""Shared access command syntax for private previews and offline administration."""

import argparse
from typing import Never

from hovorunv2.application.access.administration import AdminAction, AdminRequest
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import TargetKind


class AccessArgumentParser(argparse.ArgumentParser):
    """Report invalid private input without terminating the bot process."""

    def error(self, message: str) -> Never:
        """Raise a semantic input error that interfaces can present safely."""
        raise ValueError(message)


def add_access_arguments(parser: argparse.ArgumentParser) -> None:
    """Configure one operational command without role, limit or recovery mutations."""
    parser.add_argument("action", choices=tuple(AdminAction))
    parser.add_argument("kind", choices=tuple(TargetKind))
    parser.add_argument("target", help="Existing numeric Telegram ID or known @username.")
    parser.add_argument("--reason", help="Internal operational reason; never sent to the target.")
    parser.add_argument("--public", dest="public_explanation", help="Optional public-safe explanation.")
    parser.add_argument("--ban-kind", choices=tuple(BanKind), default=BanKind.FULL.value)
    parser.add_argument("--feature", default="*")
    parser.add_argument("--request-id", type=int)


def parse_access(arguments: list[str]) -> AdminRequest:
    """Parse bounded operational fields and preserve quoted reason text."""
    parser = AccessArgumentParser(prog="/admin", add_help=False)
    add_access_arguments(parser)
    return AdminRequest.model_validate(vars(parser.parse_args(arguments)))
