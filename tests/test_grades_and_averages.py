"""Tests for the grade and average formatters.

Three defects are covered: the literal string `"None"` published as a grade
field, a subject appearing twice as soon as one test is marked absent, and an
off-by-one that published one grade and one evaluation fewer than the
constants ask for.

All three are visible on a Lovelace card, which is why they were reported as
display bugs rather than as data bugs.
"""

import logging
from types import SimpleNamespace

import pytest

from custom_components.pronote import pronote_formatter as fmt
from custom_components.pronote.pronote_formatter import (
    format_averages,
    format_evaluations,
    format_grade,
    format_grades,
    french_decimal_or_none,
    looks_like_a_number,
)


def _subject(name):
    return SimpleNamespace(name=name)


def _grade(**overrides):
    """A stand-in for `pronotepy.Grade`.

    The defaults are the awkward case: a graded test for which the school
    published no class average, no min, no max and no coefficient. pronotepy
    resolves those five fields with `strict=False`, so they come back as
    `None`.
    """
    fields = {
        "date": "2026-03-02",
        "subject": _subject("MATHEMATIQUES"),
        "comment": "",
        "grade": "16.2",
        "out_of": "20",
        "default_out_of": None,
        "coefficient": None,
        "average": None,
        "max": None,
        "min": None,
        "is_bonus": False,
        "is_optionnal": False,
        "is_out_of_20": True,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _average(subject, student, class_average="12,35", mn="2", mx="20"):
    """A stand-in for `pronotepy.Average`.

    Every numeric field of `Average` is resolved strictly, so none is ever
    `None` - but all of them pass through `Util.grade_parse`, which turns
    Pronote's sentinel encodings into words. The risk here is a sentinel, not
    a `None`.
    """
    return SimpleNamespace(
        subject=_subject(subject),
        student=student,
        class_average=class_average,
        min=mn,
        max=mx,
        out_of="20",
        default_out_of="",
        background_color="#180EEB",
    )


@pytest.fixture(autouse=True)
def _reset_reported_failures():
    """The throttle set is module level; keep tests independent of order."""
    fmt._REPORTED_FAILURES.clear()
    yield
    fmt._REPORTED_FAILURES.clear()


class TestFrenchDecimalOrNone:
    def test_none_stays_none(self):
        assert french_decimal_or_none(None) is None

    def test_point_becomes_comma(self):
        assert french_decimal_or_none("16.2") == "16,2"

    def test_an_integer_string_is_untouched(self):
        assert french_decimal_or_none("20") == "20"

    def test_a_word_is_untouched(self):
        assert french_decimal_or_none("Absent") == "Absent"


class TestLooksLikeANumber:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("16,2", True),
            ("16.2", True),
            ("20", True),
            ("Absent", False),
            ("Dispense", False),
            ("Felicitations", False),
            (None, False),
            ("", False),
        ],
    )
    def test_classification(self, value, expected):
        assert looks_like_a_number(value) is expected


class TestFormatGrade:
    def test_absent_fields_are_null_not_the_string_none(self):
        """`str(None)` is `"None"`, and it was reaching the cards.

        A card testing `if grade.class_average` sees a non-empty string, takes
        the branch, and `parseFloat("None")` gives NaN - which is why these
        grades were badged "below average" in orange.
        """
        formatted = format_grade(_grade())
        for key in ("class_average", "min", "max", "coefficient", "default_out_of"):
            assert formatted[key] is None, key

    def test_present_fields_keep_the_french_comma(self):
        formatted = format_grade(
            _grade(
                average="10.5",
                coefficient="2.0",
                max="18.5",
                min="3.5",
                default_out_of="20",
            )
        )
        assert formatted["class_average"] == "10,5"
        assert formatted["coefficient"] == "2,0"
        assert formatted["max"] == "18,5"
        assert formatted["min"] == "3,5"

    def test_out_of_is_not_converted(self):
        assert format_grade(_grade())["out_of"] == "20"

    def test_grade_out_of_is_unchanged(self):
        """`grade` and `out_of` are strict, so this cannot become "None/20"."""
        assert format_grade(_grade())["grade_out_of"] == "16.2/20"


class TestTruncation:
    def test_the_limit_is_honoured_exactly(self):
        """The loop stopped one short: 11 asked for, 10 delivered."""
        assert len(format_grades([_grade() for _ in range(20)], 11)) == 11

    def test_the_evaluation_limit_too(self):
        assert len(format_grades([_grade() for _ in range(20)], 15)) == 15

    def test_a_short_list_is_returned_whole(self):
        assert len(format_grades([_grade() for _ in range(3)], 11)) == 3

    @pytest.mark.parametrize("value", [None, []])
    def test_nothing_to_format(self, value):
        assert format_grades(value, 11) == []


