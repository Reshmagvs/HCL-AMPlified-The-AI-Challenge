"""Live discovery of learning material, and verification of everything found.

The curated catalogue answers "what should I read about gradient descent". It
cannot answer "what should I read about quantum error correction", because
nobody curated that. A product that only ever serves a fixed 426-row catalogue
is a demo of a catalogue, so this module goes and finds the material instead.

**Why there is a chain of sources rather than a search engine.**
The first version scraped DuckDuckGo's Lite endpoint and worked beautifully --
for about forty queries. Then every request began returning HTTP 202 with a
challenge page and zero results, instantly, and it stayed that way through a
cooldown. Building a product on one scraped endpoint means building a product
that stops working under exactly the load that means people are using it.

So discovery is a chain, tried in order, each with its own health state:

``wikimedia``  the MediaWiki search API across Wikipedia, Wikibooks and
               Wikiversity. It is a documented public API with a published
               access policy, it needs no key, and its coverage of academic
               subjects is close to total. It is first because it is the one
               source that can be relied on rather than merely hoped for. The
               policy requires an identifying ``Api-User-Agent``; sending one is
               why this works where a browser user-agent gets 403.

``duckduckgo`` the open web, which is where actual tutorials, exercises and
               course pages live. Best-effort: paced, and switched off for a
               cooldown the moment it starts refusing, so a blocked engine
               degrades the result instead of emptying it.

A source that fails is skipped, not fatal. As long as one answers, the learner
gets material.

**Nothing is taken on trust.** A search result is a *claim* that a page exists.
Every URL is fetched before it can enter the catalogue, and the title and
description are read out of the page that actually responded -- never out of the
search snippet, and never out of a language model. A page that does not serve,
or that serves a bot wall, is discarded. This is the rule that separates a
learning path from a plausible-looking list of dead links.

**Facts are observed, not guessed.** ``provider`` is the registrable domain that
served the bytes, resolved through the public suffix list. ``format`` is decided
by what the document contains -- an ``og:type`` of video, a ``<video>`` element,
an embedded player -- rather than by a list of known video sites, because such a
list is exactly the hardcoding this module exists to remove. ``cost`` is ``paid``
only when pricing appears together with commerce language. ``word_count`` is
counted, which is what makes the duration estimate an estimate *of a
measurement*.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx
import tldextract

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 12.0
SEARCH_TIMEOUT = 20.0
MAX_BYTES = 400_000
FETCH_WORKERS = 8

# A browser user-agent, because the alternative is being served a bot-check page
# whose title is "Access denied" and recording that as a learning resource.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Wikimedia's robot policy asks automated clients to identify themselves and
# give a way to be contacted. Sending a browser user-agent to their API returns
# 403 and a link to that policy; sending this returns results.
PROJECT_UA = "Lodestar/1.0 (learning-path planner; +https://github.com/retr0alfred/PathFinder)"

# Hosts whose operators ask to be addressed by an identifying agent rather than
# a browser string. This is access policy, not content policy -- it says how to
# knock, never what counts as a good resource.
_POLICY_UA_HOSTS = re.compile(r"(^|\.)(wikipedia|wikibooks|wikiversity|wikimedia)\.org$", re.I)

# A 200 is not proof of content. Khan Academy answers a bot check with HTTP 200
# and a page reading "Client Challenge"; a JavaScript-only page answers 200 with
# an empty shell. Both would otherwise be recorded as learning material.
#
# Measured on real pages rather than guessed: four usable pages carried 9,825 to
# 184,792 characters of visible text, while the two junk responses carried 228
# and 17. The gate sits an order of magnitude clear of both sides.
MIN_CONTENT_CHARS = 1_200

# How far behind the best match a page may fall and still be offered. Relative
# rather than absolute, because absolute cosine is not comparable across
# embedding models -- the same lesson ``retrieval`` learned about the catalogue.
RELEVANCE_MARGIN = 0.10

# The public suffix list, from the bundled snapshot -- no network lookup, and no
# hand-written list of "known" hosts. This is what makes en.khanacademy.org and
# www.khanacademy.org one provider while keeping iupac.qmul.ac.uk distinct from
# every other .ac.uk site.
_extract_domain = tldextract.TLDExtract(suffix_list_urls=())

_RESULT_RE = re.compile(
    r"<a[^>]+href=\"(?P<url>https?://[^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>"
    r"(?P<title>.*?)</a>",
    re.I | re.S,
)
_SNIPPET_RE = re.compile(
    r"<td[^>]*class=['\"]result-snippet['\"][^>]*>(?P<text>.*?)</td>", re.I | re.S
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(
    r"<meta[^>]+(?:name|property)=['\"](?P<key>[^'\"]+)['\"][^>]+"
    r"content=['\"](?P<value>[^'\"]*)['\"]",
    re.I,
)
_META_ALT_RE = re.compile(
    r"<meta[^>]+content=['\"](?P<value>[^'\"]*)['\"][^>]+"
    r"(?:name|property)=['\"](?P<key>[^'\"]+)['\"]",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)

# Pricing shown next to commerce language. Either half alone is noise: a page
# about e-commerce mentions prices, and a free course still says "enrol now".
_PRICE_RE = re.compile(r"(?:[$£€₹]\s?\d|\d+\s?(?:USD|EUR|GBP|INR)\b)", re.I)
_COMMERCE_RE = re.compile(
    r"\b(subscribe|subscription|buy now|purchase|add to cart|enroll now|enrol now|"
    r"start free trial|per month|billed annually|upgrade to pro|premium plan)\b",
    re.I,
)
_VIDEO_META_RE = re.compile(r"\b(video|movie)\b", re.I)
_VIDEO_TAG_RE = re.compile(r"<video[\s>]", re.I)
_PLAYER_RE = re.compile(r"<iframe[^>]+(youtube|vimeo|player|embed)", re.I)
_INTERACTIVE_RE = re.compile(
    r"\b(interactive|exercise|playground|sandbox|try it yourself|run the code|"
    r"hands-on lab|quiz)\b",
    re.I,
)
_LANG_RE = re.compile(r"<html[^>]+lang=['\"]([a-zA-Z_-]+)", re.I)

# A paragraph, and how long one has to be before it counts as prose rather than
# a caption, a breadcrumb or a link row.
_PARAGRAPH_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
MIN_PROSE_WORDS = 15
# Wiki citation templates and footnote markers survive tag-stripping because
# they are text, not markup, and they are not what the page is about.
_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}", re.S)
_REF_RE = re.compile(r"\[\d{1,3}\]")
# Markup that survived stripping. Prose does not contain braces or a closing
# tag; a paragraph that still does is a data blob wearing a paragraph's clothes,
# and the next paragraph is a better description than a repaired one.
_RESIDUE_RE = re.compile(r"[{}]|</|\"\}")

# Words that steer an open-web search towards teaching material and steer an
# encyclopaedia search into nonsense.
_LEARNING_WORDS_RE = re.compile(
    r"\b(tutorials?|courses?|guides?|lessons?|practice|exercises?|"
    r"for beginners|introduction to|learn)\b",
    re.I,
)

# Search engines and shorteners never carry the material themselves.
_NON_CONTENT_HOST_RE = re.compile(
    r"^(www\.)?(duckduckgo|google|bing|yandex|baidu|t\.co|bit\.ly)\.", re.I
)


@dataclass(frozen=True)
class SearchHit:
    """One unverified search result. The URL is a claim until it is fetched."""

    url: str
    title: str
    snippet: str
    source: str = ""


@dataclass(frozen=True)
class VerifiedPage:
    """A page that answered, described entirely by what it returned."""

    url: str
    final_url: str
    title: str
    description: str
    provider: str
    format: str
    cost: str
    language: str
    status: int
    # Words of visible text, counted in the response. This is what turns
    # "duration" from an invented number into a measured reading time.
    word_count: int


def _plain(fragment: str) -> str:
    """Strip tags and resolve entities from a fragment of HTML."""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _visible(body: str, limit: int) -> str:
    """Roughly the text a reader sees, with scripts and styles removed."""
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", body)))[:limit]


def user_agent_for(url: str) -> str:
    """Which agent string this host asks to be addressed by."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return PROJECT_UA if _POLICY_UA_HOSTS.search(host) else BROWSER_UA


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
class Source:
    """One place results can come from, with its own health and pacing.

    ``cooldown`` exists because a blocked engine does not recover within one
    request. When a source starts refusing, it is set aside for a while rather
    than retried on every skill of every topic, which would turn one blocked
    engine into a guaranteed few seconds of wasted latency per search.
    """

    name = "source"
    min_interval = 0.0
    cooldown_seconds = 900.0

    def prepare(self, query: str) -> str:
        """Shape the query for this source. Identity unless overridden."""
        return query

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._blocked_until = 0.0

    def healthy(self) -> bool:
        return time.monotonic() >= self._blocked_until

    def _pace(self) -> None:
        """Serialise calls to this source and keep them a polite gap apart."""
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _block(self, why: str) -> None:
        self._blocked_until = time.monotonic() + self.cooldown_seconds
        logger.warning(
            "search source %s is refusing (%s) -- standing down for %.0f minutes",
            self.name, why, self.cooldown_seconds / 60,
        )

    def search(self, query: str, limit: int) -> list[SearchHit]:
        """Run one query. Must never raise.

        The lock covers only the pacing decision, not the request. Holding it
        across the network call would serialise every search in the process --
        turning a rate limit of one request per interval into a throughput
        ceiling of one request per *round trip*, which for a topic with fifteen
        skills is the difference between twenty seconds and ninety.
        """
        if not self.healthy():
            return []
        with self._lock:
            self._pace()
        try:
            return self._search(self.prepare(query), limit)
        except httpx.HTTPError as exc:
            logger.info("%s failed for %r: %s", self.name, query[:50], str(exc)[:100])
            return []

    def _search(self, query: str, limit: int) -> list[SearchHit]:  # pragma: no cover
        raise NotImplementedError


