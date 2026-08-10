from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def igexport_payload() -> dict:
    with open(FIXTURES / "igexport_zero2sudo.json") as handle:
        return json.load(handle)


@pytest.fixture
def web_profile_payload() -> dict:
    with open(FIXTURES / "instagram_web_profile_info.json") as handle:
        return json.load(handle)
