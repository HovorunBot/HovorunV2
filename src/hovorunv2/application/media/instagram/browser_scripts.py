"""Read requested-post JSON and ordinary article/carousel markup without provider SDKs."""

STRUCTURED_POST_JS = """
    const documents = [];
    const shared = window._sharedData;
    const additional = window.__additionalDataLoaded;
    if (shared) documents.push(shared);
    if (additional && typeof additional === 'object') documents.push(additional);
    for (const script of document.querySelectorAll('script[type="application/json"]')) {
        if (script.textContent.length > 2097152) continue;
        try { documents.push(JSON.parse(script.textContent)); } catch (_) {}
    }
    return documents;
"""

# Each operation resolves the requested article again; unrelated articles cannot supply media or controls.
POST_ARTICLE_JS = """
    const shortcode = __SHORTCODE__;
    const identifies = (value) => {
        try {
            const url = new URL(value, location.href);
            return /(^|\\.)instagram\\.com$/.test(url.hostname) &&
                new RegExp(
                    '/(?:p|reel|reels|tv)/' + shortcode + '(?:/c/[A-Za-z0-9_-]+(?:/r/[A-Za-z0-9_-]+)?)?/?$'
                ).test(url.pathname);
        } catch (_) { return false; }
    };
    const articles = Array.from(document.querySelectorAll('article'));
    const article = articles.find(node => Array.from(node.querySelectorAll('a[href]'))
        .some(anchor => identifies(anchor.href))) ||
        (articles.length === 1 && (identifies(location.href) ||
            identifies(document.querySelector('meta[property="og:url"]')?.content)) ? articles[0] : null);
    if (!article) return null;
"""

OBSERVE_SLIDE_JS = (
    POST_ARTICLE_JS
    + """
    const viewport = article.getBoundingClientRect();
    const visible = (node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' &&
            node.closest('[aria-hidden="true"]') === null && rect.width > 0 && rect.height > 0 &&
            rect.right > viewport.left && rect.left < viewport.right;
    };
    const entries = Array.from(article.querySelectorAll('li')).filter(node => node.querySelector('video, img'));
    const slide = entries.find(visible);
    const root = slide || article;
    const video = Array.from(root.querySelectorAll('video')).find(visible);
    const image = Array.from(root.querySelectorAll('img')).find(node =>
        !node.closest('header, footer, nav') && visible(node));
    const media = video || image;
    if (!media) return null;
    const rawPosition = slide?.getAttribute('aria-posinset');
    const position = rawPosition ? Number(rawPosition) - 1 : Math.max(0, entries.indexOf(slide));
    const source = video?.querySelector('source')?.src;
    return {
        position,
        id: shortcode + ':' + position,
        url: media.currentSrc || media.src || source || '',
        is_video: Boolean(video),
        carousel: entries.length > 1 || Boolean(article.querySelector('button[aria-label="Next"]')),
    };
"""
)

ADVANCE_SLIDE_JS = (
    POST_ARTICLE_JS
    + """
    const button = article.querySelector('button[aria-label="Next"], button[aria-label="Далі"]');
    if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
    button.click();
    return true;
"""
)
