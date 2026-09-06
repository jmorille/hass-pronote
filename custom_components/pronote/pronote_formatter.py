"""Data Formatter for the Pronote integration."""

import logging
from datetime import datetime
from slugify import slugify

from .const import (
    HOMEWORK_DESC_MAX_LENGTH,
)

_LOGGER = logging.getLogger(__name__)


def format_displayed_lesson(lesson):
    if lesson.detention is True:
        return "RETENUE"
    if lesson.subject:
        return lesson.subject.name
    return "autre"


def format_lesson(lesson, lunch_break_time):
    return {
        "start_at": lesson.start,
        "end_at": lesson.end,
        "start_time": lesson.start.strftime("%H:%M"),
        "end_time": lesson.end.strftime("%H:%M"),
        "lesson": format_displayed_lesson(lesson),
        "classroom": lesson.classroom,
        "canceled": lesson.canceled,
        "status": lesson.status,
        "background_color": lesson.background_color,
        "teacher_name": lesson.teacher_name,
        "teacher_names": lesson.teacher_names,
        "classrooms": lesson.classrooms,
        "outing": lesson.outing,
        "memo": lesson.memo,
        "group_name": lesson.group_name,
        "group_names": lesson.group_names,
        "exempted": lesson.exempted,
        "virtual_classrooms": lesson.virtual_classrooms,
        "num": lesson.num,
        "detention": lesson.detention,
        "test": lesson.test,
        "is_morning": lesson.start.time() < lunch_break_time,
        "is_afternoon": lesson.start.time() >= lunch_break_time,
    }


def format_attachment_list(attachments):
    return [
        {
            "name": attachment.name,
            "url": attachment.url,
            "type": attachment.type,
        }
        for attachment in attachments
    ]


def format_homework(homework) -> dict:
    try:
        files = format_attachment_list(homework.files)
    except AttributeError:
        files = []
    return {
        "date": homework.date,
        "subject": homework.subject.name,
        "short_description": (homework.description)[0:HOMEWORK_DESC_MAX_LENGTH],
        "description": (homework.description),
        "done": homework.done,
        "background_color": homework.background_color,
        "files": files,
    }


def french_decimal_or_none(value):
    """Return a Pronote decimal with a French comma, or None when it is absent.

    pronotepy resolves Grade.average, .max, .min, .coefficient and
    .default_out_of with `strict=False`, which means the attribute is plain
    `None` whenever the establishment did not send the field (a grade with no
    class average, a subject with no coefficient...). `str(None)` is the
    literal string "None", so the previous `str(value).replace(".", ",")`
    happily produced `class_average: "None"` and cards rendered "None/20".

    Returning None instead makes the attribute `null` in Home Assistant, which
    is what a template or a card can actually test for. An empty string would
    not do: it is falsy but still a string, so `float()` on it raises the very
    error this indirection exists to avoid.
    """
    if value is None:
        return None
    return str(value).replace(".", ",")


def looks_like_a_number(value) -> bool:
    """True when a Pronote grade-ish value can be read as a number.

    Every value that goes through `Util.grade_parse` may come back as one of
    the sentinels in `Util.grade_translate` ("Absent", "Dispense", "NonNote",
    "Inapte", "NonRendu", "AbsentZero", "NonRenduZero", "Felicitations")
    instead of a number, because Pronote encodes those states as "|1".."|8".
    Testing for a number rather than matching that list keeps us correct if
    pronotepy ever adds a sentinel.
    """
    if value is None:
        return False
    try:
        float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return False
    return True


def format_grade(grade) -> dict:
    return {
        "date": grade.date,
        "subject": grade.subject.name,
        "comment": grade.comment,
        "grade": grade.grade,
        "out_of": french_decimal_or_none(grade.out_of),
        "default_out_of": french_decimal_or_none(grade.default_out_of),
        # f-string, not concatenation: `grade` and `out_of` are the only two of
        # these pronotepy resolves in strict mode, but should one ever be None
        # the old `grade.grade + "/" + grade.out_of` raised a TypeError that
        # emptied the entire grades attribute. "None/20" in one row is a much
        # better symptom than an empty card.
        "grade_out_of": f"{grade.grade}/{grade.out_of}",
        "coefficient": french_decimal_or_none(grade.coefficient),
        "class_average": french_decimal_or_none(grade.average),
        "max": french_decimal_or_none(grade.max),
        "min": french_decimal_or_none(grade.min),
        "is_bonus": grade.is_bonus,
        "is_optionnal": grade.is_optionnal,
        "is_out_of_20": grade.is_out_of_20,
    }


