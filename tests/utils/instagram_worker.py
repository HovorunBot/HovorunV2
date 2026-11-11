"""Exercise real Instagram worker services against synthetic external HTTP endpoints."""

from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from functools import partialmethod
from urllib.parse import urlsplit

import pytest
from yt_dlp.networking import Request, Response
from yt_dlp.networking.common import RequestDirector

from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.jobs.feature_job_runner import FeatureJobRunner
from hovorunv2.application.jobs.http_ports import SyncBrowserPort, SyncTelegramDeliveryPort
from hovorunv2.application.media.extractor import NativeMediaExtractor
from hovorunv2.application.media.providers.instagram import InstagramService
from hovorunv2.application.usage.budgets import RuntimeNamespace, WallClock
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.bootstrap.worker_database import WorkerDatabase
from hovorunv2.bootstrap.worker_runtime import WorkerQueuePublisher, WorkerRuntime
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from hovorunv2.infrastructure.media.embed_adapter import VxInstagramEmbedAdapter
from hovorunv2.infrastructure.runtime.feedback_gate import ValkeyFeedbackGate
from hovorunv2.interface.telegram.transport.sync_api import NativeTelegramAPI
from tests.utils.sync_http import HTTPFixture
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.translation import unavailable_translation

TEST_SAFETY = UsageSafetyCaps()


def _route_instagram_request(
    director: RequestDirector,
    request: Request,
    *,
    original: Callable[[RequestDirector, Request], Response],
    target: str,
) -> Response:
    """Route only the external Instagram request to a real loopback failure endpoint."""
    host = urlsplit(request.url).hostname or ""
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        msg = "Unexpected external host in synthetic Instagram test."
        raise AssertionError(msg)
    routed = request.copy()
    routed.update(url=target)
    return original(director, routed)


def route_instagram_backend(monkeypatch: pytest.MonkeyPatch, server: HTTPFixture) -> None:
    """Keep yt-dlp extraction real while routing its external requests to a synthetic HTTP server."""
    monkeypatch.setattr(
        RequestDirector,
        "send",
        partialmethod(_route_instagram_request, original=RequestDirector.send, target=server.url("/unavailable")),
    )


@contextmanager
def instagram_worker(
    settings: Settings,
    namespace: RuntimeNamespace,
    delivery: SyncTelegramDeliveryPort,
    server: HTTPFixture,
    browser: SyncBrowserPort | None = None,
    *,
    safety: UsageSafetyCaps = TEST_SAFETY,
) -> Generator[tuple[FeatureJobRunner, WorkerRuntime, WorkerDatabase]]:
    """Compose real child-owned persistence, extraction, translation and accounting for one test."""
    clock = WallClock()
    with (
        WorkerDatabase(settings, FEATURE_REGISTRY, safety) as database,
        WorkerRuntime(settings, clock, namespace, safety) as runtime,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1", "localhost", "synthetic.invalid"})) as http,
        closing(WorkerQueuePublisher(settings.valkey_url, namespace, safety, clock)) as publisher,
        unavailable_translation(settings, database.policies, server) as translation,
    ):
        embed = VxInstagramEmbedAdapter(
            base_url=server.url(""),
            allowed_hosts=frozenset({"127.0.0.1", "localhost", "synthetic.invalid", "media.example.invalid"}),
        )
        telegram = NativeTelegramAPI(http, BOT_CREDENTIAL, api_base=server.url(""))
        runner = FeatureJobRunner(
            admission=runtime.admission,
            payloads=runtime.payloads,
            budgets=runtime.budgets,
            policies=database.policies,
            ledger=database.ledger,
            http=http,
            delivery=delivery,
            translation=translation,
            extractor=NativeMediaExtractor(),
            clock=clock,
            settings=settings,
            publisher=publisher,
            telegram=telegram,
            feedback=database.policies,
            gate=ValkeyFeedbackGate(runtime.client, namespace),
            embed=embed,
            safety=safety,
            browser=browser,
            clients=[InstagramService.sync(settings, embed=embed)],
        )
        yield runner, runtime, database