class Wikimedia(Source):
    """MediaWiki search across the projects that carry teaching material.

    Wikipedia explains a subject, Wikibooks writes textbooks about it and
    Wikiversity builds courses. All three expose the same documented API, none
    needs a key, and between them they cover essentially every academic topic a
    learner is likely to name -- which is why this source is first.
    """

    name = "wikimedia"
    min_interval = 0.3
    PROJECTS = ("en.wikipedia.org", "en.wikibooks.org", "en.wikiversity.org")

    def prepare(self, query: str) -> str:
        """Drop the words that help a web search and hurt an encyclopaedia one.

        "Integration by parts practice" returns better articles as "integration
        by parts": a wiki has no page called "tutorial", so the word only adds
        noise to its relevance ranking.
        """
        return _LEARNING_WORDS_RE.sub(" ", query).strip() or query

    def _search(self, query: str, limit: int) -> list[SearchHit]:
        hits: list[SearchHit] = []
        per_project = max(1, limit // len(self.PROJECTS) + 1)
        headers = {"User-Agent": PROJECT_UA, "Api-User-Agent": PROJECT_UA}

        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True, headers=headers) as client:
            for host in self.PROJECTS:
                try:
                    response = client.get(
                        f"https://{host}/w/api.php",
                        params={
                            "action": "query",
                            "list": "search",
                            "srsearch": query,
                            "srlimit": per_project,
                            "format": "json",
                        },
                    )
                    if response.status_code == 403:
                        self._block("403 from the MediaWiki API")
                        return hits
                    response.raise_for_status()
                    results = response.json().get("query", {}).get("search", [])
                except (httpx.HTTPError, ValueError) as exc:
                    logger.debug("%s: %s failed (%s)", self.name, host, str(exc)[:80])
                    continue

                for item in results:
                    title = item.get("title", "")
                    if not title:
                        continue
                    hits.append(
                        SearchHit(
                            url=f"https://{host}/wiki/{quote(title.replace(' ', '_'))}",
                            title=title,
                            snippet=_plain(item.get("snippet", "")),
                            source=self.name,
                        )
                    )
        return hits[:limit]


