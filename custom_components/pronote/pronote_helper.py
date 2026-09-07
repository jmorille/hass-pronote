"""Client wrapper for the Pronote integration."""

### Hotfix for python 3.13/3.14 https://github.com/bain3/pronotepy/pull/317#issuecomment-2523257656
import autoslot
import dis


def assignments_to_self(method) -> set:
    instance_var = next(iter(method.__code__.co_varnames), "self")
    instructions = list(dis.Bytecode(method))
    names = set()
    for i, inst in enumerate(instructions):
        if inst.opname == "STORE_ATTR" and i > 0:
            prev = instructions[i - 1]
            # Handle any LOAD_FAST variant (LOAD_FAST, LOAD_FAST_BORROW, etc.) or LOAD_DEREF
            is_load = "LOAD_FAST" in prev.opname or prev.opname == "LOAD_DEREF"
            if not is_load:
                continue
            if isinstance(prev.argval, tuple):
                # Combined instruction (e.g. LOAD_FAST_LOAD_FAST), self is in the tuple
                if instance_var in prev.argval:
                    names.add(inst.argval)
            elif prev.argval == instance_var:
                names.add(inst.argval)
    return names


autoslot.assignments_to_self = assignments_to_self
### End Hotfix

import pronotepy

### Hotfix for pronotepy 2.15.6 login on migrated PRONOTE instances
### https://github.com/delphiki/hass-pronote/issues/172
from . import pronotepy_hotfix  # noqa: F401
### End Hotfix

import json
import logging
import re

_LOGGER = logging.getLogger(__name__)


def get_pronote_client(data) -> pronotepy.Client | pronotepy.ParentClient | None:
    _LOGGER.debug(f"Coordinator uses connection: {data['connection_type']}")

    if data["connection_type"] == "qrcode":
        client = get_client_from_qr_code(data)
    else:
        client = get_client_from_username_password(data)

    if client is None:
        _LOGGER.warning("Client creation failed")
        return None

    try:
        client.session_check()
    except Exception as e:
        # Not fatal: the client is returned and the fetch may still work.
        _LOGGER.warning(
            "Pronote session check failed, continuing with the client anyway: %s", e
        )

    return client


def get_client_from_username_password(
    data,
) -> pronotepy.Client | pronotepy.ParentClient | None:
    url = data["url"]
    url = re.sub(r"/[^/]+\.html$", "/", url)
    if not url.endswith("/"):
        url += "/"
    url = url + ("parent" if data["account_type"] == "parent" else "eleve") + ".html"

    ent = None
    if "ent" in data:
        ent = getattr(pronotepy.ent, data["ent"])

    if not ent:
        url += "?login=true"

    try:
        client = (
            pronotepy.ParentClient
            if data["account_type"] == "parent"
            else pronotepy.Client
        )(
            pronote_url=url,
            username=data["username"],
            password=data["password"],
            account_pin=data.get("account_pin", None),
            device_name=data.get("device_name", None),
            client_identifier=data.get("client_identifier", None),
            ent=ent,
        )
        del ent
        del client.account_pin
        _LOGGER.debug("Logged in as %s", client.info.name)
    except Exception as err:
        # debug, not error: called on every refresh, and returning None lets
        # the coordinator raise UpdateFailed, which logs once on the transition.
        _LOGGER.debug(
            "Pronote login failed for %s (%s account%s): %s",
            url,
            data["account_type"],
            f", ENT {data['ent']}" if data.get("ent") else "",
            err,
            exc_info=True,
        )
        return None

    return client


def get_client_from_qr_code(data) -> pronotepy.Client | pronotepy.ParentClient | None:

    if "qr_code_json" in data:  # first login from QR Code JSON

        # login with qrcode json
        try:
            qr_code_json = json.loads(data["qr_code_json"])
        except ValueError as err:
            # The payload holds the enrolment secret, so it stays out of the log.
            _LOGGER.error("QR-code payload is not valid JSON: %s", err)
            return None

        qr_code_pin = data["qr_code_pin"]
        uuid = data["qr_code_uuid"]

        # get the initial client using qr_code
        try:
            client = (
                pronotepy.ParentClient
                if data["account_type"] == "parent"
                else pronotepy.Client
            ).qrcode_login(
                qr_code=qr_code_json,
                pin=qr_code_pin,
                uuid=uuid,
                account_pin=data.get("account_pin", None),
                client_identifier=data.get("client_identifier", None),
                device_name=data.get("device_name", None),
            )
        except Exception:
            # Unguarded this reached data_entry_flow as "Unknown error occurred"
            # (#128). No QR payload, no PIN, no token in the message.
            _LOGGER.exception(
                "Pronote QR-code enrolment failed (%s account, pin=%s, device=%s)",
                data["account_type"],
                "set" if data.get("account_pin") else "unset",
                data.get("device_name"),
            )
            return None

        qr_code_url = client.pronote_url
        qr_code_username = client.username
        qr_code_password = client.password
        qr_code_uuid = client.uuid
        qr_code_account_pin = client.account_pin
        qr_code_device_name = client.device_name
        qr_code_client_identifier = client.client_identifier
    else:
        qr_code_url = data["qr_code_url"]
        qr_code_username = data["qr_code_username"]
        qr_code_password = data["qr_code_password"]
        qr_code_uuid = data.get("uuid", data["qr_code_uuid"])
        qr_code_account_pin = data.get("account_pin", None)
        qr_code_device_name = data.get("device_name", None)
        qr_code_client_identifier = data.get("client_identifier", None)

    # Tells "no token stored" from "token refused" without logging the token.
    _LOGGER.debug(
        "QR-code login: url=%s, uuid=%s, token=%d chars, pin=%s, device=%s",
        qr_code_url,
        qr_code_uuid,
        len(qr_code_password or ""),
        "set" if qr_code_account_pin else "unset",
        qr_code_device_name,
    )

    try:
        return (
            pronotepy.ParentClient
            if data["account_type"] == "parent"
            else pronotepy.Client
        ).token_login(
            pronote_url=qr_code_url,
            username=qr_code_username,
            password=qr_code_password,
            uuid=qr_code_uuid,
            account_pin=qr_code_account_pin,
            device_name=qr_code_device_name,
            client_identifier=qr_code_client_identifier,
        )
    except Exception as err:
        # Same contract as the username/password path: None, and one log line
        # at debug rather than a traceback on every refresh.
        _LOGGER.debug(
            "Pronote QR-code login failed for %s: %s", qr_code_url, err, exc_info=True
        )
        return None


def get_day_start_at(lessons):
    day_start_at = None

    if lessons is not None:
        for lesson in lessons:
            if not lesson.canceled:
                day_start_at = lesson.start
                break

    return day_start_at
