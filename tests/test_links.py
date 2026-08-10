from __future__ import annotations

from instagram_tracker.links import canonicalize, extract_links, normalize, unwrap_redirect


def test_unwraps_instagram_redirect():
    wrapped = "https://l.instagram.com/?u=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F1&e=ABC"
    assert unwrap_redirect(wrapped) == "https://boards.greenhouse.io/acme/jobs/1"


def test_leaves_plain_urls_alone():
    url = "https://jobs.lever.co/acme/123"
    assert unwrap_redirect(url) == url


def test_redirect_without_u_parameter_is_returned_unchanged():
    url = "https://l.instagram.com/?x=1"
    assert unwrap_redirect(url) == url


def test_normalize_strips_tracking_and_www():
    url = "http://WWW.Example.com/jobs/1/?fbclid=abc&utm_source=ig&ref=story"
    assert normalize(url) == "https://example.com/jobs/1?ref=story"


def test_normalize_rejects_non_urls():
    assert normalize("example.com/jobs/1") == ""


def test_canonicalize_unwraps_then_normalizes():
    wrapped = (
        "https://l.instagram.com/?u=https%3A%2F%2Fwww.sonypicturesjobs.com%2Fjob%2F-%2F-%2F22978"
        "%2F98897894576%3Ffbclid%3Dxyz&e=AUA"
    )
    assert canonicalize(wrapped) == "https://sonypicturesjobs.com/job/-/-/22978/98897894576"


def test_extract_links_reads_story_link_stickers():
    item = {
        "story_link_stickers": [
            {"story_link": {"url": "https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fa"}},
            {"story_link": {"display_url": "example.com/b"}},
        ]
    }
    links = extract_links(item)
    assert [link.canonical_url for link in links] == ["https://example.com/a"]


def test_extract_links_deduplicates_within_a_story():
    item = {
        "story_link_stickers": [
            {"story_link": {"url": "https://example.com/a?fbclid=1"}},
            {"story_link": {"url": "https://www.example.com/a/?utm_source=ig"}},
        ]
    }
    assert len(extract_links(item)) == 1


def test_story_without_links_yields_nothing():
    assert extract_links({"story_link_stickers": []}) == []
    assert extract_links({}) == []
