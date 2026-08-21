"""Discovery: parsing, verification, and the rules that keep junk out.

Every test here is offline. The network-facing methods are the seam, and they
are replaced with recorded responses -- including the two that caused real bugs:
DuckDuckGo's HTTP 202 challenge, and Khan Academy's HTTP 200 bot wall.
"""

from __future__ import annotations

import httpx
import pytest

from app.core import websearch


# --------------------------------------------------------------------------- #
# Page classification, from the document rather than the host
# --------------------------------------------------------------------------- #
def _page(body: str, *, url: str = "https://example.org/a") -> str:
    """Enough surrounding text to clear the content gate."""
    filler = "word " * (websearch.MIN_CONTENT_CHARS // 4)
    return f"<html lang='en'><head><title>A Real Page Title</title></head><body>{body}{filler}</body></html>"


def test_video_is_detected_from_the_document_not_a_list_of_hosts() -> None:
    assert websearch._classify_format("<video src='x'>", {}) == "video"
    assert websearch._classify_format("", {"og:type": "video.other"}) == "video"
    assert websearch._classify_format("<iframe src='https://youtube.com/embed/x'>", {}) == "video"


def test_plain_prose_is_text() -> None:
    assert websearch._classify_format("<p>Some explanation of a topic.</p>", {}) == "text"


def test_interactive_is_detected_from_what_the_page_offers() -> None:
    assert websearch._classify_format("<p>Try it yourself in the playground</p>", {}) == "interactive"


def test_pricing_alone_does_not_make_a_page_paid() -> None:
    """An article about e-commerce mentions prices; that is not a paywall."""
    assert websearch._classify_cost("<p>The item cost $40 in 1998.</p>") == "free"


def test_pricing_with_commerce_language_makes_a_page_paid() -> None:
    assert websearch._classify_cost("<p>Subscribe for $9 per month</p>") == "paid"


def test_provider_is_the_registrable_domain() -> None:
    """Subdomains of one site are one provider; two universities are not one."""
    assert websearch._provider("https://en.khanacademy.org/x") == "khanacademy.org"
    assert websearch._provider("https://www.khanacademy.org/y") == "khanacademy.org"
    assert websearch._provider("https://iupac.qmul.ac.uk/z") == "qmul.ac.uk"


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def _mock_transport(monkeypatch, handler) -> None:
    """Point every httpx.Client in the module at a recorded handler."""
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(websearch.httpx, "Client", factory)


def test_a_bot_wall_answering_200_is_discarded(monkeypatch) -> None:
    """Khan Academy returns 200 with "Client Challenge" and 228 characters.

    Treating that as a learning resource is how a plan fills with pages that
    teach nothing, so the content gate exists and this is the case it exists for.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Client Challenge</title></head>"
                 "<body>A required part of this site could not load.</body></html>",
        )

    _mock_transport(monkeypatch, handler)
    assert websearch.verify("https://www.khanacademy.org/anything") is None


def test_a_real_page_is_described_from_its_own_response(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=_page("<meta property='og:description' content='"
                       + "A thorough explanation of the subject at hand for learners." * 2
                       + "'><p>Body text.</p>"),
        )

    _mock_transport(monkeypatch, handler)
    page = websearch.verify("https://example.org/article")
    assert page is not None
    assert page.title == "A Real Page Title"
    assert page.provider == "example.org"
    assert page.word_count > 100
    assert "explanation" in page.description


def test_a_404_is_discarded(monkeypatch) -> None:
    _mock_transport(monkeypatch, lambda request: httpx.Response(404, text="gone"))
    assert websearch.verify("https://example.org/missing") is None


def test_a_non_html_response_is_discarded(monkeypatch) -> None:
    _mock_transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG"),
    )
    assert websearch.verify("https://example.org/image.png") is None


def test_a_connection_failure_is_discarded_not_raised(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _mock_transport(monkeypatch, handler)
    assert websearch.verify("https://example.org/dead") is None


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def test_duckduckgo_treats_a_202_challenge_as_stand_down_not_as_no_results(monkeypatch) -> None:
    """The bug this cost: a blocked engine looked like a topic nobody has written about."""
    source = websearch.DuckDuckGoLite()
    _mock_transport(monkeypatch, lambda request: httpx.Response(202, text="<html>challenge</html>"))

    assert source.search("anything", 5) == []
    assert not source.healthy()


def test_wikimedia_query_drops_words_that_only_help_a_web_search() -> None:
    """A wiki has no page called "tutorial"; the word only adds noise."""
    assert websearch.Wikimedia().prepare("integration by parts practice") == "integration by parts"
    assert websearch.Wikimedia().prepare("quantum gates tutorial") == "quantum gates"


def test_query_shaping_keeps_words_that_merely_contain_a_stopword() -> None:
    assert "coursework" in websearch.Wikimedia().prepare("coursework quantum gates")


def test_an_unhealthy_source_is_skipped_without_a_request() -> None:
    source = websearch.DuckDuckGoLite()
    source._block("test")
    assert source.search("anything", 5) == []


def test_search_merges_sources_and_drops_duplicate_urls(monkeypatch) -> None:
    class Fake(websearch.Source):
        def __init__(self, name, urls):
            super().__init__()
            self.name = name
            self.urls = urls

        def _search(self, query, limit):
            return [websearch.SearchHit(url=u, title=u, snippet="", source=self.name) for u in self.urls]

    monkeypatch.setattr(
        websearch,
        "SOURCES",
        [Fake("a", ["https://x.org/1", "https://y.org/2"]), Fake("b", ["https://y.org/2", "https://z.org/3"])],
    )
    urls = [hit.url for hit in websearch.search("q")]
    assert urls == ["https://x.org/1", "https://y.org/2", "https://z.org/3"]


def test_relevance_ranking_puts_the_matching_page_first() -> None:
    """The local embedder decides, so one notion of relevance governs everywhere."""
    def page(title: str, url: str) -> websearch.VerifiedPage:
        return websearch.VerifiedPage(
            url=url, final_url=url, title=title, description=title, provider="example.org",
            format="text", cost="free", language="en", status=200, word_count=1000,
        )

    pages = [
        page("Virtues and Moral Integration", "https://a.org/1"),
        page("Integration by parts in calculus", "https://b.org/2"),
    ]
    ranked = websearch.rank_by_relevance("integration by parts practice", pages)
    assert ranked[0][0].url == "https://b.org/2"


# --------------------------------------------------------------------------- #
# Descriptions
# --------------------------------------------------------------------------- #
NAV_PAGE = """
<html><head><title>Music Theory/How to read Music</title></head><body>
<div id="mw-navigation">Jump to content Main menu Main page Help Browse Recent changes</div>
<p>Edit</p>
<p>This Wikibook is here to give the reader an idea of how to read music, starting
from the staff and working up to key signatures and time signatures.</p>
</body></html>
"""


def test_a_description_comes_from_prose_not_from_the_navigation() -> None:
    """The regression: a page described itself as "Jump to content Main menu"."""
    described = websearch._describe(NAV_PAGE, {})
    assert described.startswith("This Wikibook")
    assert "Main menu" not in described


def test_a_short_paragraph_is_not_a_description() -> None:
    """"Edit" is a link, not what the page is about."""
    assert "Edit" != websearch._describe(NAV_PAGE, {})


def test_the_page_s_own_description_still_wins() -> None:
    meta = {"og:description": "A gentle introduction to reading standard musical notation."}
    assert websearch._describe(NAV_PAGE, meta) == meta["og:description"]


def test_a_paragraph_full_of_markup_residue_is_skipped() -> None:
    """Citation blobs survive tag-stripping because they are text, not markup."""
    body = (
        '<html><body><p>Photosynthesis </ref>"}},"i":0}}]}> is a process used by plants '
        "and other organisms to convert light into chemical energy over time.</p>"
        "<p>Some organisms also perform anoxygenic photosynthesis, which does not "
        "produce oxygen but does store energy in chemical bonds.</p></body></html>"
    )
    described = websearch._describe(body, {})
    assert described.startswith("Some organisms")


def test_a_page_with_no_prose_at_all_still_describes_itself() -> None:
    """Degraded, not empty: an entry with no description reads as a bug to a learner."""
    assert websearch._describe("<html><body><div>Sign in Register</div></body></html>", {})
