"""Shared fixtures.

`pytest_homeassistant_custom_component` is a pytest plugin, so the Home
Assistant fixtures are available without importing anything here.
"""

from datetime import date, datetime, time
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load `custom_components/pronote` during tests."""
    return


def _subject(name):
    return SimpleNamespace(name=name)


@pytest.fixture
def make_lesson():
    """Build a stand-in for `pronotepy.Lesson`."""

    def _make(**overrides):
        fields = {
            "id": "lesson-1",
            "start": datetime(2026, 9, 7, 9, 30),
            "end": datetime(2026, 9, 7, 10, 30),
            "subject": _subject("ANGLAIS LV1"),
            "classroom": "2.2",
            "canceled": False,
            "status": None,
            "background_color": "#4A90D9",
            "teacher_name": "DAUTRAIX L.",
            "teacher_names": ["DAUTRAIX L."],
            "classrooms": ["2.2"],
            "outing": False,
            "memo": None,
            "group_name": None,
            "group_names": [],
            "exempted": False,
            "virtual_classrooms": [],
            "num": 3,
            "detention": False,
            "test": False,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    return _make


@pytest.fixture
def lunch_break_time():
    """The default lunch break, as `const.DEFAULT_LUNCH_BREAK_TIME` parses."""
    return time(13, 0)


@pytest.fixture
def make_homework():
    """Build a stand-in for `pronotepy.Homework`."""

    def _make(**overrides):
        fields = {
            "date": date(2026, 9, 8),
            "subject": _subject("MATHEMATIQUES"),
            "description": "Exercices 1 a 12 page 42",
            "done": False,
            "background_color": "#B81C1C",
            "files": [],
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    return _make


@pytest.fixture
def make_attachment():
    """Build a stand-in for `pronotepy.Attachment`."""

    def _make(**overrides):
        fields = {
            "name": "enonce.pdf",
            "url": "https://example.invalid/enonce.pdf",
            "type": 1,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    return _make


@pytest.fixture
def make_period():
    """Build a stand-in for `pronotepy.Period`."""

    def _make(**overrides):
        fields = {
            "name": "Trimestre 1",
            "start": datetime(2026, 9, 1, 0, 0),
            "end": datetime(2026, 12, 5, 0, 0),
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    return _make
