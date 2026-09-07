"""Tests that no credential reaches the log.

`home-assistant.log` is what users attach to bug reports, so these tests
assert the negative: a given secret does *not* appear. They run at DEBUG, the
most verbose level a user can enable here.

The guarantee covers this integration's own messages, not a dependency's:
`str(err)` is interpolated. Checked in pronotepy 2.15.6 - no `raise` in
`clients.py`, `pronoteAPI.py` or `ent/` quotes a username or password.
"""

import json
import logging
from unittest.mock import patch

import pytest

from custom_components.pronote.pronote_helper import (
    get_client_from_qr_code,
    get_client_from_username_password,
    get_pronote_client,
)

PASSWORD = "correct-horse-battery-staple"
QR_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef"
QR_PIN = "4291"
ACCOUNT_PIN = "1337"

UP_DATA = {
    "connection_type": "username_password",
    "url": "https://0000000a.index-education.net/pronote/parent.html",
    "username": "parent.dupont",
    "password": PASSWORD,
    "account_type": "parent",
    "account_pin": ACCOUNT_PIN,
    "device_name": "Home Assistant",
}

TOKEN_DATA = {
    "connection_type": "qrcode",
    "qr_code_url": "https://0000000a.index-education.net/pronote/mobile.parent.html",
    "qr_code_username": "parent.dupont",
    "qr_code_password": QR_TOKEN,
    "qr_code_uuid": "8f14e45fceea167a5a36dedd4bea2543",
    "account_type": "parent",
    "account_pin": ACCOUNT_PIN,
    "device_name": "Home Assistant",
}

QR_JSON_DATA = {
    "connection_type": "qrcode",
    "qr_code_json": json.dumps(
        {"jeton": QR_TOKEN, "login": "parent.dupont", "url": "x"}
    ),
    "qr_code_pin": QR_PIN,
    "qr_code_uuid": "8f14e45fceea167a5a36dedd4bea2543",
    "account_type": "parent",
}

# Every secret that must never be written, whatever the code path.
SECRETS = (PASSWORD, QR_TOKEN, QR_PIN, ACCOUNT_PIN)


def _assert_no_secret(caplog):
    """Fail naming the secret that leaked, not just that one did."""
    for secret in SECRETS:
        assert secret not in caplog.text, f"{secret!r} was written to the log"


@pytest.fixture
def at_debug(caplog):
    """Capture this integration's records at DEBUG."""
    caplog.set_level(logging.DEBUG, logger="custom_components.pronote.pronote_helper")
    return caplog


class TestUsernamePasswordPath:
    def test_password_absent_when_the_login_fails(self, at_debug):
        with patch(
            "pronotepy.ParentClient", side_effect=Exception("boom")
        ):
            assert get_client_from_username_password(dict(UP_DATA)) is None
        _assert_no_secret(at_debug)

    def test_the_failure_is_still_diagnosable(self, at_debug):
        """Redaction must not leave the user with nothing."""
        with patch("pronotepy.ParentClient", side_effect=Exception("boom")):
            get_client_from_username_password(dict(UP_DATA))

        assert "index-education.net" in at_debug.text
        assert "parent" in at_debug.text

class TestTokenPath:
    def test_token_absent_when_the_login_fails(self, at_debug):
        with patch("pronotepy.ParentClient") as client:
            client.token_login.side_effect = Exception("token refused")
            assert get_client_from_qr_code(dict(TOKEN_DATA)) is None
        _assert_no_secret(at_debug)

    def test_the_token_length_is_logged_instead_of_the_token(self, at_debug):
        """Length answers "is a token stored at all" without disclosing it."""
        with patch("pronotepy.ParentClient") as client:
            client.token_login.side_effect = Exception("token refused")
            get_client_from_qr_code(dict(TOKEN_DATA))

        assert f"token={len(QR_TOKEN)} chars" in at_debug.text
        assert QR_TOKEN not in at_debug.text

    def test_pin_is_reported_as_set_or_unset_only(self, at_debug):
        with patch("pronotepy.ParentClient") as client:
            client.token_login.side_effect = Exception("token refused")
            get_client_from_qr_code(dict(TOKEN_DATA))

        assert "pin=set" in at_debug.text
        assert ACCOUNT_PIN not in at_debug.text


class TestQrCodeEnrolmentPath:
    def test_enrolment_failure_returns_none_instead_of_raising(self, at_debug):
        """Issue #128: this path had no handler and escaped to the config flow."""
        with patch("pronotepy.ParentClient") as client:
            client.qrcode_login.side_effect = Exception("ENT host did not resolve")
            assert get_client_from_qr_code(dict(QR_JSON_DATA)) is None

    def test_no_qr_payload_or_pin_in_the_log(self, at_debug):
        with patch("pronotepy.ParentClient") as client:
            client.qrcode_login.side_effect = Exception("ENT host did not resolve")
            get_client_from_qr_code(dict(QR_JSON_DATA))

        _assert_no_secret(at_debug)
        # The decoded JSON must not be dumped either.
        assert "jeton" not in at_debug.text


class TestGetPronoteClient:
    def test_no_secret_from_the_dispatching_wrapper(self, at_debug):
        with patch("pronotepy.ParentClient", side_effect=Exception("boom")):
            assert get_pronote_client(dict(UP_DATA)) is None
        _assert_no_secret(at_debug)

    def test_a_failed_session_check_is_not_fatal(self, at_debug):
        """The client is returned even if the session check complains."""
        with patch("pronotepy.ParentClient") as client_cls:
            instance = client_cls.return_value
            instance.session_check.side_effect = Exception("session stale")
            instance.info.name = "ENFANT Prenom"

            assert get_pronote_client(dict(UP_DATA)) is instance

        assert "continuing with the client anyway" in at_debug.text
        _assert_no_secret(at_debug)
