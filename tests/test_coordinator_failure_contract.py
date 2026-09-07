"""Tests for the coordinator's failure contract.

`DataUpdateCoordinator` assigns `self.data = await self._async_update_data()`,
so the returned value is the whole contract: return a dict and it becomes the
new state, raise and the previous state is kept untouched. Writing into
`self.data` as the fetch progresses breaks it in the way that is hardest to
notice - the entry stays marked successful while the state is half a refresh
old and half a refresh new.

These tests pin the three properties that follow from returning a local dict
and raising `UpdateFailed`.
"""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pronote.const import DOMAIN
from custom_components.pronote.coordinator import PronoteDataUpdateCoordinator

HELPER = "custom_components.pronote.coordinator.get_pronote_client"


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
