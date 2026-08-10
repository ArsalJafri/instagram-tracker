from __future__ import annotations

import pytest

from instagram_tracker.sources import StorySourceError
from instagram_tracker.sources.igexport import IGExportStorySource


def test_parses_fixture_into_stories(igexport_payload):
    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")

    assert len(stories) == 9
    assert all(story.username == "zero2sudo" for story in stories)
    assert stories == sorted(stories, key=lambda s: s.posted_at)


def test_extracts_the_job_link_from_the_fixture(igexport_payload):
    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")
    with_links = [story for story in stories if story.links]

    assert len(with_links) == 1
    story = with_links[0]
    assert story.story_id == "3960059145617903970"
    assert story.links[0].canonical_url == (
        "https://sonypicturesjobs.com/job/-/-/22978/98897894576"
    )
    assert story.links[0].original_url.startswith("https://l.instagram.com/?u=")


def test_posted_at_comes_from_taken_at(igexport_payload):
    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")
    assert stories[0].posted_at.isoformat() == "2026-08-09T17:17:36+00:00"


def test_missing_items_is_an_error():
    with pytest.raises(StorySourceError):
        IGExportStorySource.parse({"data": {}}, "zero2sudo")


def test_malformed_items_are_skipped_not_fatal():
    payload = {"data": {"items": [{"no_id": True}, "junk", {"id": 7, "taken_at": 1700000000}]}}
    stories = IGExportStorySource.parse(payload, "zero2sudo")
    assert [story.story_id for story in stories] == ["7"]