class _Boom:
    """An object on which *any* field access raises.

    `__getattr__` rather than a single property, so the exception type does
    not depend on which field the formatter happens to read first - the point
    of these tests is the handling, not the order of the reads.
    """

    def __init__(self, message="secret payload 1234"):
        self._message = message

    def __getattr__(self, name):
        # `__getattr__` only fires when normal lookup fails, and `_message`
        # is in the instance dict. The guard keeps a missing one from
        # recursing rather than reporting itself.
        if name == "_message":
            raise AttributeError(name)
        raise RuntimeError(self._message)


class TestResilience:
    def test_one_unreadable_grade_does_not_empty_the_list(self):
        """This ran in `extra_state_attributes`, so a raise emptied the lot."""
        assert len(format_grades([_grade(), _Boom(), _grade()])) == 2

    def test_a_skipped_item_does_not_consume_a_slot(self):
        items = [_Boom()] + [_grade() for _ in range(15)]
        assert len(format_grades(items, 11)) == 11

    def test_the_warning_does_not_carry_the_exception_payload(self, caplog):
        """pronotepy's `ParsingError` holds the raw JSON of the object.

        For a grade that is the child's personal data, so the log line names
        the exception type and the index, never the message.
        """
        caplog.set_level(logging.WARNING, logger=fmt.__name__)

        format_grades([_grade(), _Boom()])

        assert "secret payload 1234" not in caplog.text
        assert "RuntimeError" in caplog.text

    def test_the_same_failure_is_reported_once_not_once_per_refresh(self, caplog):
        """`extra_state_attributes` is evaluated on every state write.

        An unreadable item nobody can fix would otherwise write 96 identical
        lines a day at the default interval.
        """
        caplog.set_level(logging.WARNING, logger=fmt.__name__)

        items = [_grade(), _Boom("boom"), _grade()]
        for _ in range(5):
            format_grades(items)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1


class _BadGrade:
    """A readable id, unreadable everywhere else - the real failure shape.

    A pronotepy object that fails to parse still has its `id`: the identifier
    comes straight out of the JSON, while the fields that raise are the ones
    resolved through `Util.grade_parse`. `_Boom` is the harsher case where even
    the id cannot be read.
    """

    def __init__(self, identifier):
        self.id = identifier

    def __getattr__(self, name):
        raise RuntimeError("Error while converting value: 15,5 secret")


class TestTheThrottleIsKeyedOnTheItem:
    """The message promised "once per distinct failure". It was not true.

    The signature was `(label, ((index, error), ...))`, so it named a position
    in a list rather than an item. Grades are re-sorted on every refresh and
    `format_grades` is called from `extra_state_attributes`: one new grade
    arriving at the top shifted every index below it, every signature changed,
    and the same permanently unreadable grade was announced again - which is
    exactly the 96-lines-a-day the throttle was written to prevent.
    """

    def test_a_new_item_at_the_top_does_not_re_report(self, caplog):
        """The defect, reduced to two calls.

        Same broken grade, one index further down because a grade came in
        above it. On the unfixed branch this logs twice.
        """
        caplog.set_level(logging.WARNING, logger=fmt.__name__)
        broken = _BadGrade("G1")

        format_grades([broken, _grade()])
        format_grades([_grade(), broken, _grade()])

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1

    def test_a_second_failure_does_not_re_announce_the_first(self, caplog):
        """The signature covered the whole batch, not one item.

        So a second grade going bad re-announced the first one along with it.
        Two distinct failures, two lines, each said once.
        """
        caplog.set_level(logging.WARNING, logger=fmt.__name__)
        first, second = _BadGrade("G1"), _BadGrade("G2")

        format_grades([first, _grade()])
        format_grades([first, second, _grade()])
        format_grades([first, second, _grade()])

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 2
        assert sum("id=G1" in r.getMessage() for r in warnings) == 1
        assert sum("id=G2" in r.getMessage() for r in warnings) == 1

    def test_the_same_id_under_two_labels_is_two_failures(self, caplog):
        """Ids are unique per kind, not across kinds - the label stays in the key."""
        caplog.set_level(logging.WARNING, logger=fmt.__name__)

        format_grades([_BadGrade("42")])
        format_evaluations([_BadGrade("42")])

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 2

    def test_an_item_whose_id_cannot_be_read_is_still_reported(self, caplog):
        """`getattr(item, "id", None)` only swallows `AttributeError`.

        `_Boom` raises `RuntimeError` on every field, id included, so naming it
        has to be wrapped or the report crashes the very property it protects.
        """
        caplog.set_level(logging.WARNING, logger=fmt.__name__)

        assert format_grades([_Boom(), _grade()]) != []

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert "_Boom" in warnings[0].getMessage()

    def test_the_identity_is_the_id_not_the_content(self, caplog):
        """Naming the item must not undo the module's own privacy property.

        Only `id` is read, deliberately: an opaque Pronote identifier. A field
        like an evaluation's `name` would be the child's schoolwork.
        """
        caplog.set_level(logging.WARNING, logger=fmt.__name__)

        format_grades([_BadGrade("G1")])

        message = caplog.records[-1].getMessage()
        assert "id=G1" in message
        assert "secret" not in message
        assert "15,5" not in message


