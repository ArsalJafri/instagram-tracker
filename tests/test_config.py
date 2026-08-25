"""Environment loading, and the 2026-08-24 setting rename.

`Config.from_env` had no coverage at all before this, which is why renaming three
settings broke nothing in the suite while being perfectly capable of breaking
production. The rename is the reason the gap was noticed; the tests are worth keeping
regardless of it.
"""

from __future__ import annotations

import pytest

from instagram_tracker.config import Config, deprecated_settings_in_use

NEW_GRAD = "https://discord.test/new-grad"
REVIEW = "https://discord.test/review"
LEGACY = "https://discord.test/legacy"

ALL_SETTINGS = (
    "DISCORD_NEW_GRAD_WEBHOOK_URL",
    "DISCORD_NEW_GRAD_MENTIONS",
    "DISCORD_REVIEW_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_MENTIONS",
    "DISCORD_UNKNOWN_WEBHOOK_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """A developer's own .env must not decide what these tests observe."""
    monkeypatch.setattr("instagram_tracker.config.load_dotenv", lambda *a, **k: None)
    for name in ALL_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def test_the_current_names_are_read(monkeypatch):
    monkeypatch.setenv("DISCORD_NEW_GRAD_WEBHOOK_URL", NEW_GRAD)
    monkeypatch.setenv("DISCORD_REVIEW_WEBHOOK_URL", REVIEW)
    monkeypatch.setenv("DISCORD_NEW_GRAD_MENTIONS", "<@&1>")

    config = Config.from_env()

    assert config.discord_webhook_url == NEW_GRAD
    assert config.discord_unknown_webhook_url == REVIEW
    assert config.discord_mentions == "<@&1>"
    assert deprecated_settings_in_use() == []


def test_the_old_names_still_work(monkeypatch):
    """Deploying the rename before migrating the host must not stop every alert."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", LEGACY)
    monkeypatch.setenv("DISCORD_UNKNOWN_WEBHOOK_URL", REVIEW)
    monkeypatch.setenv("DISCORD_MENTIONS", "<@&2>")

    config = Config.from_env()

    assert config.discord_webhook_url == LEGACY
    assert config.discord_unknown_webhook_url == REVIEW
    assert config.discord_mentions == "<@&2>"


def test_the_current_name_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("DISCORD_NEW_GRAD_WEBHOOK_URL", NEW_GRAD)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", LEGACY)

    assert Config.from_env().discord_webhook_url == NEW_GRAD


def test_an_empty_current_name_falls_through_rather_than_winning(monkeypatch):
    """A blank var is how Render represents 'declared but unset', not a real value."""
    monkeypatch.setenv("DISCORD_NEW_GRAD_WEBHOOK_URL", "")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", LEGACY)

    assert Config.from_env().discord_webhook_url == LEGACY


def test_a_legacy_name_in_use_is_reported(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", LEGACY)
    monkeypatch.setenv("DISCORD_MENTIONS", "<@&2>")

    assert sorted(deprecated_settings_in_use()) == [
        ("DISCORD_MENTIONS", "DISCORD_NEW_GRAD_MENTIONS"),
        ("DISCORD_WEBHOOK_URL", "DISCORD_NEW_GRAD_WEBHOOK_URL"),
    ]


def test_a_migrated_setting_is_not_reported(monkeypatch):
    """Both set is the state during a migration, and is not worth warning about."""
    monkeypatch.setenv("DISCORD_NEW_GRAD_WEBHOOK_URL", NEW_GRAD)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", LEGACY)

    assert deprecated_settings_in_use() == []


def test_settings_that_were_not_renamed_are_untouched(monkeypatch):
    monkeypatch.setenv("DISCORD_INTERNSHIP_WEBHOOK_URL", "https://discord.test/intern")
    monkeypatch.setenv("DISCORD_INTERNSHIP_MENTIONS", "<@&3>")

    config = Config.from_env()

    assert config.discord_internship_webhook_url == "https://discord.test/intern"
    assert config.discord_internship_mentions == "<@&3>"
