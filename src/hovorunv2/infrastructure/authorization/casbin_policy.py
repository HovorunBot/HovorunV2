"""Immutable in-memory Casbin policy loaded from packaged text assets."""

import asyncio
import csv
from importlib.resources import files
from io import StringIO

from casbin import Enforcer, Model
from casbin.persist.adapters import StringAdapter

from hovorunv2.application.permissions import PermissionFacts, PermissionTarget
from hovorunv2.domain.enums import Role

POLICY_FIELDS = 5


def _at_least(role: Role, minimum: str) -> bool:
    """Reuse the application's integer authority ordering inside the policy matcher."""
    return role >= Role[minimum.upper()]


class CasbinAuthorizationPolicy:
    """Expose checks only; never mutate per-user links or persist parallel assignments."""

    def __init__(self, model_text: str, policy_text: str) -> None:
        rules = self._validate_rules(policy_text)
        try:
            model = Model()
            model.load_model_from_text(model_text)
            self._enforcer = Enforcer(model, StringAdapter(policy_text))
            self._enforcer.add_function("atLeast", _at_least)
            probes = [
                self.is_allowed(PermissionFacts(Role.OWNER, action, PermissionTarget(resource), group_admin=True))
                for action, resource in rules
            ]
        except Exception as error:
            msg = "Invalid authorization model or policy."
            raise ValueError(msg) from error

        if not all(probes):
            msg = "Authorization policy rule cannot match its declared resource and action."
            raise ValueError(msg)

    def _validate_rules(self, policy_text: str) -> list[tuple[str, str]]:
        """Fail initialization on malformed static policy instead of silently dropping rules."""
        rules: list[tuple[str, str]] = []
        for row in csv.reader(StringIO(policy_text), skipinitialspace=True):
            if not row or row[0].startswith("#"):
                continue
            if len(row) != POLICY_FIELDS or row[0] != "p":
                msg = "Authorization policy must contain four-field permission rules."
                raise ValueError(msg)
            _, minimum, action, resource, authority = row
            if (
                minimum.upper() not in Role.__members__
                or not action
                or not resource
                or authority
                not in (
                    "role",
                    "group_admin",
                )
            ):
                msg = "Authorization policy has an invalid role, action, resource or authority source."
                raise ValueError(msg)
            rules.append((action, resource))
        if not rules:
            msg = "Authorization policy must contain at least one permission rule."
            raise ValueError(msg)
        return rules

    def is_allowed(self, facts: PermissionFacts) -> bool:
        """Pure synchronous check over fresh caller-supplied facts."""
        return bool(self._enforcer.enforce(facts.role, facts.action, facts.target, facts.group_admin))


def load_packaged_policy() -> CasbinAuthorizationPolicy:
    """Read resources through importlib so installed and zipped wheels both work."""
    assets = files("hovorunv2.infrastructure.authorization").joinpath("policies")
    return CasbinAuthorizationPolicy(
        assets.joinpath("model.conf").read_text(encoding="utf-8"),
        assets.joinpath("policy.csv").read_text(encoding="utf-8"),
    )


async def load_authorization_policy() -> CasbinAuthorizationPolicy:
    """Perform policy file reads and initialization off the application's event loop."""
    return await asyncio.to_thread(load_packaged_policy)