class TestTheThrottleIsBounded:
    """It was an unbounded module-level set in a process that runs for months.

    Keying on the item makes the account's own volume the natural bound, but
    an identity nobody foresaw should not be able to grow it without end.
    """

    def test_the_set_stops_growing_at_the_cap(self, caplog):
        caplog.set_level(logging.WARNING, logger=fmt.__name__)

        over_the_cap = fmt._MAX_REPORTED_FAILURES + 50
        format_grades([_BadGrade(f"G{n}") for n in range(over_the_cap)])

        assert len(fmt._REPORTED_FAILURES) == fmt._MAX_REPORTED_FAILURES

    def test_items_past_the_cap_are_still_skipped_not_raised(self):
        """The cap silences the warning, never the resilience.

        Whatever the log does, the point of the try/except is that the good
        items still reach the card.
        """
        broken = [_BadGrade(f"G{n}") for n in range(fmt._MAX_REPORTED_FAILURES + 10)]

        assert format_grades([*broken, _grade(), _grade()]) != []


class TestFormatAverages:
    def test_the_absence_duplicate_is_folded(self):
        """Issue #68, with the two rows from the report.

        Pronote splits the service when a test is missed: the subject comes
        back twice, once with the real average and once with a sentinel.
        """
        out = format_averages(
            [
                _average("MATHEMATIQUES", "Absent"),
                _average("MATHEMATIQUES", "16,2"),
            ]
        )
        assert len(out) == 1
        assert out[0]["average"] == "16,2"

    def test_the_reason_is_carried_onto_the_row_that_survives(self):
        """Folding must not lose the fact that a test was missed."""
        out = format_averages(
            [
                _average("MATHEMATIQUES", "Absent"),
                _average("MATHEMATIQUES", "16,2"),
            ]
        )
        assert out[0]["status"] == "Absent"

    def test_a_subject_with_no_number_is_kept(self):
        """Filtering sentinels unconditionally would hide the subject.

        A subject in which the pupil has genuinely not been graded has one
        row, a sentinel. Dropping it would remove the subject from the card
        instead of showing the reason.
        """
        out = format_averages([_average("MUSIQUE", "NonNote")])
        assert len(out) == 1
        assert out[0]["average"] == "NonNote"
        assert out[0]["status"] == "NonNote"

    def test_two_numeric_rows_for_one_subject_both_survive(self):
        """A subject legitimately split into groups keeps both rows.

        The deduplication never removes a number.
        """
        out = format_averages(
            [_average("LV1 ANGLAIS", "14,0"), _average("LV1 ANGLAIS", "15,5")]
        )
        assert len(out) == 2

    def test_two_sentinel_rows_without_a_number_both_survive(self):
        out = format_averages([_average("EPS", "Absent"), _average("EPS", "Dispense")])
        assert len(out) == 2

    def test_status_is_null_when_the_average_is_a_number(self):
        out = format_averages([_average("HISTOIRE", "13,0")])
        assert out[0]["status"] is None

    def test_the_existing_attribute_shape_is_preserved(self):
        """Eight keys were published before; they all keep their name.

        `status` is added. Cards in place must keep working unchanged.
        """
        out = format_averages([_average("HISTOIRE", "13,0")])
        assert set(out[0]) == {
            "average",
            "class",
            "max",
            "min",
            "out_of",
            "default_out_of",
            "subject",
            "background_color",
            "status",
        }

    def test_rows_are_sorted_by_subject(self):
        out = format_averages(
            [_average("MUSIQUE", "12,0"), _average("ANGLAIS", "13,0")]
        )
        assert [row["subject"] for row in out] == ["ANGLAIS", "MUSIQUE"]

    @pytest.mark.parametrize("value", [None, []])
    def test_nothing_to_format(self, value):
        assert format_averages(value) == []

    def test_the_input_list_is_not_mutated(self):
        rows = [_average("MATHEMATIQUES", "Absent"), _average("MATHEMATIQUES", "16,2")]
        before = list(rows)
        format_averages(rows)
        assert rows == before
