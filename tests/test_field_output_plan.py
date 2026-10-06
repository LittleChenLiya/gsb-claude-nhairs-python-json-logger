"""Targeted tests for the field output plan.

Covers:
- two sources mapping to the same final name are rejected
- the serializer is never called (count of 0) when a plan is rejected
- valid mappings produce deterministic output
- supplied input mappings and record fields are not rewritten

See `pythonjsonlogger.core.FieldOutputPlan` for the small mapping examples.
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from collections.abc import Generator
import io
import json
import logging
from typing import Any

## Installed
import pytest

## Application
from pythonjsonlogger.core import FieldOutputPlan
from pythonjsonlogger.json import JsonFormatter


### FIXTURES
### ============================================================================
@pytest.fixture
def stream() -> Generator[io.StringIO]:
    buffer = io.StringIO()
    yield buffer
    buffer.close()
    return


@pytest.fixture
def logger(stream: io.StringIO) -> Generator[logging.Logger]:
    test_logger = logging.getLogger("tests.test_field_output_plan")
    test_logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(stream)
    test_logger.addHandler(handler)
    yield test_logger
    test_logger.removeHandler(handler)
    test_logger.setLevel(logging.NOTSET)
    return


def load_json(stream: io.StringIO) -> Any:
    return json.loads(stream.getvalue())


### SMALL MAPPING EXAMPLES (FieldOutputPlan)
### ============================================================================
def test_plan_valid_mapping_examples() -> None:
    # Rename a source field to a final output name.
    plan = FieldOutputPlan(rename_fields={"levelname": "LEVEL"})
    assert plan.rename_fields == {"levelname": "LEVEL"}
    assert plan.static_fields == {}
    assert plan.get_rename("levelname") == "LEVEL"

    # A static field whose key is renamed is planned under its final name.
    plan = FieldOutputPlan(rename_fields={"svc": "service.name"}, static_fields={"svc": "demo"})
    assert plan.static_fields == {"service.name": "demo"}
    return


def test_plan_two_rename_sources_same_target_rejected() -> None:
    # Two sources mapping to the same final name must be rejected.
    with pytest.raises(ValueError, match="LEVEL"):
        FieldOutputPlan(rename_fields={"levelname": "LEVEL", "severity": "LEVEL"})
    return


def test_plan_rename_target_collides_with_static_name_rejected() -> None:
    with pytest.raises(ValueError, match="service"):
        FieldOutputPlan(rename_fields={"name": "service"}, static_fields={"service": "demo"})
    return


def test_plan_two_static_fields_same_final_name_rejected() -> None:
    # One static key is renamed onto another static key's final name.
    with pytest.raises(ValueError, match="env"):
        FieldOutputPlan(
            rename_fields={"environment": "env"}, static_fields={"environment": "p", "env": "q"}
        )
    return


def test_plan_static_key_equal_to_rename_source_is_allowed() -> None:
    # The same field passing through rename_fields is not a second source.
    plan = FieldOutputPlan(rename_fields={"svc": "service.name"}, static_fields={"svc": "demo"})
    assert plan.get_rename("svc") == "service.name"
    assert plan.static_fields == {"service.name": "demo"}
    return


### FORMATTER INTEGRATION
### ============================================================================
@pytest.mark.parametrize(
    ("rename_fields", "static_fields"),
    [
        # two rename sources -> same target
        ({"a": "x", "b": "x"}, None),
        # renamed field -> same final name as a static field
        ({"a": "x"}, {"x": 1}),
        # two static fields -> same final name
        ({"a": "x"}, {"a": 1, "x": 2}),
    ],
)
def test_formatter_rejects_conflict_without_calling_serializer(
    rename_fields: dict[str, str] | None, static_fields: dict[str, Any] | None
) -> None:
    calls = 0

    def counting_serializer(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return "{}"

    with pytest.raises(ValueError):
        JsonFormatter(
            json_serializer=counting_serializer,
            rename_fields=rename_fields,
            static_fields=static_fields,
        )
    assert calls == 0
    return


def test_valid_mapping_output_is_deterministic(logger: logging.Logger, stream: io.StringIO) -> None:
    formatter = JsonFormatter(
        rename_fields={"message": "@message", "svc": "service.name"},
        static_fields={"svc": "demo", "env": "prod"},
    )
    logger.handlers[0].setFormatter(formatter)

    logger.info("hello")
    log_json = load_json(stream)

    # Exact ordered content: required fields (planned output names) first, then
    # the planned static fields.
    assert list(log_json.items()) == [
        ("@message", "hello"),
        ("service.name", "demo"),
        ("env", "prod"),
    ]

    # Formatting a second record gives the same deterministic result.
    stream.seek(0)
    stream.truncate()
    logger.info("hello")
    assert load_json(stream) == log_json
    return


def test_input_mappings_are_not_rewritten(logger: logging.Logger, stream: io.StringIO) -> None:
    rename_fields = {"message": "@message"}
    static_fields = {"svc": "demo"}
    rename_fields_before = dict(rename_fields)
    static_fields_before = dict(static_fields)

    formatter = JsonFormatter(rename_fields=rename_fields, static_fields=static_fields)
    logger.handlers[0].setFormatter(formatter)
    logger.info("hello")

    # The caller's dicts must not be modified by planning or formatting.
    assert rename_fields == rename_fields_before
    assert static_fields == static_fields_before
    return


def test_record_source_fields_are_not_rewritten(
    logger: logging.Logger, stream: io.StringIO
) -> None:
    formatter = JsonFormatter(
        "%(levelname)s%(message)s",
        rename_fields={"levelname": "LEVEL", "custom": "custom.out"},
    )
    logger.handlers[0].setFormatter(formatter)

    captured: list[logging.LogRecord] = []

    class CaptureFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            captured.append(record)
            return True

    logger.handlers[0].addFilter(CaptureFilter())
    logger.info("hello", extra={"custom": 1234})

    log_json = load_json(stream)
    assert log_json["LEVEL"] == "INFO"
    assert log_json["custom.out"] == 1234

    # The original record attributes keep their source names and values.
    record = captured[0]
    assert record.levelname == "INFO"
    assert record.__dict__["custom"] == 1234
    assert "LEVEL" not in record.__dict__
    assert "custom.out" not in record.__dict__
    return