class DuckDuckGoLite(Source):
    """The open web, best-effort.

    This is where tutorials, exercise sets and course pages actually live, so it
    is worth having -- but it is scraped rather than offered, and it answers HTTP
    202 with an empty challenge page once it decides you are automated. That is
    treated as "stand down", not as "no results", so a blocked engine does not
    quietly look like a topic with nothing written about it.
    """

    name = "duckduckgo"
    min_interval = 2.5
    ENDPOINT = "https://lite.duckduckgo.com/lite/"

    def _search(self, query: str, limit: int) -> list[SearchHit]:
        with httpx.Client(
            timeout=SEARCH_TIMEOUT, follow_redirects=True, headers={"User-Agent": BROWSER_UA}
        ) as client:
            response = client.post(self.ENDPOINT, data={"q": query})

        if response.status_code == 202 or (
            response.status_code == 200 and not _RESULT_RE.search(response.text)
        ):
            self._block(f"HTTP {response.status_code} with no results")
            return []
        response.raise_for_status()

        body = response.text
        snippets = [_plain(m.group("text")) for m in _SNIPPET_RE.finditer(body)]
        hits: list[SearchHit] = []
        for index, match in enumerate(_RESULT_RE.finditer(body)):
            url = match.group("url")
            if _NON_CONTENT_HOST_RE.match(urlparse(url).netloc):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=_plain(match.group("title")),
                    snippet=snippets[index] if index < len(snippets) else "",
                    source=self.name,
                )
            )
            if len(hits) >= limit:
                break
        return hits


