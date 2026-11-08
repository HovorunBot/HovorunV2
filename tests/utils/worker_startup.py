"""Observe resource cleanup when a fresh worker lacks its optional media dependency."""

import json
import logging
import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec

from sqlalchemy import Engine, event

from hovorunv2.bootstrap.worker_runtime import WorkerChildContext
from hovorunv2.domain.enums import WorkerRole
from hovorunv2.infrastructure.browser.native_browser import NativeBrowserService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.http_transport.native_http import NativeHTTPClient
from tests.utils.sync_http import HTTPFixture, HTTPReply
from tests.utils.telegram import BOT_CREDENTIAL


class MissingMediaDependency(MetaPathFinder):
    """Model a deployment missing yt-dlp without substituting internal services."""

    def __init__(self) -> None:
        self.error = ModuleNotFoundError("Synthetic missing media dependency.")

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> ModuleSpec | None:
        """Reject the external optional dependency during the worker's first media import."""
        del path, target
        if fullname == "yt_dlp" or fullname.startswith("yt_dlp."):
            raise self.error
        return None


class CleanupFailureBrowser(NativeBrowserService):
    """Exercise teardown failure after closing an actual owned Chromium process."""

    def close(self) -> None:
        """Release the real browser before reporting a synthetic platform cleanup error."""
        super().close()
        msg = "Synthetic browser cleanup failure."
        raise OSError(msg)


def main() -> None:
    """Report only cleanup outcomes, never configuration or message contents."""
    logging.basicConfig(handlers=[logging.NullHandler()])
    settings = Settings(_env_file=None, bot_token=BOT_CREDENTIAL)
    disposed: list[Engine] = []
    record_disposal = disposed.append
    missing = MissingMediaDependency()
    browser_type = CleanupFailureBrowser if "--cleanup-failure" in sys.argv else NativeBrowserService
    browser = browser_type(allowed_hosts=frozenset({"127.0.0.1"}))
    observed = {"original_error": False}
    with (
        HTTPFixture({"/": HTTPReply(b"<html><body>startup fixture</body></html>")}) as server,
        NativeHTTPClient(allowed_hosts=frozenset({"127.0.0.1"})) as http,
    ):
        browser.get_content(server.url("/"))
        if not browser.is_running:
            msg = "The startup test requires a live browser."
            raise ValueError(msg)
        context = WorkerChildContext(settings, role=WorkerRole.BROWSER, browser=browser, http=http)
        event.listen(Engine, "engine_disposed", record_disposal)
        sys.meta_path.insert(0, missing)
        try:
            try:
                context.open()
            except ModuleNotFoundError as error:
                observed["original_error"] = error is missing.error
            observed["database_disposed"] = bool(disposed)
            observed["browser_stopped"] = not browser.is_running
            observed["browser_detached"] = context.browser is None
            observed["caller_http_usable"] = http.download_bytes(server.url("/")).startswith(b"<html>")
        finally:
            sys.meta_path.remove(missing)
            event.remove(Engine, "engine_disposed", record_disposal)
            context.close()
            NativeBrowserService.close(browser)
    sys.stdout.write(json.dumps(observed) + "\n")


if __name__ == "__main__":
    main()
