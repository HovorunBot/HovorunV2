"""Verify the shared extraction-action runner and distinct provider sequences."""

from functools import partial

import pytest

from hovorunv2.application.media.actions import ExtractionAction, ExtractionStage, run_extraction_actions
from hovorunv2.application.media.errors import BrowserContinuationRequired, MediaExtractionError
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.payloads import RichMediaPayload
from hovorunv2.application.media.providers.threads import ThreadsService
from hovorunv2.application.media.providers.twitter import TwitterService
from tests.utils.sync_http import FakeSyncHTTP

pytestmark = pytest.mark.unit

PAYLOAD = RichMediaPayload(author_name="Synthetic", author_handle="synthetic", author_url="", content="ok")
ACTIONS_LOGGER = "hovorunv2.application.media.actions"


class _Recorder:
    """Record action invocation order and return controlled outcomes."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def succeed(self, name: str) -> RichMediaPayload:
        self.calls.append(name)
        return PAYLOAD

    def fail(self, name: str) -> RichMediaPayload:
        self.calls.append(name)
        msg = "synthetic miss"
        raise MediaExtractionError(msg)

    def miss(self, name: str) -> None:
        self.calls.append(name)


def _action(name: str, run: object, stage: ExtractionStage = ExtractionStage.MEDIA) -> ExtractionAction:
    assert callable(run)
    return ExtractionAction(name, stage, run)


def test_runner_returns_first_success_without_running_later_actions() -> None:
    """An early success stops the sequence; later network actions never run."""
    recorder = _Recorder()
    actions = (
        _action("first", partial(recorder.succeed, "first")),
        _action("second", partial(recorder.succeed, "second")),
    )
    assert run_extraction_actions(actions, provider_name="synthetic") is PAYLOAD
    assert recorder.calls == ["first"]


def test_runner_continues_after_recoverable_failure() -> None:
    """A recoverable failure advances to the next action instead of aborting."""
    recorder = _Recorder()
    actions = (
        _action("failing", partial(recorder.fail, "failing")),
        _action("success", partial(recorder.succeed, "success")),
    )
    assert run_extraction_actions(actions, provider_name="synthetic") is PAYLOAD
    assert recorder.calls == ["failing", "success"]


def test_runner_raises_exhausted_when_all_actions_miss() -> None:
    """Exhaustion is an explicit failure, not a false empty result."""
    recorder = _Recorder()
    with pytest.raises(MediaExtractionError, match="exhausted"):
        run_extraction_actions((_action("miss", partial(recorder.miss, "miss")),), provider_name="synthetic")
    assert recorder.calls == ["miss"]


def test_runner_hands_off_at_browser_action() -> None:
    """A browser-stage marker raises the handoff signal before any later action."""
    recorder = _Recorder()
    actions = (
        _action("media", partial(recorder.miss, "media")),
        _action("browser", partial(recorder.miss, "browser"), ExtractionStage.BROWSER),
        _action("after", partial(recorder.succeed, "after")),
    )
    with pytest.raises(BrowserContinuationRequired):
        run_extraction_actions(actions, provider_name="synthetic")
    assert recorder.calls == ["media"]


def test_runner_propagates_cancellation() -> None:
    """Cancellation and other non-recoverable exceptions are not swallowed."""

    def cancel() -> RichMediaPayload:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_extraction_actions((_action("cancel", cancel),), provider_name="synthetic")


class _FailingExtractor(NativeMediaExtractor):
    """Force the generic backend strategy to miss recoverably."""

    def extract(self, url: str, cookies: dict[str, str] | None = None) -> RichMediaPayload:
        msg = "generic miss"
        raise MediaExtractionError(msg)


def _action_order(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == ACTIONS_LOGGER]


def test_threads_declares_direct_generic_browser_order(caplog: pytest.LogCaptureFixture) -> None:
    """Threads hands off after direct and generic tiers, without Instagram-only actions."""
    caplog.set_level("INFO", logger=ACTIONS_LOGGER)
    with pytest.raises(BrowserContinuationRequired):
        ThreadsService.sync().extract_media_sync(
            "https://www.threads.net/@synthetic/post/ABC123", FakeSyncHTTP(), _FailingExtractor()
        )
    assert _action_order(caplog) == [
        "Starting direct extraction strategy for provider threads",
        "Starting generic extraction strategy for provider threads",
        "Generic extraction strategy failed for provider threads",
        "Provider requires browser continuation: threads",
    ]


def test_twitter_declares_direct_generic_metadata_order(caplog: pytest.LogCaptureFixture) -> None:
    """A non-browser provider finishes with the metadata tier, never a browser handoff."""
    caplog.set_level("INFO", logger=ACTIONS_LOGGER)
    with pytest.raises(MediaExtractionError, match="exhausted"):
        TwitterService.sync().extract_media_sync(
            "https://x.com/synthetic/status/123456", FakeSyncHTTP(), _FailingExtractor()
        )
    assert _action_order(caplog) == [
        "Starting direct extraction strategy for provider twitter",
        "Direct extraction strategy failed for provider twitter",
        "Starting generic extraction strategy for provider twitter",
        "Generic extraction strategy failed for provider twitter",
        "Starting metadata extraction strategy for provider twitter",
    ]