# Order is priority. Wikimedia first because it can be relied on; the open web
# second because it is richer when it is available.
SOURCES: list[Source] = [Wikimedia(), DuckDuckGoLite()]


def source_health() -> list[dict[str, object]]:
    """What each source is currently doing. Surfaced on /health."""
    return [{"name": s.name, "healthy": s.healthy()} for s in SOURCES]


def search(query: str, *, limit: int = 10) -> list[SearchHit]:
    """Query every healthy source in priority order and merge, keeping order."""
    seen: set[str] = set()
    merged: list[SearchHit] = []
    for source in SOURCES:
        for hit in source.search(query, limit):
            if hit.url in seen:
                continue
            seen.add(hit.url)
            merged.append(hit)
    logger.debug("search %r -> %d hits", query[:60], len(merged))
    return merged


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def _metadata(body: str) -> dict[str, str]:
    """Every meta name/property in the document, keys lowercased."""
    found: dict[str, str] = {}
    for pattern in (_META_RE, _META_ALT_RE):
        for match in pattern.finditer(body):
            key = match.group("key").strip().lower()
            if key not in found:
                found[key] = _plain(match.group("value"))
    return found


def _first_prose(body: str) -> str:
    """The first paragraph of the document that reads like prose.

    The fallback used to be "the first 400 characters a reader sees", which on
    a page with no meta description is the site's furniture. A Wikibooks page
    described itself to learners as "Jump to content Main menu Main page..."
    because that is genuinely the first visible text.

    Asking the document's own structure is both better and more general than a
    list of navigation phrases to strip: chrome lives in headers and menus,
    while the thing the page is about is in its first substantial paragraph.
    """
    for match in _PARAGRAPH_RE.finditer(body):
        candidate = _REF_RE.sub("", _TEMPLATE_RE.sub(" ", _plain(match.group(1))))
        candidate = _SPACE_RE.sub(" ", candidate).strip()
        if _RESIDUE_RE.search(candidate):
            continue
        if len(candidate.split()) >= MIN_PROSE_WORDS:
            return candidate[:600]
    return ""


def _describe(body: str, meta: dict[str, str]) -> str:
    """The page's own description, preferring OpenGraph over the meta tag."""
    for key in ("og:description", "description", "twitter:description"):
        value = meta.get(key, "").strip()
        if len(value) >= 40:
            return value[:600]
    return _first_prose(_SCRIPT_RE.sub(" ", body)) or _visible(body, 400).strip()


def _classify_format(body: str, meta: dict[str, str]) -> str:
    """Decide the format from what the document contains, not from its host."""
    if _VIDEO_META_RE.search(meta.get("og:type", "")) or meta.get("og:video"):
        return "video"
    if _VIDEO_TAG_RE.search(body) or _PLAYER_RE.search(body):
        return "video"
    if _INTERACTIVE_RE.search(_visible(body, 20_000)):
        return "interactive"
    return "text"


def _classify_cost(body: str) -> str:
    """``paid`` only when pricing and commerce language appear together."""
    visible = _visible(body, 40_000)
    if _PRICE_RE.search(visible) and _COMMERCE_RE.search(visible):
        return "paid"
    return "free"


def _language(body: str, meta: dict[str, str]) -> str:
    match = _LANG_RE.search(body)
    tag = match.group(1) if match else meta.get("og:locale", "en")
    return (tag.split("-")[0].split("_")[0].lower() or "en")[:5]


