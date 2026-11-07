"""Synthetic Instagram post pages for deterministic browser carousel tests."""

import json
from collections.abc import Iterable

NEXT_BUTTON_HTML = '<button id="ig-next" aria-label="Next" onclick="__igNext()">Next</button>'


def _structured_edge(position: int, url: str, is_video: bool) -> dict[str, object]:
    node: dict[str, object] = {"id": f"slide-{position}"}
    if is_video:
        node.update({"__typename": "GraphVideo", "is_video": True, "video_url": url, "display_url": url})
    else:
        node.update({"__typename": "GraphImage", "display_url": url})
    return {"node": node}


def _slide_html(position: int, url: str, is_video: bool, *, active: bool) -> str:
    active_attr = "false" if active else "true"
    media = f'<video src="{url}"></video>' if is_video else f'<img src="{url}">'
    return f'<li aria-posinset="{position + 1}" aria-hidden="{active_attr}">{media}</li>'


def carousel_page(
    shortcode: str,
    slides: Iterable[tuple[str, bool]],
    *,
    structured_observed: int | None = None,
    loop: bool = False,
    advance_delay_ms: int = 0,
) -> str:
    """Build a synthetic Instagram post page with structured data and a carousel.

    Args:
        shortcode: Requested post shortcode embedded in structured data.
        slides: Ordered (url, is_video) media for the carousel DOM.
        structured_observed: Number of slides exposed in structured data; defaults to all.
        loop: Whether the next control wraps instead of disappearing at the terminal.
        advance_delay_ms: Synthetic delay between clicking and observing the next slide.

    Returns:
        Self-contained HTML exercising the Instagram structured and traversal selectors.
    """
    slide_list = list(slides)
    total = len(slide_list)
    observed = total if structured_observed is None else structured_observed
    structured = {
        "shortcode": shortcode,
        "__typename": "GraphSidecar",
        "edge_sidecar_to_children": {
            "count": total,
            "edges": [_structured_edge(i, slide_list[i][0], slide_list[i][1]) for i in range(observed)],
        },
    }
    structured_json = json.dumps(structured)
    items = [_slide_html(i, slide_list[i][0], slide_list[i][1], active=i == 0) for i in range(total)]
    wrap_mode = "true" if loop else "false"
    script = f"""
    window._sharedData = {{ entry_data: {{ PostPage: [{{ graphql: {{ shortcode_media: {structured_json} }} }}] }} }};
    let __igCurrent = 0;
    const __igSlides = Array.from(document.getElementById('synthetic-post').querySelectorAll('li'));
    function __igNext() {{
        setTimeout(() => {{
        __igSlides[__igCurrent].setAttribute('aria-hidden', 'true');
        __igCurrent += 1;
        if (__igCurrent >= __igSlides.length) {{
            if (!{wrap_mode}) {{ document.getElementById('ig-next').remove(); return; }}
            __igCurrent = 0;
        }}
        __igSlides[__igCurrent].setAttribute('aria-hidden', 'false');
        if (!{wrap_mode} && __igCurrent === __igSlides.length - 1) {{
            document.getElementById('ig-next').remove();
        }}
        }}, {advance_delay_ms});
    }}
    """
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<style>article,li,img,video{width:400px;height:300px} "
        'li[aria-hidden="true"]{display:none}</style></head><body>'
        f'<article id="synthetic-post"><a href="https://www.instagram.com/p/{shortcode}/">Post</a>'
        f"<ul>{''.join(items)}</ul>{NEXT_BUTTON_HTML}</article>"
        f"<script>{script}</script></body></html>"
    )
