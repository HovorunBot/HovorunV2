"""Current private/CLI authority and shared audited access administration."""

from hovorunv2.application.access.administration import AdminAction, AdminRequest, AdminTarget
from hovorunv2.application.access.ban_service import BanService
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.groups.activation import ActivationAction, ActivationRequest, ActivationStore
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.application.usage.policies import UsagePolicy, UsageUnit, UsageWindow
from hovorunv2.application.usage.usage_administration import UsageAdministrationService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ActorOrigin, TargetKind

MAX_TELEGRAM_ID = (1 << 63) - 1


class AdministrationService:
    """Resolve only known identities and delegate mutations to existing audited commands."""

    def __init__(
        self,
        users: UserStore,
        chats: ChatStore,
        handles: PublicHandleStore,
        permissions: PermissionService,
        trust: TrustService,
        bans: BanService,
        beta: BetaEligibilityService,
        activation: ActivationStore,
        grants: GroupGrantStore,
        usage: UsageAdministrationService,
    ) -> None:
        self._users = users
        self._chats = chats
        self._handles = handles
        self._permissions = permissions
        self._trust = trust
        self._bans = bans
        self._beta = beta
        self._activation = activation
        self._grants = grants
        self._usage = usage

    async def authorize(self, actor: ActorContext, transport: str | None) -> None:
        """Require current private bot administration or actual CLI origin.

        Args:
            actor: Audited actor requesting access.
            transport: Transport the actor used.

        Raises:
            PermissionError: If the actor is not the CLI or a private bot administrator, or is banned.
        """
        if actor.origin == ActorOrigin.CLI:
            return
        if (
            actor.origin != ActorOrigin.TELEGRAM
            or transport != "private"
            or actor.actor_id is None
            or not await self._permissions.has_authority(actor.actor_id, "bot:administer")
        ):
            msg = "Access administration requires a bot administrator in private chat or CLI."
            raise PermissionError(msg)
        if await self._bans.list_active(TargetKind.USER, actor.actor_id):
            msg = "Administrative access is restricted."
            raise PermissionError(msg)

    async def resolve(self, kind: TargetKind, reference: str) -> AdminTarget:
        """Resolve a known identity to a canonical target without provisioning.

        Args:
            kind: Kind of the target to resolve.
            reference: Numeric Telegram ID or @handle supplied by the caller.

        Returns:
            Resolved target bound to the current identity generation.

        Raises:
            ValueError: If the reference cannot be resolved to an active canonical target.
        """
        telegram_id = await self._resolve_reference(kind, reference)
        if not -MAX_TELEGRAM_ID <= telegram_id <= MAX_TELEGRAM_ID or telegram_id == 0:
            msg = "Telegram ID must be a nonzero signed 64-bit integer."
            raise ValueError(msg)
        if kind == TargetKind.USER:
            user = await self._users.get_by_telegram_id(telegram_id)
            if telegram_id <= 0 or user is None:
                msg = "No active known user."
                raise ValueError(msg)
            return AdminTarget(kind=kind, telegram_id=telegram_id, entity_id=user.id, generation=user.generation)
        chat = await self._chats.get_chat(telegram_id)
        if (
            telegram_id >= 0
            or chat is None
            or chat.chat_id != telegram_id
            or chat.chat_type not in {"group", "supergroup"}
        ):
            msg = "No active canonical group."
            raise ValueError(msg)
        return AdminTarget(kind=kind, telegram_id=telegram_id, entity_id=chat.id, generation=chat.generation)

    async def _resolve_reference(self, kind: TargetKind, reference: str) -> int:
        """Resolve a numeric ID or @handle to its known target.

        Args:
            kind: Kind of the target to resolve.
            reference: Numeric Telegram ID or @handle to resolve.

        Returns:
            Numeric Telegram ID of the resolved target.

        Raises:
            ValueError: If no active target has that known handle.
        """
        if not reference.startswith("@"):
            return int(reference)
        handle = await self._handles.resolve_handle(reference)
        if handle is None or handle.target_kind != kind:
            msg = "No active target has that known handle."
            raise ValueError(msg)
        return handle.target_id

    async def inspect(self, target: AdminTarget, actor: ActorContext, transport: str | None) -> dict[str, object]:
        """Return operational state without internal reasons, salts or message contents.

        Args:
            target: Target to inspect.
            actor: Audited actor performing the inspection.
            transport: Transport the actor used.

        Returns:
            Operational state keyed by field name.

        Raises:
            ValueError: If the target identity changed since the preview.
        """
        await self.authorize(actor, transport)
        current = await self.resolve(target.kind, str(target.telegram_id))
        if current != target:
            msg = "Target identity changed. Repeat lookup."
            raise ValueError(msg)
        result: dict[str, object] = target.model_dump(mode="json")
        result["bans"] = [ban.kind for ban in await self._bans.list_active(target.kind, target.telegram_id)]
        if target.kind == TargetKind.USER:
            user = await self._users.get_by_telegram_id(target.telegram_id)
            if user is not None:
                result.update(role=user.role.name.lower(), trusted=user.is_trusted)
        else:
            result["approved"] = bool(await self._grants.approvals(target.entity_id))
            result["requests"] = [
                {"id": request.id, "status": request.status.value}
                for request in await self._grants.requests(target.entity_id)
            ]
        return result

    async def execute(
        self, request: AdminRequest, target: AdminTarget, actor: ActorContext, transport: str | None
    ) -> dict[str, object]:
        """Recheck the previewed identity and authority before executing an operation.

        Args:
            request: Administration request to execute.
            target: Previewed target to execute against.
            actor: Audited actor performing the operation.
            transport: Transport the actor used.

        Returns:
            Operation result keyed by field name.

        Raises:
            ValueError: If the request target kind differs from the preview.
        """
        state = await self.inspect(target, actor, transport)
        if request.kind != target.kind:
            msg = "Target kind changed."
            raise ValueError(msg)
        permission_target = PermissionTarget(target.kind.value, target.entity_id, target.generation, target.telegram_id)
        match request.action:
            case AdminAction.INSPECT | AdminAction.REQUESTS:
                return state
            case AdminAction.LIMITS:
                return await self._inspect_limits(permission_target, request)
            case AdminAction.LIMIT_SET:
                changed = await self._usage.set_override(
                    permission_target,
                    self._limit_policy(request),
                    actor=actor,
                    transport_kind=transport,
                )
            case AdminAction.LIMIT_RESET:
                changed = await self._usage.reset_override(
                    permission_target,
                    self._limit_policy(request),
                    actor=actor,
                    transport_kind=transport,
                )
            case AdminAction.TRUST | AdminAction.UNTRUST:
                changed = await self._execute_trust(request, target, actor, transport)
            case AdminAction.BAN:
                changed = await self._bans.apply(
                    target.kind,
                    target.telegram_id,
                    BanKind(request.ban_kind),
                    actor,
                    request.public_explanation,
                    transport_kind=transport,
                )
            case AdminAction.UNBAN:
                changed = await self._bans.revoke(
                    target.kind, target.telegram_id, BanKind(request.ban_kind), actor, transport_kind=transport
                )
            case AdminAction.BETA_GRANT | AdminAction.BETA_REVOKE:
                changed = await self._beta.set_grant(
                    permission_target,
                    request.feature,
                    granted=request.action == AdminAction.BETA_GRANT,
                    actor=actor,
                    transport_kind=transport,
                )
            case AdminAction.APPROVE | AdminAction.ACCEPT | AdminAction.REJECT:
                return await self._execute_activation(request, permission_target, actor, transport)
        return {"changed": changed}

    async def _inspect_limits(self, target: PermissionTarget, request: AdminRequest) -> dict[str, object]:
        """Return effective default or override values with independent safety caps.

        Args:
            target: Permission target to inspect limits for.
            request: Administration request carrying the feature and unit.

        Returns:
            Usage inspection view serialized to a dictionary.
        """
        view = await self._usage.inspect(target, request.feature, request.unit or UsageUnit.REQUESTS)
        return view.as_dict()

    def _limit_policy(self, request: AdminRequest) -> UsagePolicy:
        """Build the typed override policy for the request.

        Args:
            request: Administration request carrying limit fields.

        Returns:
            Typed usage policy for the override.

        Raises:
            ValueError: If bucket, unit or window is missing.
        """
        bucket = request.bucket
        unit = request.unit
        window_seconds = request.window_seconds
        if bucket is None or unit is None or window_seconds is None:
            msg = "Limit changes require bucket, unit and window."
            raise ValueError(msg)
        if request.action == AdminAction.LIMIT_SET:
            return self._complete_limit_policy(request, bucket, unit, window_seconds)
        return UsagePolicy(bucket, 1, 1, 1, 1, unit, UsageWindow(window_seconds))

    def _complete_limit_policy(
        self, request: AdminRequest, bucket: str, unit: UsageUnit, window_seconds: int
    ) -> UsagePolicy:
        """Build a complete positive override policy.

        Args:
            request: Administration request carrying limit, burst, concurrency and pending.
            bucket: Usage bucket for the override.
            unit: Usage unit for the override.
            window_seconds: Override window in seconds.

        Returns:
            Fully populated usage policy.

        Raises:
            ValueError: If any override dimension is missing.
        """
        limit = request.limit
        burst = request.burst
        concurrency = request.concurrency
        pending = request.pending
        if limit is None or burst is None or concurrency is None or pending is None:
            msg = "Setting a limit requires limit, burst, concurrency and pending."
            raise ValueError(msg)
        return UsagePolicy(bucket, limit, burst, concurrency, pending, unit, UsageWindow(window_seconds))

    async def _execute_trust(
        self, request: AdminRequest, target: AdminTarget, actor: ActorContext, transport: str | None
    ) -> bool:
        """Apply a trust change to a verified user target.

        Args:
            request: Administration request carrying the trust action.
            target: Previewed target.
            actor: Audited actor performing the change.
            transport: Transport the actor used.

        Returns:
            Whether the trust flag was applied.

        Raises:
            ValueError: If the target is not a user.
        """
        if target.kind != TargetKind.USER:
            msg = "Trust applies only to users."
            raise ValueError(msg)
        return await self._trust.set_trust(
            str(target.telegram_id),
            is_trusted=request.action == AdminAction.TRUST,
            actor=actor,
            transport_kind=transport,
        )

    async def _execute_activation(
        self, request: AdminRequest, target: PermissionTarget, actor: ActorContext, transport: str | None
    ) -> dict[str, object]:
        """Translate the selected administration action into an activation request.

        Args:
            request: Administration request carrying the action and request ID.
            target: Permission target for the activation.
            actor: Audited actor performing the action.
            transport: Transport the actor used.

        Returns:
            Activation outcome and request ID keyed by field name.
        """
        action = {
            AdminAction.APPROVE: ActivationAction.GRANT,
            AdminAction.ACCEPT: ActivationAction.APPROVE,
            AdminAction.REJECT: ActivationAction.REJECT,
        }[request.action]
        result = await self._activation.execute(
            ActivationRequest(
                actor.actor_id or 0,
                target,
                transport or "cli",
                action,
                request.request_id,
                administrator_actor=actor,
            )
        )
        return {"outcome": result.outcome.value, "request_id": result.request_id}