def _provider(url: str) -> str:
    """The registrable domain that served the bytes."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return _extract_domain(host).top_domain_under_public_suffix or host


def verify(url: str) -> VerifiedPage | None:
    """Fetch a URL and describe it from the response. None if it does not serve."""
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": user_agent_for(url), "Accept-Language": "en"},
        ) as client:
            response = client.get(url)
            if response.status_code >= 400:
                logger.debug("discarded %s (HTTP %d)", url[:80], response.status_code)
                return None
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                logger.debug("discarded %s (content-type %r)", url[:80], content_type)
                return None
            body = response.text[:MAX_BYTES]
            final_url = str(response.url)
            status = response.status_code
    except (httpx.HTTPError, UnicodeDecodeError) as exc:
        logger.debug("discarded %s (%s)", url[:80], str(exc)[:80])
        return None

    readable = _visible(body, 200_000)
    if len(readable) < MIN_CONTENT_CHARS:
        # A bot wall or an unrendered single-page app. Both answer 200.
        logger.debug("discarded %s (only %d chars of text)", url[:80], len(readable))
        return None

    meta = _metadata(body)
    title_match = _TITLE_RE.search(body)
    title = (meta.get("og:title") or (_plain(title_match.group(1)) if title_match else "")).strip()
    if len(title) < 4:
        logger.debug("discarded %s (no usable title)", url[:80])
        return None

    return VerifiedPage(
        url=url,
        final_url=final_url,
        title=title[:180],
        description=_describe(body, meta),
        provider=_provider(final_url),
        format=_classify_format(body, meta),
        cost=_classify_cost(body),
        language=_language(body, meta),
        status=status,
        word_count=len(readable.split()),
    )


def verify_all(urls: list[str]) -> list[VerifiedPage]:
    """Verify many URLs concurrently, preserving order and dropping failures."""
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        pages = list(pool.map(verify, urls))
    return [page for page in pages if page is not None]


def rank_by_relevance(query: str, pages: list[VerifiedPage]) -> list[tuple[VerifiedPage, float]]:
    """Order pages by how well they actually match the query.

    A search engine ranks by its own signals, and for an encyclopaedia those
    signals produce "Virtues/Moral Integration" for "integration by parts". The
    local embedder is already loaded and already the thing that decides which
    resource covers which skill, so it decides here too -- one consistent notion
    of relevance rather than trusting whatever each source happened to return.

    Falls back to the source order if embedding is unavailable for any reason;
    a worse ordering is better than no resources.
    """
    if not pages:
        return []
    try:
        from app.core.embeddings import get_embedder

        embedder = get_embedder()
        vectors = embedder.embed_batch(
            [query] + [f"{p.title}. {p.description}" for p in pages]
        )
    except Exception as exc:  # noqa: BLE001 -- ranking must never break discovery
        logger.warning("relevance ranking unavailable (%s) -- using source order", str(exc)[:100])
        return [(page, 0.0) for page in pages]

    query_vector = vectors[0]
    scored = [
        (page, float(sum(query_vector * vectors[index + 1])))
        for index, page in enumerate(pages)
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0].final_url))
    return scored


def find_resources(query: str, *, want: int = 3, search_limit: int = 9) -> list[VerifiedPage]:
    """Search, verify, then keep the ``want`` most relevant pages that served.

    Provider diversity is a preference here, not a rule. Preferring different
    sites gives a learner independent explanations; enforcing it absolutely once
    produced a page about moral integration for a calculus skill, because the
    rule insisted on taking something from a third site.
    """
    hits = search(query, limit=search_limit)
    if not hits:
        logger.info("search %r: no source returned anything", query[:60])
        return []

    verified = verify_all([hit.url for hit in hits])
    ranked = rank_by_relevance(query, verified)
    if not ranked:
        return []

    best = ranked[0][1]
    # Relative, for the same reason the catalogue's filter is relative: absolute
    # cosine is not comparable across embedding models, but the gap within one
    # result set is.
    in_contention = [(page, score) for page, score in ranked if best - score <= RELEVANCE_MARGIN]

    kept: list[VerifiedPage] = []
    seen: set[str] = set()
    for page, _ in in_contention:
        if page.provider in seen:
            continue
        seen.add(page.provider)
        kept.append(page)
        if len(kept) >= want:
            break
    # Only if diversity left us short do we allow a second page from a site.
    if len(kept) < want:
        for page, _ in in_contention:
            if page not in kept:
                kept.append(page)
            if len(kept) >= want:
                break

    logger.info(
        "search %r: %d hits, %d served, %d relevant, %d kept",
        query[:60], len(hits), len(verified), len(in_contention), len(kept),
    )
    return kept