def _format_list(items, formatter, label, limit=None):
    """Format a list of Pronote objects, skipping the ones that cannot be read.

    Two reasons for the try/except around a single item. First, these
    formatters run inside `extra_state_attributes`, a property Home Assistant
    calls while writing the state: an exception there empties the whole
    attribute and the card shows nothing, so one unparsable grade used to cost
    the user every other grade. Second, the failure was effectively silent for
    the person reading the card.

    The warning stays deliberately vague: pronotepy's ParsingError carries the
    raw JSON payload of the object in its message, which is the child's
    personal data, and warnings do reach home-assistant.log. The exception type
    is enough to tell a parsing problem from a missing attribute, and the full
    traceback is available at debug level for anyone who turns it on for their
    own account.
    """
    if not items:
        return []

    formatted = []
    for index, item in enumerate(items):
        if limit is not None and len(formatted) >= limit:
            break
        try:
            formatted.append(formatter(item))
        except Exception as ex:  # one bad item must not empty the whole list
            _LOGGER.warning(
                "Could not format %s #%d (%s), skipping it; "
                "enable debug logging for %s to see the details",
                label,
                index,
                type(ex).__name__,
                __name__,
            )
            _LOGGER.debug("Could not format %s #%d", label, index, exc_info=True)

    return formatted


def format_grades(grades, limit=None) -> list:
    """Format at most `limit` grades.

    The caller used to do `index_note += 1`, then break on
    `index_note == GRADES_TO_DISPLAY` before appending, so GRADES_TO_DISPLAY =
    11 displayed ten grades. Stopping on the length of the output honours the
    constant exactly, and counts only items that were actually appended, so a
    skipped unreadable grade does not silently eat a slot.
    """
    return _format_list(grades, format_grade, "grade", limit)


def format_absence(absence) -> dict:
    return {
        "from": absence.from_date,
        "to": absence.to_date,
        "justified": absence.justified,
        "hours": absence.hours,
        "days": absence.days,
        "reason": str(absence.reasons)[2:-2],
    }


def format_delay(delay) -> dict:
    return {
        "date": delay.date,
        "minutes": delay.minutes,
        "justified": delay.justified,
        "justification": delay.justification,
        "reasons": str(delay.reasons)[2:-2],
    }


def format_evaluation(evaluation) -> dict:
    return {
        "name": evaluation.name,
        "domain": evaluation.domain,
        "date": evaluation.date,
        "subject": evaluation.subject.name,
        "description": evaluation.description,
        "coefficient": evaluation.coefficient,
        "paliers": evaluation.paliers,
        "teacher": evaluation.teacher,
        "acquisitions": [
            {
                "order": acquisition.order,
                "name": acquisition.name,
                "abbreviation": acquisition.abbreviation,
                "level": acquisition.level,
                "domain": acquisition.domain,
                "coefficient": acquisition.coefficient,
                "pillar": acquisition.pillar,
                "pillar_prefix": acquisition.pillar_prefix,
            }
            for acquisition in evaluation.acquisitions
        ],
    }


def format_evaluations(evaluations, limit=None) -> list:
    """Format at most `limit` evaluations. Same off-by-one as the grades."""
    return _format_list(evaluations, format_evaluation, "evaluation", limit)


def format_average(average) -> dict:
    """Format one subject average.

    Every numeric-looking field of `Average` goes through `Util.grade_parse`,
    so `average` can be the word "Absent" (or "Dispense", "NonNote"...) rather
    than a number. The existing keys are left exactly as they were - users have
    Lovelace cards bound to them - and the information is added as a separate
    `status` key: None when the student average really is a number, otherwise
    the Pronote sentinel. A card can then hide or badge those rows without
    hard-coding the sentinel list, and existing templates keep using `average`.
    """
    student = average.student
    return {
        "average": student,
        "class": average.class_average,
        "max": average.max,
        "min": average.min,
        "out_of": average.out_of,
        "default_out_of": average.default_out_of,
        "subject": average.subject.name,
        "background_color": average.background_color,
        "status": None if looks_like_a_number(student) else student,
    }


