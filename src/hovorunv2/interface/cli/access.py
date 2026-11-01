"""Offline access administration using the same commands as private Telegram."""

import json
import logging

from hovorunv2.application.access.administration import AdminRequest
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.domain.audit import ActorContext

logger = logging.getLogger(__name__)


class AccessCLI:
    """Produce structured operational output without Telegram, cache or browser dependencies."""

    def __init__(self, administration: AdministrationService) -> None:
        self._administration = administration

    async def execute(self, request: AdminRequest) -> str:
        """Resolve current known targets and preserve actual CLI provenance.

        Args:
            request: Administrative command parsed from offline arguments.

        Returns:
            JSON-encoded result of the applied operation.

        Raises:
            ValueError: If the administration service refuses the operation.
        """
        actor = ActorContext.cli(reason=request.reason)
        target = await self._administration.resolve(request.kind, request.target)
        logger.info("Access CLI executing request: action=%s kind=%s", request.action, request.kind)
        result = await self._administration.execute(request, target, actor, None)
        outcome = result.get("outcome", "success")
        logger.info("Access CLI executed request: action=%s outcome=%s", request.action, outcome)
        if result.get("outcome") in {"denied", "unavailable", "group_banned", "stale_request"}:
            msg = f"Access operation refused: {result['outcome']}."
            raise ValueError(msg)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
