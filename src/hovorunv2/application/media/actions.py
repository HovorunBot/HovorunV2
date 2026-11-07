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
class ActionOutcome:
    """One action's usable media with explicit completeness evidence.

    Args:
        payload: Usable ordered media returned by the action.
        complete: True when completion is proven, False when known incomplete,
            None when completeness is unknown and the chain must continue.
    """

    payload: RichMediaPayload
    complete: bool | None


@dataclass(frozen=True)
class ExtractionAction:
    """A provider-declared extraction step with an explicit stage and typed runner.

    Args:
        name: Human-readable strategy label for operational logging.
        stage: Worker stage owning this action.
        run: Zero-argument action returning an outcome, or None for a miss.
    """

    name: str
    stage: ExtractionStage
    run: Callable[[], ActionOutcome | None]


def _better(retained: ActionOutcome | None, candidate: ActionOutcome) -> ActionOutcome:
    if retained is None:
        return candidate
    return candidate if len(candidate.payload.media_items) > len(retained.payload.media_items) else retained


def run_extraction_actions(actions: Sequence[ExtractionAction], *, provider_name: str) -> RichMediaPayload:
    """Execute the ordered media-stage actions, preferring a complete result.

    Incomplete and unknown-completeness results are retained and later actions still run. A
    proven-complete result returns immediately. On the browser handoff the best retained usable
    partial travels with `BrowserContinuationRequired`; after media exhaustion the best retained
    partial is returned, matching the D07 ranking (most slides, earlier attempt on a tie).

    Args:
        actions: Provider-declared ordered actions.
        provider_name: Provider name for operational logging.

    Returns:
        The first proven-complete result, otherwise the best retained usable partial.

    Raises:
        BrowserContinuationRequired: When a browser-stage action is reached, carrying the retained partial.
        MediaExtractionError: When every action is exhausted without usable media.
    """
    retained: ActionOutcome | None = None
    for action in actions:
        if action.stage == ExtractionStage.BROWSER:
            logger.info("Provider requires browser continuation: %s", provider_name)
            raise BrowserContinuationRequired(retained.payload if retained is not None else None)
        logger.info("Starting %s strategy for provider %s", action.name, provider_name)
        try:
            outcome = action.run()
        except MediaExtractionError:
            logger.info("%s strategy failed for provider %s", action.name.capitalize(), provider_name)
            continue
        if outcome is None:
            continue
        if outcome.complete is True:
            return outcome.payload
        retained = _better(retained, outcome)
    if retained is not None:
        return retained.payload
    msg = "Media extraction strategies are exhausted."
    raise MediaExtractionError(msg)
