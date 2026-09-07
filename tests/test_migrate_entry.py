"""Tests for `async_migrate_entry`.

The migration runs once, on someone else's Home Assistant, on an entry created
before `connection_type` existed - which makes it both hard to reach by hand
and expensive to get wrong: a failed migration leaves the entry in
`MIGRATION_ERROR` and the integration does not load at all.
"""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pronote import async_migrate_entry
from custom_components.pronote.const import DOMAIN


@pytest.fixture
def v1_entry(hass):
    """A config entry as it was created before `connection_type` existed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="ENFANT Prenom",
        data={
            "url": "https://0000000a.index-education.net/pronote/parent.html",
            "username": "parent",
            "password": "hunter2",
            "account_type": "parent",
            "child": "ENFANT Prenom",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_migrates_a_version_1_entry(hass, v1_entry):
    """A v1 entry comes out at version 2, with the new key set."""
    assert await async_migrate_entry(hass, v1_entry) is True

    assert v1_entry.version == 2
    assert v1_entry.data["connection_type"] == "username_password"


async def test_keeps_every_existing_key(hass, v1_entry):
    """Migration adds a key; it must not drop or rewrite the others."""
    before = dict(v1_entry.data)

    await async_migrate_entry(hass, v1_entry)

    for key, value in before.items():
        assert v1_entry.data[key] == value, key


async def test_data_and_version_are_written_together(hass, v1_entry):
    """The entry is never observable as "data migrated, version still 1".

    Both go through a single `async_update_entry` call, so there is no window
    in which a reader could see the new data under the old version. The
    previous implementation assigned `config_entry.version` first and would
    have produced exactly that intermediate state - had it not raised
    `AttributeError` before getting there, since `version` is one of the
    attributes `ConfigEntry.__setattr__` refuses.
    """
    seen = []

    original = hass.config_entries.async_update_entry

    def _record(entry, **kwargs):
        seen.append(kwargs)
        return original(entry, **kwargs)

    with patch.object(
        hass.config_entries, "async_update_entry", side_effect=_record
    ):
        await async_migrate_entry(hass, v1_entry)

    assert len(seen) == 1
    assert seen[0]["version"] == 2
    assert seen[0]["data"]["connection_type"] == "username_password"


async def test_a_version_2_entry_is_left_alone(hass):
    """Nothing happens to an entry that is already current."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"connection_type": "qrcode", "account_type": "eleve"},
    )
    entry.add_to_hass(hass)
    before = dict(entry.data)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 2
    assert dict(entry.data) == before


async def test_migration_is_idempotent(hass, v1_entry):
    """Running it twice is harmless.

    Home Assistant retries a migration on every start until it succeeds, so a
    second run must not undo or duplicate the first.
    """
    await async_migrate_entry(hass, v1_entry)
    after_first = dict(v1_entry.data)

    assert await async_migrate_entry(hass, v1_entry) is True

    assert v1_entry.version == 2
    assert dict(v1_entry.data) == after_first
