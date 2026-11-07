# Instagram extraction

[Back to README](../README.md) · [Architecture](architecture.md) · [Operations rollout](operations-rollout.md)

Public Instagram posts are extracted in the media and browser worker roles. Instaloader is no longer a runtime or
distribution dependency (removed in `refactor(instagram): remove Instaloader fallback and dependency`).

## Strategy order

Instagram declares its ordered extraction actions (executed by the shared `application/media/actions.py` runner):

1. `direct` — no direct Instagram API strategy in this deployment; yields silently.
2. `generic extraction` — the native `yt-dlp` backend with the configured `instagram_sessionid` cookie.
3. `embed fallback` — the public vxinstagram JSON status route through a bounded, cookie-free HTTP client.
4. `browser extraction` — the browser worker enumerates complete carousels from structured post data and bounded lazy
   traversal.

Earlier strategy failures are recoverable and advance to the next action. A browser handoff keeps the original job
deadline and admission fence; it never replays completed network actions.

## Disable controls

- `INSTAGRAM_SESSIONID` — optional Instagram `sessionid` cookie for direct Instagram access. It is used only for
  `yt-dlp` and the direct browser session; it is never sent to the third-party embed service.
- `VXINSTAGRAM_ENABLED` (default `true`) — when `false`, a concrete no-request adapter is injected; the embed action
  fails recoverably and the chain continues to the browser. The browser path never depends on the embed service.
- `BROWSER_ENABLED` (default `false`) — gates browser extraction and continuation. When disabled, jobs that reach the
  browser handoff terminate with the existing failure notice.

## Bounds

- `ExtractionDeadline` is application-owned and monotonic; it clamps to the remaining job and stage time and reserves
  10 s cleanup plus a 5 s handoff reserve before extraction.
- Provisional strategy allowances: 30 s `yt-dlp`, 10 s embed. Browser traversal and media downloads share the remaining
  stage allowance rather than resetting per slide.
- The browser SDK is bounded per navigation/JS/selection call, with an owned-child teardown when the shared deadline
  expires. The bounded HTTP client aborts over-size bodies and enforces total (not socket-inactivity) time budget.

## Privacy

- The embed client is cookie-free (`discard_cookies=True`, `trust_env=False`) and receives only an allowlisted
  shortcode/type route; Telegram identity and Instagram cookies are never forwarded.
- Media URLs in the embed response are validated as public HTTPS before use; credential-bearing, local and non-public
  targets are rejected.
- Stored candidate media and captions stay in the existing encrypted transient paths. Diagnostics are limited to
  strategy, allowlisted reason, status, count and duration.

## Incomplete albums

Completeness requires nonempty usable media plus count agreement or a verified terminal slide. Incomplete output is
labelled with the incomplete-media footer rather than claiming a total. Contract 20 / D07 cross-action retained-candidate
selection (prefer a complete result, otherwise the best usable partial, encrypted across the browser handoff) is a
documented follow-up; the current runner returns the best in-stage result.

## Optional live diagnostics

`python ai-schemes/instagram-extraction/experiments/probe_vxinstagram.py` checks a public photo and reel prefix without
retaining payloads. It is an optional operator diagnostic, not a CI gate or a deployment readiness proof.

## Rollout

Rebuild and restart the media and browser worker roles. No database migration, queue schema change or Instaloader
installation is required. Verify the roles import without `instaloader` and that `vxinstagram_enabled`/`browser_enabled`
match the deployment intent.
