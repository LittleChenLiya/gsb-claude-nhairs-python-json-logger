"""Tests for nested field redaction (`redact_fields`).

Covers explicit paths through nested lists/dicts, same-named non-target
fields, missing paths, circular references and preservation of the original
`LogRecord` data.
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import copy
import datetime
import json
import logging
from typing import Any

## Installed
import pytest

## Application
import pythonjsonlogger
from pythonjsonlogger import JsonFormatError, DEFAULT_REDACTION_VALUE
from pythonjsonlogger.core import BaseJsonFormatter
from pythonjsonlogger.json import JsonFormatter
from pythonjsonlogger import redaction

if pythonjsonlogger.ORJSON_AVAILABLE:
    from pythonjsonlogger.orjson import OrjsonFormatter

if pythonjsonlogger.MSGSPEC_AVAILABLE:
    from pythonjsonlogger.msgspec import MsgspecFormatter


### SETUP
### ============================================================================
ALL_FORMATTERS: list[type[BaseJsonFormatter]] = [JsonFormatter]
if pythonjsonlogger.ORJSON_AVAILABLE:
    ALL_FORMATTERS.append(OrjsonFormatter)
if pythonjsonlogger.MSGSPEC_AVAILABLE:
    ALL_FORMATTERS.append(MsgspecFormatter)


def make_record(payload: dict[str, Any]) -> logging.LogRecord:
    """Create a LogRecord carrying `payload` as extra attributes."""
    record = logging.LogRecord(
        name="pythonjsonlogger.tests.redaction",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg="message",
        args=None,
        exc_info=None,
    )
    for key, value in payload.items():
        setattr(record, key, value)
    return record


def make_message_record(message_dict: dict[str, Any]) -> logging.LogRecord:
    """Create a LogRecord whose `msg` is a dict."""
    return logging.LogRecord(
        name="pythonjsonlogger.tests.redaction",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg=message_dict,
        args=None,
        exc_info=None,
    )


### FORMATTER TESTS
### ============================================================================
@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_nested_list_target_field(class_: type[BaseJsonFormatter]) -> None:
    """An explicit path through a list redacts the field in every element."""
    payload = {
        "accounts": [
            {"user": "alice", "password": "alice-secret"},
            {"user": "bob", "password": "bob-secret", "extra": [{"password": "deep"}]},
        ]
    }
    formatter = class_(redact_fields=["accounts.password", ("accounts", "extra", "password")])

    output = json.loads(formatter.format(make_record(payload)))

    assert output["accounts"][0] == {"user": "alice", "password": DEFAULT_REDACTION_VALUE}
    assert output["accounts"][1]["password"] == DEFAULT_REDACTION_VALUE
    assert output["accounts"][1]["extra"][0]["password"] == DEFAULT_REDACTION_VALUE
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_same_named_non_target_fields_untouched(
    class_: type[BaseJsonFormatter],
) -> None:
    """Only fields matching the full explicit path are redacted."""
    payload = {
        "password": "top-secret",
        "meta": {"password": "meta-secret"},
        "accounts": [{"user": "alice", "password": "target-secret"}],
    }
    formatter = class_(redact_fields={"accounts.password": "[redacted]"})

    output = json.loads(formatter.format(make_record(payload)))

    assert output["password"] == "top-secret"
    assert output["meta"]["password"] == "meta-secret"
    assert output["accounts"][0]["password"] == "[redacted]"
    assert output["accounts"][0]["user"] == "alice"
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_missing_path_does_not_add_field(
    class_: type[BaseJsonFormatter],
) -> None:
    """A path that cannot be resolved never introduces a new field."""
    payload = {"accounts": [{"user": "alice"}], "other": {"nested": [1, 2]}}
    formatter = class_(redact_fields=["accounts.password", "missing.deeply.path"])

    output = json.loads(formatter.format(make_record(payload)))

    assert output["accounts"][0] == {"user": "alice"}
    assert "missing" not in output
    assert output["other"] == {"nested": [1, 2]}
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_circular_value_raises_library_error(
    class_: type[BaseJsonFormatter],
) -> None:
    """A circular container encountered during traversal raises JsonFormatError."""
    cyclic: dict[str, Any] = {}
    cyclic["loop"] = cyclic
    payload = {"accounts": [{"password": "secret"}], "bad": cyclic}
    record = make_record(payload)
    formatter = class_(redact_fields=["accounts.password"])

    with pytest.raises(JsonFormatError):
        formatter.format(record)

    # And through a list cycle
    cyclic_list: list[Any] = []
    cyclic_list.append(cyclic_list)
    record2 = make_record({"accounts": [{"password": "secret"}], "bad": cyclic_list})
    with pytest.raises(JsonFormatError):
        formatter.format(record2)
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_does_not_modify_record(class_: type[BaseJsonFormatter]) -> None:
    """Redaction copies modified containers; LogRecord data is left intact."""
    payload: dict[str, Any] = {
        "accounts": [
            {"user": "alice", "password": "secret", "prefs": {"theme": "dark"}},
            {"user": "bob", "password": "secret2"},
        ]
    }
    snapshot = copy.deepcopy(payload)
    record = make_record(payload)
    formatter = class_(redact_fields=["accounts.password"])

    json.loads(formatter.format(record))

    accounts = getattr(record, "accounts")
    assert accounts == snapshot["accounts"]
    # Identity checks: containers that didn't need changes are the same objects
    assert accounts is payload["accounts"]
    assert accounts[0] is payload["accounts"][0]
    assert accounts[0]["prefs"] is payload["accounts"][0]["prefs"]
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_dict_msg_does_not_modify_caller_dict(
    class_: type[BaseJsonFormatter],
) -> None:
    """When `record.msg` is a dict, the caller's dict must not be mutated."""
    message_dict = {"event": "login", "users": [{"name": "a", "token": "t"}]}
    record = make_message_record(message_dict)
    formatter = class_(redact_fields={"users.token": None})

    output = json.loads(formatter.format(record))

    assert output["users"][0]["token"] is None
    assert message_dict["users"][0]["token"] == "t"
    assert getattr(record, "message") == ""
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_redact_dict_msg_cycle_raises_and_preserves(class_: type[BaseJsonFormatter]) -> None:
    """A cycle inside a dict msg raises and the caller dict is untouched."""
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    message_dict = {"users": [{"token": "t"}], "bad": cyclic}
    record = make_message_record(message_dict)
    formatter = class_(redact_fields=["users.token"])

    with pytest.raises(JsonFormatError):
        formatter.format(record)

    assert message_dict["users"][0]["token"] == "t"
    return


