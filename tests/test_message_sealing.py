"""Targeted tests for one-shot message sealing in JSON formatters.

Covers `BaseJsonFormatter.seal_record_message` and the guarantee that `extra`
processing neither re-evaluates nor overwrites the sealed message.
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import json
import logging
from typing import Any

## Installed

## Application
from pythonjsonlogger.core import RESERVED_ATTRS
from pythonjsonlogger.json import JsonFormatter


### HELPERS
### ============================================================================
class StrCounter:
    """Object that counts how many times `str()` is called on it."""

    def __init__(self, text: str = "counted message") -> None:
        self.text = text
        self.count = 0

    def __str__(self) -> str:
        self.count += 1
        return self.text


def make_record(msg: Any, args: Any = None, **attributes: Any) -> logging.LogRecord:
    """Build a record, attaching `attributes` the way `extra` would."""
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)
    for key, value in attributes.items():
        record.__dict__[key] = value
    return record


def format_message(formatter: JsonFormatter, record: logging.LogRecord) -> dict[str, Any]:
    return json.loads(formatter.format(record))


### TESTS
### ============================================================================
def test_message_is_sealed_with_single_str_call() -> None:
    "A counting object used as `msg` is stringified exactly once."
    counter = StrCounter("counted %s")
    record = make_record(counter, ("value",))

    output = format_message(JsonFormatter("%(message)s"), record)

    assert output["message"] == "counted value"
    assert counter.count == 1


def test_seal_record_message_entry_point() -> None:
    "The sealing entry point interpolates once and stores the result on the record."
    counter = StrCounter()
    record = make_record(counter)
    formatter = JsonFormatter("%(message)s")

    sealed = formatter.seal_record_message(record)

    assert sealed == "counted message"
    assert record.message == "counted message"
    assert counter.count == 1


def test_extra_message_does_not_replace_sealed_message() -> None:
    "A `message` attribute smuggled in via `extra` cannot replace the sealed message."
    # Standard logging rejects extra={"message": ...}; records built outside a
    # logger (e.g. logging.makeLogRecord) may still carry such an attribute.
    record = make_record("real %s", ("message",), message="extra message")
    formatter = JsonFormatter(
        "%(message)s",
        reserved_attrs=[attr for attr in RESERVED_ATTRS if attr != "message"],
    )

    output = format_message(formatter, record)

    assert output["message"] == "real message"


def test_renamed_extra_does_not_overwrite_sealed_message() -> None:
    "An `extra` field renamed to `message` cannot overwrite the sealed message."
    record = make_record("sealed message", note="extra message")
    formatter = JsonFormatter("%(message)s", rename_fields={"note": "message"})

    output = format_message(formatter, record)

    assert output["message"] == "sealed message"


def test_plain_string_message() -> None:
    "Plain string messages are passed through unchanged."
    record = make_record("plain message")

    output = format_message(JsonFormatter("%(message)s"), record)

    assert output["message"] == "plain message"


def test_mapping_args_message() -> None:
    "Mapping args are interpolated following logging's real rules."
    # tuple-wrapped exactly as `logger.info(msg, {"value": 3})` passes them;
    # `LogRecord` normalizes a lone mapping arg to the mapping itself.
    record = make_record("value=%(value)s", ({"value": 3},))

    output = format_message(JsonFormatter("%(message)s"), record)

    assert output["message"] == "value=3"


def test_dict_message_still_supported() -> None:
    "Logging a dict still works and seals an empty message."
    record = make_record({"custom": "field"})

    output = format_message(JsonFormatter("%(message)s"), record)

    assert output["message"] == ""
    assert output["custom"] == "field"
