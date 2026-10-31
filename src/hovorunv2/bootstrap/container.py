"""Dependency injection configuration using dishka."""

from typing import NewType

from hovorunv2.bootstrap.providers.identity import IdentityAndMembershipProvider
from hovorunv2.bootstrap.providers.infrastructure import InfrastructureProvider
from hovorunv2.bootstrap.providers.interface import InterfaceAndUtilityProvider
from hovorunv2.bootstrap.providers.media import MediaAndContentProvider
from hovorunv2.bootstrap.providers.persistence import AuditAndPersistenceProvider
from hovorunv2.bootstrap.providers.runtime import RuntimeUsageProvider
from hovorunv2.bootstrap.providers.security import SecurityAndAccessProvider
from hovorunv2.interface.telegram.handlers.base import BaseCommand

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])

__all__ = ["AppProvider", "InfrastructureProvider", "UtilityCommands"]


class AppProvider(
    SecurityAndAccessProvider,
    IdentityAndMembershipProvider,
    AuditAndPersistenceProvider,
    RuntimeUsageProvider,
    MediaAndContentProvider,
    InterfaceAndUtilityProvider,
):
    """Unified application provider composing domain, persistence and service modules."""
