"""Typed provider extraction actions and the shared media-stage runner."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from hovorunv2.application.media.errors import BrowserContinuationRequired, MediaExtractionError
from hovorunv2.application.media.payloads import RichMediaPayload

logger = logging.getLogger(__name__)


class ExtractionStage(StrEnum):
    """Worker stage that owns an extraction action."""

    MEDIA = "media"
    BROWSER = "browser"


@dataclass(frozen=True)
class ExtractionAction:
    """A provider-declared extraction step with an explicit stage and typed runner.

    Args:
        name: Human-readable strategy label for operational logging.
        stage: Worker stage owning this action.
        run: Zero-argument action returning usable media, or None for a miss.
    """

    name: str
    stage: ExtractionStage
    run: Callable[[], RichMediaPayload | None]


def run_extraction_actions(
    actions: Sequence[ExtractionAction],
    *,
    provider_name: str,
) -> RichMediaPayload:
    """Execute the ordered media-stage actions, then hand off to browser or fail.

    Args:
        actions: Provider-declared ordered actions.
        provider_name: Provider name for operational logging.

    Returns:
        Usable provider payload.

    Raises:
        BrowserContinuationRequired: When a browser-stage action is reached.
        MediaExtractionError: When every action is exhausted without usable media.
    """
    for action in actions:
        if action.stage == ExtractionStage.BROWSER:
            logger.info("Provider requires browser continuation: %s", provider_name)
            raise BrowserContinuationRequired
        logger.info("Starting %s strategy for provider %s", action.name, provider_name)
        try:
            payload = action.run()
        except MediaExtractionError:
            logger.info("%s strategy failed for provider %s", action.name.capitalize(), provider_name)
            continue
        if payload is not None:
            return payload
    msg = "Media extraction strategies are exhausted."
    raise MediaExtractionError(msg)
