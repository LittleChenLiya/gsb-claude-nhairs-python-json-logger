"""Tests for nested value redaction via explicit field paths.

*New in 4.2.1*
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import copy
import io
import json
import logging
import logging.config
from typing import Any

## Installed
import pytest

## Application
import pythonjsonlogger
from pythonjsonlogger.core import BaseJsonFormatter
from pythonjsonlogger.exception import PythonJsonLoggerError, RedactionError
from pythonjsonlogger.json import JsonFormatter

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


@pytest.fixture
def formatter_cls(request: pytest.FixtureRequest) -> type[BaseJsonFormatter]:
    return request.param  # type: ignore[no-any-return]


def make_logger(
    formatter: BaseJsonFormatter,
) -> tuple[logging.Logger, io.StringIO]:
    logger = logging.getLogger(f"pythonjsonlogger.tests.redaction.{id(formatter)}")
    logger.setLevel(logging.DEBUG)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, buffer


def make_record(msg: Any = "x", **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


### TESTS
### ============================================================================
@pytest.mark.parametrize("formatter_cls", ALL_FORMATTERS, indirect=True)
def test_nested_list_target_same_name_nontarget_and_record_unchanged(
    formatter_cls: type[BaseJsonFormatter],
) -> None:
    """Nested list target fields are redacted; same-named non-target fields are
    kept, and the original record data is not modified.
    """
    payload = {
        "users": [
            {"name": "Ada", "secret": "111-22-3333"},
            {"name": "Grace", "secret": "444-55-6666"},
        ],
        # same field name but not at the configured path: must be untouched
        "secret": "top-level-secret",
        "audit": {"secret": "nested-non-target-secret"},
    }
    payload_snapshot = copy.deepcopy(payload)

    formatter = formatter_cls(
        redact_fields=[
            ("data", "users", 0, "secret"),
            ("data", "users", 1, "secret"),
        ]
    )
    logger, buffer = make_logger(formatter)
    logger.info("login event", extra={"data": payload})

    result = json.loads(buffer.getvalue())

    # targeted values inside the nested list are redacted
    assert result["data"]["users"][0]["secret"] == "[REDACTED]"
    assert result["data"]["users"][1]["secret"] == "[REDACTED]"
    # non-target values are kept verbatim
    assert result["data"]["users"][0]["name"] == "Ada"
    assert result["data"]["users"][1]["name"] == "Grace"
    assert result["data"]["secret"] == "top-level-secret"
    assert result["data"]["audit"]["secret"] == "nested-non-target-secret"

    # original record data is not modified
    assert payload == payload_snapshot


@pytest.mark.parametrize("formatter_cls", ALL_FORMATTERS, indirect=True)
def test_missing_paths_do_not_add_fields(
    formatter_cls: type[BaseJsonFormatter],
) -> None:
    payload: dict[str, Any] = {
        "users": [{"name": "Ada"}],  # no "secret" key; second item missing
        "present": {"secret": "shh"},
    }
    payload_snapshot = copy.deepcopy(payload)

    formatter = formatter_cls(
        redact_fields=[
            ("data", "users", 0, "secret"),
            ("data", "users", 1, "secret"),
            ("data", "missing", "deep", "path"),
            ("data", "present", "secret"),
        ]
    )
    logger, buffer = make_logger(formatter)
    logger.info("x", extra={"data": payload})

    result = json.loads(buffer.getvalue())

    # missing keys/indices are not created
    assert "secret" not in result["data"]["users"][0]
    assert len(result["data"]["users"]) == 1
    assert "missing" not in result["data"]
    # a present path is still redacted
    assert result["data"]["present"]["secret"] == "[REDACTED]"

    assert payload == payload_snapshot


@pytest.mark.parametrize("formatter_cls", ALL_FORMATTERS, indirect=True)
def test_circular_reference_raises_library_error_and_keeps_record(
    formatter_cls: type[BaseJsonFormatter],
) -> None:
    cyclic: dict[str, Any] = {"name": "loop"}
    cyclic["self"] = cyclic

    formatter = formatter_cls(redact_fields=[("data", "self", "name")])
    record = make_record(data=cyclic)

    with pytest.raises(RedactionError) as exc_info:
        formatter.format(record)

    assert isinstance(exc_info.value, PythonJsonLoggerError)
    assert "Circular reference" in str(exc_info.value)

    # original record data is not modified after the failed format
    assert cyclic["name"] == "loop"
    assert cyclic["self"] is cyclic

    # formatter keeps working for subsequent, non-cyclic records
    _, buffer = make_logger(formatter)
    record2 = make_record(data={"self": {"name": "fine"}})
    buffer.write(formatter.format(record2))
    result = json.loads(buffer.getvalue())
    assert result["data"]["self"]["name"] == "[REDACTED]"


def test_circular_reference_outside_target_paths_is_also_reported() -> None:
    """Non-target cycles must also produce this library's error, not an
    encoder error, and must not mutate the record."""
    cyclic: list[Any] = []
    cyclic.append(cyclic)

    formatter = JsonFormatter(redact_fields=[("data", "other")])
    with pytest.raises(RedactionError):
        formatter.format(make_record(data={"loop": cyclic, "other": "value"}))

    assert cyclic[0] is cyclic


def test_modified_containers_are_copied_and_others_shared() -> None:
    untouched = {"keep": "me"}
    other_list = [1, 2]
    target: dict[str, Any] = {
        "users": [{"secret": "s", "name": "a"}],
        "untouched": untouched,
        "other_list": other_list,
    }

    formatter = JsonFormatter(redact_fields=[("users", 0, "secret")])
    # exercise the redaction stage directly (identity does not survive JSON)
    redacted = formatter.redact_log_data(target)

    assert redacted["users"][0]["secret"] == "[REDACTED]"
    # modified containers are copies ...
    assert redacted is not target
    assert redacted["users"] is not target["users"]
    assert redacted["users"][0] is not target["users"][0]
    # ... while unchanged containers are shared
    assert redacted["untouched"] is untouched
    assert redacted["other_list"] is other_list
    # source containers unmodified
    assert target["users"][0]["secret"] == "s"


@pytest.mark.parametrize("formatter_cls", ALL_FORMATTERS, indirect=True)
def test_default_encoder_still_used_without_redaction(
    formatter_cls: type[BaseJsonFormatter],
) -> None:
    """Short regression for the original interface: with no redaction
    configured the normal encoding path is used unchanged."""
    formatter = formatter_cls()
    logger, buffer = make_logger(formatter)
    logger.info("plain %s", "message", extra={"n": 3})

    result = json.loads(buffer.getvalue())
    assert result["message"] == "plain message"
    assert result["n"] == 3


def test_dict_message_is_redacted_and_not_mutated() -> None:
    msg = {"event": "login", "user": {"ssn": "123"}}
    msg_snapshot = copy.deepcopy(msg)

    formatter = JsonFormatter(redact_fields=[("user", "ssn")])
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    result = json.loads(formatter.format(record))
    assert result["user"]["ssn"] == "[REDACTED]"
    assert result["event"] == "login"
    assert msg == msg_snapshot


def test_custom_redact_value() -> None:
    formatter = JsonFormatter(redact_fields=[("data", "secret")], redact_value=None)
    result = json.loads(formatter.format(make_record(data={"secret": "shh", "ok": 1})))
    assert result["data"]["secret"] is None
    assert result["data"]["ok"] == 1


def test_path_prefix_and_nested_path_coexist() -> None:
    """A configured path can be a prefix of another: the scalar at the
    shorter path is redacted and the deeper field is redacted too."""
    formatter = JsonFormatter(
        redact_fields=[("data", "p"), ("data", "p", "deep")],
        redact_value="X",
    )
    # "p" is a container: only the deeper target is replaced
    result = json.loads(formatter.format(make_record(data={"p": {"deep": "d", "keep": "k"}})))
    assert result["data"]["p"] == {"deep": "X", "keep": "k"}

    # "p" itself is a scalar and a terminal: it is replaced directly
    result2 = json.loads(formatter.format(make_record(data={"p": "scalar"})))
    assert result2["data"]["p"] == "X"


def test_invalid_redact_fields() -> None:
    with pytest.raises(TypeError):
        JsonFormatter(redact_fields=["users"])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        JsonFormatter(redact_fields=[()])
    with pytest.raises(TypeError):
        JsonFormatter(redact_fields=[(1.5,)])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        JsonFormatter(redact_fields=[("ok", True)])  # type: ignore[list-item]


def test_dictconfig_redaction(tmp_path: Any) -> None:
    """Redaction config survives logging.config.dictConfig."""
    log_file = tmp_path / "out.log"
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "redact_fields": [("data", "users", 0, "secret")],
            }
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": str(log_file),
                "formatter": "json",
            }
        },
        "loggers": {"dictconfig.redaction": {"level": "INFO", "handlers": ["file"]}},
    }
    logging.config.dictConfig(config)
    logger = logging.getLogger("dictconfig.redaction")
    payload = {"users": [{"secret": "s", "name": "a"}]}
    logger.info("x", extra={"data": payload})

    for handler in logger.handlers:
        handler.flush()

    result = json.loads(log_file.read_text(encoding="utf-8"))
    assert result["data"]["users"][0]["secret"] == "[REDACTED]"
    assert payload["users"][0]["secret"] == "s"