@pytest.mark.parametrize("class_", ALL_FORMATTERS)
def test_normal_encoding_still_works(class_: type[BaseJsonFormatter]) -> None:
    """Redaction coexists with the ordinary encoder (datetimes etc.)."""
    record = make_record({"when": datetime.datetime(2026, 10, 7, 12, 0, 0)})
    formatter = class_(redact_fields=["nope.not.there"])

    output = json.loads(formatter.format(record))

    assert isinstance(output["when"], str)
    assert output["message"] == "message"
    return


def test_redact_without_configuration_returns_same_object() -> None:
    """With no redaction configured the log data object passes through untouched."""
    formatter = JsonFormatter()
    log_data = {"a": [1, 2, 3], "b": {"c": 4}}
    assert formatter.redact_log_data(log_data) is log_data
    return


def test_redact_no_change_returns_same_containers() -> None:
    """Copy-on-write: when no path matches, original containers are returned."""
    tree = redaction.build_redaction_tree(["a.password"])
    data = {"a": [{"user": "x"}], "b": {"password": "untouched"}}
    result = redaction.redact_log_data(data, tree)
    assert result is data
    assert result["a"] is data["a"]
    assert result["b"] is data["b"]
    return


def test_redact_custom_replacements() -> None:
    """Mapping config supports arbitrary JSON-safe replacement values."""
    tree = redaction.build_redaction_tree(
        {"users.password": None, "users.token": 0, "users.name": "hidden"}
    )
    data = {"users": [{"password": "p", "token": "t", "name": "n"}]}
    result = redaction.redact_log_data(data, tree)
    assert result["users"][0] == {"password": None, "token": 0, "name": "hidden"}
    # original unchanged
    assert data["users"][0] == {"password": "p", "token": "t", "name": "n"}
    return


def test_redact_invalid_path_raises_value_error() -> None:
    """Empty/invalid path configurations fail fast when the formatter is built."""
    with pytest.raises(ValueError):
        JsonFormatter(redact_fields=[""])
    with pytest.raises(ValueError):
        JsonFormatter(redact_fields=[()])
    with pytest.raises(TypeError):
        JsonFormatter(redact_fields={123: "x"})  # type: ignore[dict-item]
    return
