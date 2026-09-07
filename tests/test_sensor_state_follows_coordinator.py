"""Tests that a sensor's state follows the coordinator.

Every sensor used to capture its state in `__init__`, from the coordinator
data as it stood when the platform was set up. `async_setup_entry` runs once,
so that value never changed again: the number of lessons today was whatever it
had been at startup, for as long as Home Assistant stayed up. Issues #175,
#130 and #131 are all that same symptom seen from different sensors.

The other half is what happens when a key the entity was built on is missing
from a later refresh - which the per-period keys genuinely are, whenever
Pronote returns no period list.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.pronote import sensor as sensor_module
from custom_components.pronote.sensor import PronoteGenericSensor


@pytest.fixture
def coordinator():
    """A coordinator stub whose `data` can be swapped between reads."""
    stub = MagicMock()
    stub.data = {
        "child_info": SimpleNamespace(name="ENFANT Prenom"),
        "sensor_prefix": "enfant_prenom",
        "account_type": "parent",
        "lessons_today": [],
        "grades": [],
    }
    stub.last_update_success = True
    stub.config_entry.options = {}
    return stub


def _sensor(coordinator, key="lessons_today", name="Today's timetable", state_fn=len):
    return PronoteGenericSensor(coordinator, key, name, state_fn)


class TestStateTracksTheCoordinator:
    def test_a_later_refresh_changes_the_state(self, coordinator):
        """The defect, stated directly.

        The entity is built when the list is empty - which is what happens on
        a restart during the night, before Pronote has published the day - and
        must report 5 once the coordinator holds five lessons.
        """
        entity = _sensor(coordinator)
        assert entity.native_value == 0

        coordinator.data = dict(coordinator.data, lessons_today=[1, 2, 3, 4, 5])

        assert entity.native_value == 5

    def test_it_follows_downwards_too(self, coordinator):
        entity = _sensor(coordinator)
        coordinator.data = dict(coordinator.data, lessons_today=[1, 2, 3])
        assert entity.native_value == 3

        coordinator.data = dict(coordinator.data, lessons_today=[])
        assert entity.native_value == 0

    def test_without_a_state_function_the_value_is_the_data(self, coordinator):
        coordinator.data = dict(coordinator.data, current_period="Trimestre 1")
        entity = _sensor(coordinator, key="current_period", name="Current period",
                         state_fn=None)
        assert entity.native_value == "Trimestre 1"

        coordinator.data = dict(coordinator.data, current_period="Trimestre 2")
        assert entity.native_value == "Trimestre 2"


class TestAMissingKey:
    def test_the_value_is_none_not_the_string_unavailable(self, coordinator):
        """`native_value` returning `"unavailable"` published a literal string.

        Home Assistant renders an unavailable entity itself, from `available`;
        a sensor whose state is the *word* is a different thing, and templates
        comparing to it were reading a value the entity never has when it is
        genuinely unavailable.
        """
        entity = _sensor(coordinator, key="grades_trimestre_1", name="Grades")
        assert entity.native_value is None

    def test_the_entity_reports_unavailable(self, coordinator):
        entity = _sensor(coordinator, key="grades_trimestre_1", name="Grades")
        assert entity.available is False

    def test_a_present_key_is_available(self, coordinator):
        assert _sensor(coordinator).available is True

    def test_a_failed_refresh_makes_it_unavailable(self, coordinator):
        entity = _sensor(coordinator)
        coordinator.last_update_success = False
        assert entity.available is False

    def test_the_disappearance_is_logged_once_not_once_per_refresh(
        self, coordinator, caplog
    ):
        """`extra_state_attributes` and `available` are read on every write.

        Without the flag this warning would repeat for as long as the key
        stayed away, which on a per-period key can be the rest of the term.
        """
        caplog.set_level(logging.WARNING, logger=sensor_module.__name__)
        entity = _sensor(coordinator, key="grades_trimestre_1", name="Grades")

        for _ in range(5):
            assert entity.available is False

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1

    def test_it_is_logged_again_if_the_key_disappears_a_second_time(
        self, coordinator, caplog
    ):
        """The flag resets when the key comes back.

        Otherwise a key that flaps would be reported once ever, and the second
        outage - the one the user is actually asking about - would be silent.
        """
        caplog.set_level(logging.WARNING, logger=sensor_module.__name__)
        entity = _sensor(coordinator, key="grades_trimestre_1", name="Grades")

        assert entity.available is False
        coordinator.data = dict(coordinator.data, grades_trimestre_1=[])
        assert entity.available is True
        coordinator.data = {
            k: v for k, v in coordinator.data.items() if k != "grades_trimestre_1"
        }
        assert entity.available is False

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 2

    def test_the_warning_names_the_key_and_the_entity(self, coordinator, caplog):
        caplog.set_level(logging.WARNING, logger=sensor_module.__name__)
        entity = _sensor(coordinator, key="grades_trimestre_1", name="Grades")

        assert entity.available is False

        assert "grades_trimestre_1" in caplog.text
        assert entity.entity_id in caplog.text


class TestIdentity:
    def test_the_unique_id_does_not_depend_on_the_state(self, coordinator):
        """A refresh must never re-key an entity.

        `unique_id` is what ties the entity to its registry row, its history
        and every dashboard referring to it.
        """
        entity = _sensor(coordinator)
        before = entity.unique_id

        coordinator.data = dict(coordinator.data, lessons_today=[1, 2, 3])

        assert entity.unique_id == before

    def test_the_entity_id_is_the_english_slug(self, coordinator):
        """Pinned so a translated name cannot move the entity id."""
        entity = _sensor(coordinator)
        assert entity.entity_id == "sensor.pronote_enfant_prenom_today_s_timetable"
