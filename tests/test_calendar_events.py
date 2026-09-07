"""Tests for the calendar platform.

Three defects are covered here, all of them visible in the Home Assistant
calendar panel: the requested date range was ignored, every exported event
carried the same uid, and the entity reported the lesson *after* the current
one rather than the one in progress.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.pronote.calendar import (
    PronoteCalendar,
    async_get_calendar_event_from_lessons,
)

TZ = "Europe/Paris"


def _lesson(lesson_id, start, end, *, subject="ANGLAIS LV1", canceled=False,
            classroom="2.2", teacher="DAUTRAIX L."):
    """A stand-in for `pronotepy.Lesson`.

    pronotepy resolves every field in `__init__`, so a plain object with the
    same attributes behaves identically for everything the calendar reads.
    """
    return SimpleNamespace(
        id=lesson_id,
        start=start,
        end=end,
        subject=SimpleNamespace(name=subject),
        canceled=canceled,
        classroom=classroom,
        teacher_name=teacher,
        detention=False,
    )


def _day(hour, minute=0, day=7):
    """A naive datetime, which is what Pronote sends."""
    return datetime(2026, 9, day, hour, minute)


class _Calendar:
    """Binds the real `async_get_events` to a minimal coordinator stub.

    Constructing the entity itself would pull in the device registry and a
    config entry for no benefit: the method under test reads exactly one key.
    """

    async_get_events = PronoteCalendar.async_get_events

    def __init__(self, lessons):
        self.coordinator = SimpleNamespace(data={"lessons_period": lessons})


class TestCalendarEventFromLesson:
    def test_uid_is_the_pronote_lesson_id(self):
        event = async_get_calendar_event_from_lessons(
            _lesson("29#YPsrILbDk56N", _day(9, 30), _day(10, 30)), TZ
        )
        assert event.uid == "29#YPsrILbDk56N"

    def test_each_lesson_gets_a_distinct_uid(self):
        """Issue #77: without this every VEVENT shared one uid.

        An ICS consumer treats same-uid entries as one recurring event and
        keeps only the last, so an exported timetable collapsed to a single
        lesson.
        """
        lessons = [
            _lesson("29#aaa", _day(9, 30), _day(10, 30)),
            _lesson("29#bbb", _day(10, 30), _day(11, 30)),
            _lesson("29#ccc", _day(14, 0), _day(15, 0)),
        ]
        uids = [
            async_get_calendar_event_from_lessons(lesson, TZ).uid
            for lesson in lessons
        ]
        assert len(set(uids)) == len(uids)

    def test_both_ends_are_timezone_aware(self):
        """Home Assistant's calendar schema rejects a naive datetime."""
        event = async_get_calendar_event_from_lessons(
            _lesson("29#aaa", _day(9, 30), _day(10, 30)), TZ
        )
        assert event.start.tzinfo is not None
        assert event.end.tzinfo is not None
        assert event.start.utcoffset() == event.end.utcoffset()

    def test_the_wall_clock_time_is_preserved(self):
        """The school's 09:30 must stay 09:30, not shift by an offset."""
        event = async_get_calendar_event_from_lessons(
            _lesson("29#aaa", _day(9, 30), _day(10, 30)), TZ
        )
        assert (event.start.hour, event.start.minute) == (9, 30)
        assert (event.end.hour, event.end.minute) == (10, 30)

    def test_a_cancelled_lesson_is_marked_in_the_summary(self):
        event = async_get_calendar_event_from_lessons(
            _lesson("29#aaa", _day(9, 30), _day(10, 30), canceled=True), TZ
        )
        assert event.summary.startswith("Annulé - ")


class TestAsyncGetEvents:
    """`async_get_events` builds its events in Home Assistant's timezone.

    The `hass` test fixture defaults to US/Pacific, so the timezone is pinned
    here: the windows a caller passes and the events the entity returns have
    to be read on the same clock, which is the whole point of the fix.
    """

    @pytest.fixture(autouse=True)
    async def _school_timezone(self, hass):
        await hass.config.async_set_time_zone(TZ)

    async def test_only_the_requested_day_is_returned(self, hass):
        """The range was ignored: every held lesson came back, always."""
        calendar = _Calendar(
            [
                _lesson("29#mon", _day(9, 30, day=7), _day(10, 30, day=7)),
                _lesson("29#tue", _day(9, 30, day=8), _day(10, 30, day=8)),
                _lesson("29#wed", _day(9, 30, day=9), _day(10, 30, day=9)),
            ]
        )
        tz = dt_util.get_time_zone(TZ)
        events = await calendar.async_get_events(
            hass,
            datetime(2026, 9, 8, 0, 0, tzinfo=tz),
            datetime(2026, 9, 9, 0, 0, tzinfo=tz),
        )
        assert [event.uid for event in events] == ["29#tue"]

    async def test_a_lesson_straddling_the_boundary_belongs_to_both(self, hass):
        """Overlap, not containment.

        A lesson running across midnight - or across whatever boundary the
        caller picked - is part of both windows. Containment would drop it
        from both.
        """
        calendar = _Calendar(
            [_lesson("29#late", _day(23, 30, day=7), _day(0, 30, day=8))]
        )
        tz = dt_util.get_time_zone(TZ)

        for start_day, end_day in ((7, 8), (8, 9)):
            events = await calendar.async_get_events(
                hass,
                datetime(2026, 9, start_day, 0, 0, tzinfo=tz),
                datetime(2026, 9, end_day, 0, 0, tzinfo=tz),
            )
            assert [event.uid for event in events] == ["29#late"], (start_day, end_day)

    async def test_a_lesson_touching_the_edge_is_excluded(self, hass):
        """The window is half-open: a lesson ending exactly at the start is out."""
        calendar = _Calendar(
            [_lesson("29#before", _day(7, 30, day=8), _day(8, 0, day=8))]
        )
        tz = dt_util.get_time_zone(TZ)
        events = await calendar.async_get_events(
            hass,
            datetime(2026, 9, 8, 8, 0, tzinfo=tz),
            datetime(2026, 9, 8, 18, 0, tzinfo=tz),
        )
        assert events == []

    async def test_cancelled_lessons_are_not_exported(self, hass):
        calendar = _Calendar(
            [
                _lesson("29#kept", _day(9, 30), _day(10, 30)),
                _lesson("29#gone", _day(10, 30), _day(11, 30), canceled=True),
            ]
        )
        tz = dt_util.get_time_zone(TZ)
        events = await calendar.async_get_events(
            hass,
            datetime(2026, 9, 7, 0, 0, tzinfo=tz),
            datetime(2026, 9, 8, 0, 0, tzinfo=tz),
        )
        assert [event.uid for event in events] == ["29#kept"]

    @pytest.mark.parametrize("held", [None, []])
    async def test_no_lesson_data_gives_an_empty_list(self, hass, held):
        """The key is None while the lesson fetch is failing.

        The calendar component calls this without consulting `available`, so
        opening the panel during an outage used to raise `TypeError` at the
        websocket rather than showing an empty day.
        """
        calendar = _Calendar(held)
        tz = dt_util.get_time_zone(TZ)
        events = await calendar.async_get_events(
            hass,
            datetime(2026, 9, 7, 0, 0, tzinfo=tz),
            datetime(2026, 9, 8, 0, 0, tzinfo=tz),
        )
        assert events == []
