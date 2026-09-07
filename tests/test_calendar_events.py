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

    def test_the_room_and_the_teacher_are_reported_when_present(self):
        """The ordinary case, pinned so the guard cannot quietly drop them."""
        event = async_get_calendar_event_from_lessons(
            _lesson("L1", _day(8), _day(9)), TZ
        )
        assert event.location == "Salle 2.2"
        assert event.description == "DAUTRAIX L. - Salle 2.2"

    @pytest.mark.parametrize("empty", [None, ""])
    def test_a_lesson_with_no_room_does_not_say_salle_none(self, empty):
        """`classroom` is resolved non-strictly, so study hall arrives as None.

        Interpolated unconditionally it read "Salle None" - in the calendar
        card, in the entity's location attribute, and in every exported
        VEVENT. Pronote saying nothing about the room is not a room called
        None; the field is optional and stays empty.
        """
        event = async_get_calendar_event_from_lessons(
            _lesson("L1", _day(8), _day(9), classroom=empty), TZ
        )
        assert event.location is None
        assert event.description == "DAUTRAIX L."
        assert "None" not in (event.description or "")

    @pytest.mark.parametrize("empty", [None, ""])
    def test_a_lesson_with_no_teacher_keeps_the_room(self, empty):
        """A supply teacher not yet named must not cost the room too.

        The description used to start with the teacher unconditionally, so an
        unnamed one read "None - Salle 2.2".
        """
        event = async_get_calendar_event_from_lessons(
            _lesson("L1", _day(8), _day(9), teacher=empty), TZ
        )
        assert event.location == "Salle 2.2"
        assert event.description == "Salle 2.2"

    def test_a_lesson_with_neither_has_no_description_at_all(self):
        """`""` would still write an empty attribute; None leaves it out."""
        event = async_get_calendar_event_from_lessons(
            _lesson("L1", _day(8), _day(9), classroom=None, teacher=None), TZ
        )
        assert event.location is None
        assert event.description is None

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


class TestEventSelection:
    """`_compute_event` picks the lesson that is running, or the next one.

    Selection is exercised through `_compute_event` rather than through a
    fully constructed entity: it reads the coordinator data and the configured
    timezone, and nothing else.
    """

    @pytest.fixture(autouse=True)
    async def _school_timezone(self, hass):
        await hass.config.async_set_time_zone(TZ)

    @staticmethod
    def _entity(hass, lessons):
        return SimpleNamespace(
            coordinator=SimpleNamespace(data={"lessons_period": lessons}),
            hass=hass,
        )

    def _compute(self, hass, lessons):
        return PronoteCalendar._compute_event(self._entity(hass, lessons))

    async def test_the_running_lesson_wins_over_the_next_one(self, hass, freezer):
        """The defect this branch is named after.

        `event.start >= now` implies `now < event.end`, so the second half of
        the old condition was dead and the selection landed on the lesson
        *after* the one in progress.
        """
        freezer.move_to("2026-09-07T09:47:00+02:00")
        lessons = [
            _lesson("29#now", _day(9, 30), _day(10, 30)),
            _lesson("29#next", _day(10, 30), _day(11, 30), subject="HISTOIRE"),
        ]

        assert self._compute(hass, lessons).uid == "29#now"

    async def test_the_next_lesson_when_none_is_running(self, hass, freezer):
        freezer.move_to("2026-09-07T13:15:00+02:00")
        lessons = [
            _lesson("29#morning", _day(9, 30), _day(10, 30)),
            _lesson("29#afternoon", _day(14, 0), _day(15, 0), subject="MATHS"),
        ]

        assert self._compute(hass, lessons).uid == "29#afternoon"

    async def test_the_changeover_is_immediate(self, hass, freezer):
        """Measured on a live instance: six minutes of `off` mid-lesson.

        At 10:30:00 the calendar's end alarm fires and the state is written.
        Recomputing at that moment has to yield the 10:30 lesson - otherwise
        the entity writes `off`, schedules nothing (`now >= event.end`) and
        stays wrong until the next coordinator refresh.
        """
        lessons = [
            _lesson("29#first", _day(9, 30), _day(10, 30)),
            _lesson("29#second", _day(10, 30), _day(11, 30), subject="HISTOIRE"),
        ]

        freezer.move_to("2026-09-07T10:29:59+02:00")
        assert self._compute(hass, lessons).uid == "29#first"

        freezer.move_to("2026-09-07T10:30:00+02:00")
        assert self._compute(hass, lessons).uid == "29#second"

    async def test_the_earliest_match_wins_whatever_the_input_order(
        self, hass, freezer
    ):
        """pronotepy appends week by week and never sorts.

        So "the first lesson that matches" was not the earliest one, which is
        why this is a `min()` and not a `next()`.
        """
        freezer.move_to("2026-09-07T08:00:00+02:00")
        lessons = [
            _lesson("29#late", _day(14, 0), _day(15, 0), subject="MATHS"),
            _lesson("29#early", _day(9, 30), _day(10, 30)),
            _lesson("29#middle", _day(11, 30), _day(12, 30), subject="FRANCAIS"),
        ]

        assert self._compute(hass, lessons).uid == "29#early"

    async def test_a_cancelled_lesson_is_never_selected(self, hass, freezer):
        """The entity must not contradict the panel.

        `async_get_events` drops cancelled lessons, so announcing one as the
        current event would put the entity at odds with what the calendar
        shows.
        """
        freezer.move_to("2026-09-07T09:47:00+02:00")
        lessons = [
            _lesson("29#cancelled", _day(9, 30), _day(10, 30), canceled=True),
            _lesson("29#real", _day(10, 30), _day(11, 30), subject="HISTOIRE"),
        ]

        assert self._compute(hass, lessons).uid == "29#real"

    async def test_nothing_left_today(self, hass, freezer):
        """After the last lesson the entity is `off` with no event.

        Not a failure - it is what "school is over" looks like.
        """
        freezer.move_to("2026-09-07T18:00:00+02:00")
        lessons = [_lesson("29#done", _day(9, 30), _day(10, 30))]

        assert self._compute(hass, lessons) is None

    @pytest.mark.parametrize("held", [None, []])
    async def test_no_lesson_data(self, hass, freezer, held):
        freezer.move_to("2026-09-07T09:47:00+02:00")
        assert self._compute(hass, held) is None
