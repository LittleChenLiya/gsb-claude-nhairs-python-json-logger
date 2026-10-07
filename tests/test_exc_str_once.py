"""Tests for the `exc_str_once` formatter option (single evaluation of exception strings)"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
import json
import logging
import sys
from typing import Any

## Installed

## Application
from pythonjsonlogger.json import JsonFormatter

### SETUP
### ============================================================================
## Small in-memory fixture: records are created in memory and formatted directly
## so that no logging handler (including pytest's log capture handlers) can
## evaluate the exception under test.
_LOGGER = logging.getLogger("pythonjsonlogger.tests.exc_str_once")


def render_log(
    formatter: logging.Formatter,
    msg: Any,
    args: Any = None,
    exc_info: Any = None,
    extra: dict[str, Any] | None = None,
    level: int = logging.ERROR,
) -> Any:
    """Format a single in-memory log record and return the decoded JSON output"""
    # normalize exc_info the same way logging.Logger._log does
    if exc_info:
        if isinstance(exc_info, BaseException):
            exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
        elif not isinstance(exc_info, tuple):
            exc_info = sys.exc_info()
    # logger methods receive args via *args, so a lone mapping arrives as a 1-tuple
    if isinstance(args, dict):
        args = (args,)
    record = _LOGGER.makeRecord(
        _LOGGER.name,
        level,
        __file__,
        1,
        msg,
        args,
        exc_info,
        extra=extra,
    )
    return json.loads(formatter.format(record))


class CountingException(Exception):
    """Exception that counts how many times its `__str__` is evaluated"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.str_calls = 0
        return

    def __str__(self) -> str:
        self.str_calls += 1
        return "counted-message"


class BrokenStrException(Exception):
    """Exception whose `__str__` always raises"""

    def __init__(self) -> None:
        super().__init__("unreachable")
        self.str_calls = 0
        return

    def __str__(self) -> str:
        self.str_calls += 1
        raise RuntimeError("__str__ exploded")


### TESTS
### ============================================================================
## Counting exception: single evaluation shared between message and exc_info
## -----------------------------------------------------------------------------
def test_exc_str_once_shares_evaluation_with_args() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True)

    exc = CountingException("boom")
    try:
        raise exc
    except CountingException:
        output = render_log(formatter, "failed: %s", (exc,), exc_info=True)

    assert exc.str_calls == 1
    assert output["message"] == "failed: counted-message"
    assert output["exc_info"].startswith("Traceback (most recent call last):")
    assert output["exc_info"].endswith("CountingException: counted-message")
    return


def test_exc_str_once_shares_evaluation_with_exception_as_message() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True)

    exc = CountingException("boom")
    try:
        raise exc
    except CountingException:
        output = render_log(formatter, exc, exc_info=True)

    assert exc.str_calls == 1
    assert output["message"] == "counted-message"
    assert output["exc_info"].endswith("CountingException: counted-message")
    return


def test_exc_str_once_shares_evaluation_with_dict_args() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True)

    exc = CountingException("boom")
    try:
        raise exc
    except CountingException:
        output = render_log(formatter, "failed: %(err)s", {"err": exc}, exc_info=True)

    assert exc.str_calls == 1
    assert output["message"] == "failed: counted-message"
    assert output["exc_info"].endswith("CountingException: counted-message")
    return


def test_exc_str_once_with_exc_info_as_array() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True, exc_info_as_array=True)

    exc = CountingException("boom")
    try:
        raise exc
    except CountingException:
        output = render_log(formatter, "failed: %s", (exc,), exc_info=True)

    assert exc.str_calls == 1
    assert output["message"] == "failed: counted-message"
    assert isinstance(output["exc_info"], list)
    assert output["exc_info"][0] == "Traceback (most recent call last):"
    assert output["exc_info"][-1].endswith("CountingException: counted-message")
    return


## Broken __str__: explicit placeholder, evaluated at most once
## -----------------------------------------------------------------------------
def test_exc_str_once_broken_str_uses_placeholder() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True)

    exc = BrokenStrException()
    try:
        raise exc
    except BrokenStrException:
        output = render_log(formatter, "failed: %s", (exc,), exc_info=True)

    # __str__ is attempted exactly once and never re-triggered
    assert exc.str_calls == 1
    # explicit placeholder retaining the type name in both fields
    assert output["message"] == "failed: <BrokenStrException __str__() failed>"
    assert output["exc_info"].endswith("BrokenStrException: <BrokenStrException __str__() failed>")
    return


def test_exc_str_once_broken_str_with_exception_as_message() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True)

    exc = BrokenStrException()
    try:
        raise exc
    except BrokenStrException:
        output = render_log(formatter, exc, exc_info=True)

    assert exc.str_calls == 1
    assert output["message"] == "<BrokenStrException __str__() failed>"
    assert output["exc_info"].endswith("BrokenStrException: <BrokenStrException __str__() failed>")
    return


## Normal exceptions and logs without exceptions
## -----------------------------------------------------------------------------
def test_exc_str_once_normal_exception_matches_default() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    once_formatter = JsonFormatter("%(message)s", exc_str_once=True)
    default_formatter = JsonFormatter("%(message)s")

    once_output = render_log(once_formatter, "oops", exc_info=exc_info)
    default_output = render_log(default_formatter, "oops", exc_info=exc_info)

    assert once_output["message"] == default_output["message"] == "oops"
    assert once_output["exc_info"] == default_output["exc_info"]
    assert once_output["exc_info"].endswith("ValueError: boom")
    return


def test_exc_str_once_chained_exception() -> None:
    formatter = JsonFormatter("%(message)s", exc_str_once=True)

    cause = CountingException("cause")
    main = CountingException("main")
    try:
        try:
            raise cause
        except CountingException:
            raise main
    except CountingException:
        output = render_log(formatter, "failed", exc_info=True)

    # each exception in the chain is evaluated exactly once
    assert cause.str_calls == 1
    assert main.str_calls == 1
    assert "During handling of the above exception, another exception occurred" in (
        output["exc_info"]
    )
    assert output["exc_info"].count("counted-message") == 2
    assert output["exc_info"].endswith("CountingException: counted-message")
    return


def test_exc_str_once_no_exception_log_unaffected() -> None:
    formatter = JsonFormatter("%(levelname)s %(message)s", exc_str_once=True)

    output = render_log(
        formatter, "hello %s", ("world",), extra={"extra_field": 42}, level=logging.INFO
    )

    assert output == {"levelname": "INFO", "message": "hello world", "extra_field": 42}
    return


## Default interface regression (option disabled)
## -----------------------------------------------------------------------------
def test_default_formatter_evaluates_exception_per_field() -> None:
    formatter = JsonFormatter("%(message)s")

    exc = CountingException("boom")
    try:
        raise exc
    except CountingException:
        output = render_log(formatter, "failed: %s", (exc,), exc_info=True)

    # default behaviour is unchanged: one evaluation for the message, one for exc_info
    assert exc.str_calls == 2
    assert output["message"] == "failed: counted-message"
    assert output["exc_info"].endswith("CountingException: counted-message")
    return


def test_default_formatter_no_exception_log_unaffected() -> None:
    formatter = JsonFormatter("%(levelname)s %(message)s")

    output = render_log(
        formatter, "hello %s", ("world",), extra={"extra_field": 42}, level=logging.INFO
    )

    assert output == {"levelname": "INFO", "message": "hello world", "extra_field": 42}
    return
