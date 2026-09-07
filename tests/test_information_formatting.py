"""Tests for `get_information_and_surveys`.

`Information.content` and `Information.attachments` are lazy: reading either
calls `_fetch_content()`, which posts to Pronote. The coordinator used to
store the pronotepy objects and let the sensor read those fields later - after
the client reference had been cut, and from inside a property running on the
event loop. Whether that produced an empty attribute, an exception or a
blocking HTTP call depended on timing.

Resolving them here, in the executor job that already holds a live client, is
what these tests pin.
"""

import logging
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from custom_components.pronote import coordinator as coord
from custom_components.pronote.coordinator import get_information_and_surveys


class _Information:
    """A stand-in for `pronotepy.Information` with the same laziness.

    `content()` and `attachments()` raise once `_client` is gone, exactly as
    the real object does when `_fetch_content` has nothing to post with.
    """

    def __init__(self, title, created, *, content="Corps du message", broken=False):
        self.author = "LA DIRECTION"
        self.title = title
        self.read = False
        self.creation_date = created
        self.start_date = created
        self.end_date = created
        self.category = "Informations"
        self.survey = False
        self.anonymous_response = False
        self.template = False
        self.shared_template = False
        self._client = object()
        self._content = content
        self._broken = broken

    def _require_client(self):
        if self._client is None:
            raise AttributeError("'NoneType' object has no attribute 'post'")

    def content(self):
        self._require_client()
        if self._broken:
            raise KeyError("listeModesAff")
        return self._content

    def attachments(self):
        self._require_client()
        return []


def _client(items):
    return SimpleNamespace(information_and_surveys=lambda _date_from: list(items))


DATE_FROM = date(2026, 9, 1)


class TestFormattingHappensWhileTheClientLives:
    def test_returns_plain_dicts(self):
        """No pronotepy object survives into the coordinator's data.

        Everything the sensor reads afterwards is a plain value, so nothing it
        touches can go back to the network.
        """
        items = [_Information("Rentree", datetime(2026, 9, 2, 8, 0))]

        result = get_information_and_surveys(_client(items), DATE_FROM)

        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0]["content"] == "Corps du message"
        assert result[0]["title"] == "Rentree"

    def test_the_result_survives_the_client_being_cut(self):
        """The whole point: the lazy fields are already resolved.

        After the coordinator strips the client references, reading the
        formatted result must still work - it is only strings and dates by
        then.
        """
        item = _Information("Rentree", datetime(2026, 9, 2, 8, 0))
        result = get_information_and_surveys(_client([item]), DATE_FROM)

        item._client = None  # what `_strip_client_refs` does

        assert result[0]["content"] == "Corps du message"
        assert result[0]["attachments"] == []

    def test_reading_the_object_after_the_cut_would_have_failed(self):
        """Pins the stand-in's fidelity, and states the defect.

        If this ever stops raising, the stand-in has drifted from pronotepy
        and the test above proves nothing.
        """
        item = _Information("Rentree", datetime(2026, 9, 2, 8, 0))
        item._client = None

        with pytest.raises(AttributeError):
            item.content()


class TestOrdering:
    def test_newest_first(self):
        items = [
            _Information("Ancienne", datetime(2026, 9, 1, 8, 0)),
            _Information("Recente", datetime(2026, 9, 5, 8, 0)),
            _Information("Intermediaire", datetime(2026, 9, 3, 8, 0)),
        ]

        result = get_information_and_surveys(_client(items), DATE_FROM)

        assert [row["title"] for row in result] == [
            "Recente",
            "Intermediaire",
            "Ancienne",
        ]


class TestOneBadItemDoesNotTakeTheList:
    def test_the_readable_items_are_kept(self):
        items = [
            _Information("Bonne 1", datetime(2026, 9, 5, 8, 0)),
            _Information("Cassee", datetime(2026, 9, 4, 8, 0), broken=True),
            _Information("Bonne 2", datetime(2026, 9, 3, 8, 0)),
        ]

        result = get_information_and_surveys(_client(items), DATE_FROM)

        assert [row["title"] for row in result] == ["Bonne 1", "Bonne 2"]

    def test_the_skip_is_audible_and_names_the_item(self, caplog):
        """A quietly shorter list is worse than a visible warning.

        The title is the only thing that lets a user match the dropped message
        against what they see in Pronote, and it carries no secret.
        """
        caplog.set_level(logging.WARNING, logger=coord.__name__)
        items = [_Information("Cassee", datetime(2026, 9, 4, 8, 0), broken=True)]

        get_information_and_surveys(_client(items), DATE_FROM)

        assert "Cassee" in caplog.text

    def test_the_warning_is_at_warning_level_not_info(self, caplog):
        """`INFO` is not written to `home-assistant.log` by default.

        Reporting this at info would be the same as not reporting it, which is
        what made an empty sensor so hard to investigate.
        """
        caplog.set_level(logging.DEBUG, logger=coord.__name__)
        items = [_Information("Cassee", datetime(2026, 9, 4, 8, 0), broken=True)]

        get_information_and_surveys(_client(items), DATE_FROM)

        levels = {r.levelno for r in caplog.records if "Cassee" in r.getMessage()}
        assert levels == {logging.WARNING}

    def test_an_item_without_a_title_does_not_break_the_reporting(self, caplog):
        """An object broken enough not to format may have no title either."""
        caplog.set_level(logging.WARNING, logger=coord.__name__)

        class _Untitled:
            def content(self):
                raise KeyError("listeModesAff")

            creation_date = datetime(2026, 9, 4, 8, 0)

        get_information_and_surveys(_client([_Untitled()]), DATE_FROM)

        assert "'?'" in caplog.text


class TestNothingToFormat:
    def test_no_information(self):
        assert get_information_and_surveys(_client([]), DATE_FROM) == []
