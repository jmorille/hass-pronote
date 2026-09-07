"""Tests for the coordinator's failure contract.

`DataUpdateCoordinator` assigns `self.data = await self._async_update_data()`,
so the returned value is the whole contract: return a dict and it becomes the
new state, raise and the previous state is kept untouched. Writing into
`self.data` as the fetch progresses breaks it in the way that is hardest to
notice - the entry stays marked successful while the state is half a refresh
old and half a refresh new.

These tests pin the properties that follow from returning a local dict and
raising `UpdateFailed`, plus the two places where the contract was written
down but not reachable: the current-period guard, and the alarm clock.
"""

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, PropertyMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pronote.const import DOMAIN
from custom_components.pronote.coordinator import PronoteDataUpdateCoordinator

HELPER = "custom_components.pronote.coordinator.get_pronote_client"
NOW = "custom_components.pronote.coordinator.dt_util.now"


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="ENFANT Prenom",
        data={
            "connection_type": "username_password",
            "account_type": "parent",
            "child": "ENFANT Prenom",
            "url": "https://0000000a.index-education.net/pronote/parent.html",
            "username": "parent",
            "password": "hunter2",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(hass, entry):
    return PronoteDataUpdateCoordinator(hass, entry)


def _client_with_session():
    """A client whose HTTP session records whether it was closed."""
    client = MagicMock()
    client.communication.session.close = MagicMock()
    return client


class TestUnusableClient:
    async def test_raises_update_failed(self, coordinator):
        """A login that produced no client is a failed refresh, not an empty one.

        Returning the skeleton dict here would mark the refresh successful and
        publish every sensor as empty - which reads to a user as "the school
        published nothing" rather than "the integration could not log in".
        """
        with patch(HELPER, return_value=None), pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_the_entry_is_marked_unsuccessful(self, coordinator):
        with patch(HELPER, return_value=None):
            await coordinator.async_refresh()

        assert coordinator.last_update_success is False


class TestPreviousDataIsKept:
    async def test_a_failed_refresh_does_not_overwrite_good_data(self, coordinator):
        """The point of returning a local dict rather than filling `self.data`.

        A fetch that dies halfway used to leave the state half-written, and
        `last_update_success` stayed True because nothing raised.
        """
        good = {"account_type": "parent", "grades": ["a good refresh"]}
        coordinator.data = good
        coordinator.last_update_success = True

        with patch(HELPER, return_value=None):
            await coordinator.async_refresh()

        assert coordinator.data is good
        assert coordinator.data["grades"] == ["a good refresh"]

    async def test_a_fetch_that_raises_midway_keeps_the_old_state(self, coordinator):
        """Whatever the failure, the published state stays coherent."""
        good = {"account_type": "parent", "grades": ["a good refresh"]}
        coordinator.data = good

        with (
            patch(HELPER, return_value=_client_with_session()),
            patch.object(
                PronoteDataUpdateCoordinator,
                "_fetch_data",
                side_effect=Exception("Pronote returned something unexpected"),
            ),
        ):
            await coordinator.async_refresh()

        assert coordinator.data is good
        assert coordinator.last_update_success is False


class TestSessionIsAlwaysClosed:
    async def test_closed_after_a_successful_refresh(self, coordinator):
        client = _client_with_session()
        with (
            patch(HELPER, return_value=client),
            patch.object(
                PronoteDataUpdateCoordinator, "_fetch_data", return_value={"ok": True}
            ),
        ):
            assert await coordinator._async_update_data() == {"ok": True}

        client.communication.session.close.assert_called_once()

    async def test_closed_after_a_failed_refresh(self, coordinator):
        """The close lives in a `finally`, so a failure cannot leak the session.

        This is the leak the branch is named after: an exception during the
        fetch skipped the close entirely, and every refresh interval left one
        more `requests` session behind.
        """
        client = _client_with_session()
        with (
            patch(HELPER, return_value=client),
            patch.object(
                PronoteDataUpdateCoordinator, "_fetch_data", side_effect=Exception("x")
            ),
            pytest.raises(Exception, match="x"),
        ):
            await coordinator._async_update_data()

        client.communication.session.close.assert_called_once()

    async def test_a_close_that_fails_does_not_mask_the_refresh(self, coordinator):
        """A broken close must not replace the real error, nor invent one.

        It is reported at debug and swallowed: the refresh result - success or
        the original exception - is what reaches the coordinator.
        """
        client = _client_with_session()
        client.communication.session.close.side_effect = Exception("close refused")

        with (
            patch(HELPER, return_value=client),
            patch.object(
                PronoteDataUpdateCoordinator, "_fetch_data", return_value={"ok": True}
            ),
        ):
            assert await coordinator._async_update_data() == {"ok": True}

    async def test_a_client_without_a_session_is_not_a_failure(self, coordinator):
        """`getattr` chain: not every client exposes `communication.session`."""
        client = MagicMock()
        client.communication = None

        with (
            patch(HELPER, return_value=client),
            patch.object(
                PronoteDataUpdateCoordinator, "_fetch_data", return_value={"ok": True}
            ),
        ):
            assert await coordinator._async_update_data() == {"ok": True}


# --------------------------------------------------------------------------
# A client complete enough to walk _fetch_data from end to end, so that the
# guard under test is what decides the outcome rather than some earlier stub.
# --------------------------------------------------------------------------

TZ = "Europe/Paris"


@pytest.fixture
def no_graph_walk():
    """Skip `_strip_client_refs` for the tests that run the whole fetch.

    The last thing `_fetch_data` does is walk the finished data with `getattr`
    over `__slots__` and `__dict__`, nulling every `_client` it finds. That
    terminates on pronotepy objects, which are a finite graph. It does not
    terminate on a `MagicMock`: `hasattr(obj, "_client")` *manufactures* the
    attribute, so every mock the walk touches grows a fresh child mock, which
    grows one of its own. `_visited` cannot help - each one is a new object
    with a new id. The result is not an error but a hang, and it cost a runner
    twenty minutes before it was understood.

    Nothing here is about that walk, and it is exercised by the tests for the
    branch that introduced it.
    """
    with patch("custom_components.pronote.coordinator._strip_client_refs"):
        yield


def _lesson(start):
    lesson = MagicMock()
    lesson.start = start
    lesson.end = start + timedelta(hours=1)
    lesson.canceled = False
    return lesson


def _period(name="Trimestre 2"):
    period = MagicMock()
    period.name = name
    period.start = datetime(2026, 1, 5)
    period.end = datetime(2026, 4, 3)
    return period


def _full_client(lessons_by_day=None, period_raises=False):
    """Only the attributes the fetch reads as text are pinned.

    Everything else can stay a MagicMock: it iterates empty, which keeps the
    fetch moving without pretending to be Pronote.
    """
    client = _client_with_session()
    client.export_credentials.return_value = {}
    client.password = "1234"
    client.info.name = "ENFANT Prenom"
    client._selected_child.name = "ENFANT Prenom"
    client.periods = []

    if lessons_by_day is None:
        client.lessons.return_value = [_lesson(datetime(2026, 9, 7, 8, 0))]
    else:
        client.lessons.side_effect = lambda *args: lessons_by_day(args[0])

    if period_raises:
        type(client).current_period = PropertyMock(
            side_effect=Exception("Pronote sent no period list")
        )
    else:
        type(client).current_period = PropertyMock(return_value=_period())
    return client


@pytest.mark.usefixtures("no_graph_walk")
class TestCurrentPeriodGuard:
    """The guard existed; seven fetches dereferenced the period before it.

    `data["grades"] = await executor(get_grades, client.current_period)` sat
    some 140 lines above `if raw_current_period is None: raise UpdateFailed`,
    so whichever way the period was missing, the guard never got a turn. A
    property that raises came out as a bare exception - which the coordinator's
    generic handler logs as a full traceback on *every* refresh, rather than
    one ERROR on the transition the way `UpdateFailed` does - and a period of
    `None` reached `get_grades`, whose own `except` clause then failed on
    `period.name`.

    Resolving it once, before the first fetch that needs it, is what makes the
    contract this branch describes the one that actually runs.
    """

    async def test_a_period_that_raises_is_an_update_failure(self, coordinator):
        client = _full_client(period_raises=True)

        with (
            patch(HELPER, return_value=client),
            pytest.raises(UpdateFailed, match="no current period"),
        ):
            await coordinator._async_update_data()

    async def test_no_period_scoped_fetch_is_attempted_first(self, coordinator):
        """What "the guard is reachable" means in practice.

        This is the assertion that fails on the unfixed branch: there
        `get_grades` was reached, the period expression raising as its argument
        was being built.
        """
        client = _full_client(period_raises=True)

        with (
            patch(HELPER, return_value=client),
            patch("custom_components.pronote.coordinator.get_grades") as get_grades,
            patch("custom_components.pronote.coordinator.get_averages") as get_averages,
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()

        get_grades.assert_not_called()
        get_averages.assert_not_called()

    async def test_a_period_of_none_is_an_update_failure(self, coordinator):
        """`None` is not "no grades yet", it is "we could not read the term"."""
        client = _full_client()
        type(client).current_period = PropertyMock(return_value=None)

        with (
            patch(HELPER, return_value=client),
            pytest.raises(UpdateFailed, match="no current period"),
        ):
            await coordinator._async_update_data()

    async def test_the_session_is_still_closed_when_the_guard_fires(self, coordinator):
        """Raising earlier must not step outside the `finally`."""
        client = _full_client(period_raises=True)

        with patch(HELPER, return_value=client), pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        client.communication.session.close.assert_called_once()

    async def test_a_resolved_period_keys_the_sensors_and_is_published(
        self, coordinator
    ):
        client = _full_client()

        with patch(HELPER, return_value=client):
            data = await coordinator._async_update_data()

        assert data["current_period_key"] == "trimestre_2"
        assert data["current_period"].name == "Trimestre 2"
        assert [p.name for p in data["active_periods"]] == ["Trimestre 2"]


@pytest.mark.usefixtures("no_graph_walk")
class TestNextAlarmUsesHomeAssistantsClock:
    """Pronote lesson datetimes are naive local time; the host clock is not.

    Home Assistant containers run on UTC, so `datetime.now()` sat two hours
    behind the school day all summer. The comparison it fed decides one thing
    only - "is today's first lesson still ahead of us?" - and answering it in
    the wrong zone republishes a morning alarm that has already rung.

    The two tests below are a pair on purpose. They put Home Assistant's clock
    on either side of the same alarm, so whatever the host clock happens to
    read when the suite runs, it agrees with at most one of them - and the
    other fails on any build that reads the host instead.
    """

    @pytest.fixture
    def entry(self, hass):
        entry = MockConfigEntry(
            domain=DOMAIN,
            version=2,
            title="ENFANT Prenom",
            data={
                "connection_type": "username_password",
                "account_type": "parent",
                "child": "ENFANT Prenom",
                "url": "https://0000000a.index-education.net/pronote/parent.html",
                "username": "parent",
                "password": "hunter2",
            },
            options={"alarm_offset": 30},
        )
        entry.add_to_hass(hass)
        return entry

    @pytest.fixture(autouse=True)
    async def paris(self, hass):
        await hass.config.async_set_time_zone(TZ)

    # Neither the school days nor the clock is pinned to a calendar date, and
    # the tests never call `date.today()` themselves. The fetch decides what
    # "today" is; `_Days` simply answers whatever date it is asked for, and
    # records the first one, which is today by construction. That keeps the
    # pair honest across a midnight boundary and out of the naive-clock rules
    # the integration itself is being cleaned of.
    #
    # Freezing the whole clock instead was the first attempt: freezegun around
    # `await`s that go through the executor and the config-entry store hangs
    # the loop outright, which cost a runner twenty minutes. Patching
    # `dt_util.now` is also the narrower statement - what is under test is
    # which clock the comparison reads, not what time it is.

    class _Days:
        """A first lesson at 07:30 on the first day asked for, 08:00 after."""

        def __init__(self):
            self.today = None

        def __call__(self, day):
            if self.today is None:
                self.today = day
            hour = 7 if day == self.today else 8
            minute = 30 if day == self.today else 0
            return [_lesson(datetime.combine(day, time(hour, minute)))]

    @staticmethod
    def _ha_clock(days, hour, minute=0):
        """What `dt_util.now()` returns, resolved when the fetch calls it.

        By then `days.today` is set, because the lessons are fetched first.
        """
        return lambda *_: datetime.combine(
            days.today, time(hour, minute), tzinfo=ZoneInfo(TZ)
        )

    async def test_a_morning_already_past_is_not_republished(self, coordinator):
        """Home Assistant says 08:30; today's 07:00 alarm is behind us."""
        days = self._Days()
        client = _full_client(lessons_by_day=days)

        with (
            patch(HELPER, return_value=client),
            patch(NOW, side_effect=self._ha_clock(days, 8, 30)),
        ):
            data = await coordinator._async_update_data()

        assert data["next_alarm"] == datetime.combine(
            days.today + timedelta(days=1), time(7, 30), tzinfo=ZoneInfo(TZ)
        )

    async def test_a_morning_still_ahead_is_published(self, coordinator):
        """Home Assistant says 06:00; today's 07:00 alarm is genuinely next."""
        days = self._Days()
        client = _full_client(lessons_by_day=days)

        with (
            patch(HELPER, return_value=client),
            patch(NOW, side_effect=self._ha_clock(days, 6, 0)),
        ):
            data = await coordinator._async_update_data()

        assert data["next_alarm"] == datetime.combine(
            days.today, time(7, 0), tzinfo=ZoneInfo(TZ)
        )
