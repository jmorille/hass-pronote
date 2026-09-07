"""Tests for `pronote_formatter`.

A starting point rather than full coverage: the formatters are where Pronote's
data model meets Home Assistant's state attributes.
"""

from datetime import date, datetime, time
from types import SimpleNamespace

from custom_components.pronote.const import HOMEWORK_DESC_MAX_LENGTH
from custom_components.pronote.pronote_formatter import (
    format_attachment_list,
    format_displayed_lesson,
    format_food_list,
    format_homework,
    format_lesson,
    format_menu,
    format_period,
)


class TestFormatDisplayedLesson:
    """`format_displayed_lesson` decides what a timetable row is called."""

    def test_detention_wins_over_the_subject(self, make_lesson):
        lesson = make_lesson(detention=True, subject=SimpleNamespace(name="ANGLAIS"))
        assert format_displayed_lesson(lesson) == "RETENUE"

    def test_uses_the_subject_name(self, make_lesson):
        assert format_displayed_lesson(make_lesson()) == "ANGLAIS LV1"

    def test_falls_back_when_there_is_no_subject(self, make_lesson):
        # Pronote sends subject-less entries for school outings and the like.
        assert format_displayed_lesson(make_lesson(subject=None)) == "autre"


class TestFormatLesson:
    """`format_lesson` builds one row of the timetable attributes."""

    def test_formats_the_times_as_the_cards_expect(self, make_lesson, lunch_break_time):
        formatted = format_lesson(make_lesson(), lunch_break_time)
        assert formatted["start_time"] == "09:30"
        assert formatted["end_time"] == "10:30"
        # The raw datetimes are kept alongside the strings; automations use them.
        assert formatted["start_at"] == datetime(2026, 9, 7, 9, 30)
        assert formatted["end_at"] == datetime(2026, 9, 7, 10, 30)

    def test_morning_and_afternoon_are_exclusive(self, make_lesson, lunch_break_time):
        morning = format_lesson(make_lesson(), lunch_break_time)
        assert morning["is_morning"] is True
        assert morning["is_afternoon"] is False

        afternoon = format_lesson(
            make_lesson(start=datetime(2026, 9, 7, 14, 0)), lunch_break_time
        )
        assert afternoon["is_morning"] is False
        assert afternoon["is_afternoon"] is True

    def test_a_lesson_starting_exactly_at_the_break_is_afternoon(self, make_lesson):
        # The boundary is `>=`, so 13:00 belongs to the afternoon. Asserted
        # because it is the kind of edge a refactor silently flips.
        formatted = format_lesson(
            make_lesson(start=datetime(2026, 9, 7, 13, 0)), time(13, 0)
        )
        assert formatted["is_afternoon"] is True

    def test_the_lunch_break_is_configurable(self, make_lesson):
        formatted = format_lesson(
            make_lesson(start=datetime(2026, 9, 7, 12, 0)), time(11, 30)
        )
        assert formatted["is_afternoon"] is True

    def test_keeps_a_cancelled_lesson(self, make_lesson, lunch_break_time):
        # Cancelled lessons stay in the list, flagged; a card decides how to
        # show them. Dropping them here would hide a cancellation.
        formatted = format_lesson(make_lesson(canceled=True), lunch_break_time)
        assert formatted["canceled"] is True
        assert formatted["lesson"] == "ANGLAIS LV1"


class TestFormatAttachmentList:
    """Attachments are flattened to plain dicts, never pronotepy objects."""

    def test_empty(self):
        assert format_attachment_list([]) == []

    def test_maps_the_three_published_fields(self, make_attachment):
        formatted = format_attachment_list([make_attachment()])
        assert formatted == [
            {
                "name": "enonce.pdf",
                "url": "https://example.invalid/enonce.pdf",
                "type": 1,
            }
        ]

    def test_result_is_json_serialisable(self, make_attachment):
        # State attributes are written to the recorder database, so anything
        # that is not a plain type would be dropped or raise.
        import json

        json.dumps(format_attachment_list([make_attachment()]))


class TestFormatHomework:
    """`format_homework` truncates the description for the card."""

    def test_short_description_is_capped(self, make_homework):
        long_description = "x" * (HOMEWORK_DESC_MAX_LENGTH + 50)
        formatted = format_homework(make_homework(description=long_description))
        assert len(formatted["short_description"]) == HOMEWORK_DESC_MAX_LENGTH
        # The full text is kept in a separate key, so nothing is lost.
        assert formatted["description"] == long_description

    def test_short_description_is_left_alone_when_it_fits(self, make_homework):
        formatted = format_homework(make_homework(description="Lire le chapitre 3"))
        assert formatted["short_description"] == "Lire le chapitre 3"

    def test_missing_files_attribute_does_not_raise(self, make_homework):
        # Some Pronote instances omit the attachment list entirely.
        homework = make_homework()
        del homework.files
        assert format_homework(homework)["files"] == []

    def test_attachments_are_formatted(self, make_homework, make_attachment):
        formatted = format_homework(make_homework(files=[make_attachment()]))
        assert formatted["files"][0]["name"] == "enonce.pdf"

    def test_publishes_the_subject_name_not_the_object(self, make_homework):
        assert format_homework(make_homework())["subject"] == "MATHEMATIQUES"


class TestFormatFoodList:
    """`format_food_list` is the one formatter that already guards `None`."""

    def test_none_becomes_an_empty_list(self):
        assert format_food_list(None) == []

    def test_empty_stays_empty(self):
        assert format_food_list([]) == []

    def test_labels_are_flattened(self):
        food = SimpleNamespace(
            name="Carottes rapees",
            labels=[SimpleNamespace(name="Bio", color="#00FF00")],
        )
        assert format_food_list([food]) == [
            {"name": "Carottes rapees", "labels": [{"name": "Bio", "color": "#00FF00"}]}
        ]

    def test_food_without_labels(self):
        food = SimpleNamespace(name="Pain", labels=[])
        assert format_food_list([food]) == [{"name": "Pain", "labels": []}]


class TestFormatMenu:
    """A menu carries six courses, each of which may be absent."""

    def test_all_courses_absent(self):
        menu = SimpleNamespace(
            name="Dejeuner",
            date=date(2026, 9, 8),
            is_lunch=True,
            is_dinner=False,
            first_meal=None,
            main_meal=None,
            side_meal=None,
            other_meal=None,
            cheese=None,
            dessert=None,
        )
        formatted = format_menu(menu)
        assert formatted["date"] == "2026-09-08"
        assert formatted["is_lunch"] is True
        for course in (
            "first_meal",
            "main_meal",
            "side_meal",
            "other_meal",
            "cheese",
            "dessert",
        ):
            assert formatted[course] == [], course


class TestFormatPeriod:
    """The period id is a slug, because it is used to build entity ids."""

    def test_id_is_slugified_with_underscores(self, make_period):
        formatted = format_period(make_period(), True)
        assert formatted["id"] == "trimestre_1"
        assert formatted["name"] == "Trimestre 1"
        assert formatted["is_current_period"] is True

    def test_accents_and_punctuation_are_removed(self, make_period):
        # A slug feeds `sensor.pronote_<child>_grades_<period>`; anything but
        # ASCII and underscores would produce a broken entity id.
        formatted = format_period(make_period(name="2eme Semestre (annee)"), False)
        assert formatted["id"] == "2eme_semestre_annee"
        assert formatted["is_current_period"] is False
