from __future__ import annotations

import json

from instagram_tracker.models import Classification, Job, RoleType
from instagram_tracker.notifier import DiscordNotifier, allowed_mentions, build_payload

ROLE_PING = "<@&987654321>"
INTERN_PING = "<@&111222333>"


def job(role_type=RoleType.NEW_GRAD):
    return Job(
        title="Software Engineer, New Grad",
        company="Acme",
        location="Seattle, WA",
        classification=Classification.RELEVANT,
        url="https://boards.greenhouse.io/acme/jobs/1",
        role_type=role_type,
    )


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self):
        return None


# -- payload -------------------------------------------------------------


def test_the_mention_goes_in_content_not_the_embed():
    # Mentions inside an embed render as text and notify nobody.
    payload = build_payload(job(), "zero2sudo", ROLE_PING)

    assert payload["content"].startswith(ROLE_PING)
    assert ROLE_PING not in json.dumps(payload["embeds"])


def test_the_headline_survives_the_mention():
    payload = build_payload(job(), "zero2sudo", ROLE_PING)
    assert "New entry-level software role" in payload["content"]


def test_no_mention_leaves_the_content_unchanged():
    payload = build_payload(job(), "zero2sudo", "")
    assert payload["content"] == "New entry-level software role"


# -- allowed_mentions ----------------------------------------------------


def test_without_mentions_the_message_may_ping_nothing():
    assert allowed_mentions("") == {"parse": []}
    assert allowed_mentions("   ") == {"parse": []}


def test_users_and_roles_are_permitted_when_configured():
    assert allowed_mentions(ROLE_PING) == {"parse": ["users", "roles"]}


def test_everyone_stays_locked_unless_explicitly_requested():
    # A stray character in configuration must not be able to mass-notify a server.
    assert "everyone" not in allowed_mentions("<@&123> <@456>")["parse"]


def test_here_and_everyone_unlock_the_broadcast_ping():
    assert "everyone" in allowed_mentions("@here")["parse"]
    assert "everyone" in allowed_mentions("@everyone")["parse"]


def test_the_payload_always_carries_allowed_mentions():
    assert "allowed_mentions" in build_payload(job(), "z", "")
    assert "allowed_mentions" in build_payload(job(), "z", ROLE_PING)


# -- routing -------------------------------------------------------------


def test_each_role_type_uses_its_own_mention():
    notifier = DiscordNotifier(
        "https://main.test",
        "https://intern.test",
        mentions=ROLE_PING,
        internship_mentions=INTERN_PING,
    )

    assert notifier.mentions_for(job(RoleType.NEW_GRAD)) == ROLE_PING
    assert notifier.mentions_for(job(RoleType.INTERNSHIP)) == INTERN_PING


def test_mentions_do_not_fall_back_between_role_types():
    # Pinging the wrong group is worse than pinging nobody.
    notifier = DiscordNotifier("https://main.test", mentions=ROLE_PING)
    assert notifier.mentions_for(job(RoleType.INTERNSHIP)) == ""


def test_the_sent_message_carries_the_right_mention():
    session = FakeSession()
    notifier = DiscordNotifier(
        "https://main.test",
        "https://intern.test",
        mentions=ROLE_PING,
        internship_mentions=INTERN_PING,
        session=session,
    )

    notifier.notify(job(RoleType.INTERNSHIP), "zero2sudo")
    url, payload = session.calls[0]

    assert url == "https://intern.test"
    assert payload["content"].startswith(INTERN_PING)
    assert payload["allowed_mentions"] == {"parse": ["users", "roles"]}


def test_an_unconfigured_notifier_still_sends_silently():
    session = FakeSession()
    notifier = DiscordNotifier("https://main.test", session=session)

    assert notifier.notify(job(), "zero2sudo") is True
    assert session.calls[0][1]["allowed_mentions"] == {"parse": []}