def format_averages(averages) -> list:
    """Format the averages of a period, dropping Pronote's absence duplicates.

    When a test in a subject is marked absent, Pronote splits the subject into
    two services and returns it twice: once with the real average and once with
    the student average set to a sentinel ("Absent"). The duplicate is visible
    in Pronote's own mobile app, so it is not something pronotepy invents, and
    the integration exposed both rows - a card then listed MATHEMATIQUES twice,
    once at "Absent" and once at "16,2".

    A sentinel row is only dropped when another row for the same subject
    carries a real number. Dropping every sentinel row unconditionally would be
    wrong: a subject the student has genuinely not been graded in yet has a
    single, sentinel-only row, and removing it would make the subject vanish
    from the card instead of showing "NonNote". Two numeric rows for the same
    subject (a subject legitimately split into groups) are both kept for the
    same reason - this deduplication never removes a number.
    """
    formatted = _format_list(averages, format_average, "average")

    subjects_with_a_number = {
        item["subject"] for item in formatted if item["status"] is None
    }
    kept = []
    for item in formatted:
        if item["status"] is not None and item["subject"] in subjects_with_a_number:
            _LOGGER.debug(
                "Dropping the '%s' entry of subject '%s': another entry for the "
                "same subject carries a real average",
                item["status"],
                item["subject"],
            )
            continue
        kept.append(item)

    # `subject` is resolved in strict mode so it is always a string, but sorting
    # must not become the thing that raises inside extra_state_attributes.
    return sorted(kept, key=lambda item: item["subject"] or "")


def format_punishment(punishment) -> dict:
    return {
        "date": punishment.given.strftime("%Y-%m-%d"),
        "subject": punishment.during_lesson,
        "reasons": punishment.reasons,
        "circumstances": punishment.circumstances,
        "nature": punishment.nature,
        "duration": str(punishment.duration),
        "homework": punishment.homework,
        "exclusion": punishment.exclusion,
        "during_lesson": punishment.during_lesson,
        "homework_documents": format_attachment_list(punishment.homework_documents),
        "circumstance_documents": format_attachment_list(
            punishment.circumstance_documents
        ),
        "giver": punishment.giver,
        "schedule": [
            {
                "start": schedule.start,
                "duration": str(schedule.duration),
            }
            for schedule in punishment.schedule
        ],
        "schedulable": punishment.schedulable,
    }


def format_food_list(food_list) -> dict:
    formatted_food_list = []
    if food_list is None:
        return formatted_food_list

    for food in food_list:
        formatted_food_labels = []
        for label in food.labels:
            formatted_food_labels.append(
                {
                    "name": label.name,
                    "color": label.color,
                }
            )
        formatted_food_list.append(
            {
                "name": food.name,
                "labels": formatted_food_labels,
            }
        )

    return formatted_food_list


def format_menu(menu) -> dict:
    return {
        "name": menu.name,
        "date": menu.date.strftime("%Y-%m-%d"),
        "is_lunch": menu.is_lunch,
        "is_dinner": menu.is_dinner,
        "first_meal": format_food_list(menu.first_meal),
        "main_meal": format_food_list(menu.main_meal),
        "side_meal": format_food_list(menu.side_meal),
        "other_meal": format_food_list(menu.other_meal),
        "cheese": format_food_list(menu.cheese),
        "dessert": format_food_list(menu.dessert),
    }


def format_information_and_survey(information_and_survey) -> dict:
    return {
        "author": information_and_survey.author,
        "title": information_and_survey.title,
        "read": information_and_survey.read,
        "creation_date": information_and_survey.creation_date,
        "start_date": information_and_survey.start_date,
        "end_date": information_and_survey.end_date,
        "category": information_and_survey.category,
        "survey": information_and_survey.survey,
        "anonymous_response": information_and_survey.anonymous_response,
        "attachments": format_attachment_list(information_and_survey.attachments()),
        "template": information_and_survey.template,
        "shared_template": information_and_survey.shared_template,
        "content": information_and_survey.content(),
    }


def format_period(period, is_current_period: bool) -> dict:
    return {
        "id": slugify(period.name, separator="_"),
        "name": period.name,
        "start": period.start,
        "end": period.end,
        "is_current_period": is_current_period,
    }
