"""Resolve direct-user and automatic-group policies without conflating safety or access."""

from dataclasses import replace

from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.features import FeatureRegistry, TriggerKind
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.usage import (
    MEDIA_BUCKET,
    ResolvedUsage,
    UsagePolicyStore,
    UsageSafetyCaps,
    UsageSubject,
    UsageUnit,
)
from hovorunv2.application.usage_resolution import resolve_usage
from hovorunv2.domain.enums import CommandName, TargetKind


class UsagePolicyService:
    """Apply overrides before trusted/ordinary defaults; keep group defaults independent."""

    def __init__(
        self,
        users: UserStore,
        chats: ChatStore,
        registry: FeatureRegistry,
        store: UsagePolicyStore,
        permissions: PermissionService,
        safety: UsageSafetyCaps,
    ) -> None:
        self._users = users
        self._chats = chats
        self._registry = registry
        self._store = store
        self._permissions = permissions
        self._safety = safety

    async def _subject(
        self, actor_id: int | None, group_id: int | None, trigger: TriggerKind
    ) -> tuple[UsageSubject, bool]:
        if trigger == TriggerKind.AUTOMATIC:
            chat = await self._chats.get_chat(group_id or 0)
            if chat is None or chat.chat_id != group_id or chat.chat_type not in {"group", "supergroup"}:
                msg = "Automatic usage requires an active canonical group."
                raise ValueError(msg)
            return UsageSubject(TargetKind.GROUP, chat.chat_id, chat.id, chat.generation), False
        if actor_id is None or actor_id <= 0:
            msg = "Direct usage requires an active user."
            raise ValueError(msg)
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None:
            msg = "Direct usage requires an active user."
            raise ValueError(msg)
        return UsageSubject(TargetKind.USER, actor_id, user.id, user.generation), user.is_trusted

    async def resolve(
        self,
        feature: str,
        trigger: TriggerKind,
        actor_id: int | None = None,
        *,
        group_id: int | None = None,
        unit: UsageUnit = UsageUnit.REQUESTS,
    ) -> ResolvedUsage:
        """Return intersecting feature/family limits and immutable independent safety inputs."""
        definition = self._registry.require(feature)
        if trigger not in definition.triggers or unit not in definition.units:
            msg = "Feature does not support this usage trigger or unit."
            raise ValueError(msg)
        subject, trusted = await self._subject(actor_id, group_id, trigger)
        bypass = (
            subject.kind == TargetKind.USER
            and actor_id is not None
            and await self._permissions.has_authority(actor_id, "bot:administer")
        )
        return resolve_usage(
            definition,
            subject,
            await self._store.list_overrides(subject.target),
            self._safety,
            trusted=trusted,
            bypass=bypass,
            unit=unit,
        )

    async def resolve_media(
        self, provider: CommandName, trigger: TriggerKind, actor_id: int | None, *, group_id: int | None = None
    ) -> ResolvedUsage:
        """Reserve ingress against the shared family before extraction determines the exact content split."""
        feature = self._registry.capture(provider, is_video=True)
        usage = await self.resolve(feature.key, trigger, actor_id, group_id=group_id)
        return replace(usage, policies=tuple(policy for policy in usage.policies if policy.bucket == MEDIA_BUCKET))
