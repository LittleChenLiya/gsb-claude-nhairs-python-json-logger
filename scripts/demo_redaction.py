"""Small acceptance fixture for nested value redaction.

Run from the repository root with:
    PYTHONPATH=src python scripts/demo_redaction.py

Uses an in-memory StringIO stream and a temp directory only; no network or
heavy dependencies. Prints the redacted JSON, the non-redaction regression
output, the circular reference error, and proves the source record data is
unchanged.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import tempfile
from pathlib import Path

from pythonjsonlogger.exception import PythonJsonLoggerError, RedactionError
from pythonjsonlogger.json import JsonFormatter


def make_logger(formatter: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(formatter)
    logger = logging.getLogger("demo.redaction")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, buffer


def main() -> None:
    # 1. Nested list target fields + same-named non-target fields
    payload = {
        "users": [
            {"name": "Ada", "secret": "111-22-3333"},
            {"name": "Grace", "secret": "444-55-6666"},
        ],
        "secret": "top-level-non-target",
        "audit": {"secret": "nested-non-target"},
    }
    snapshot = copy.deepcopy(payload)

    logger, buffer = make_logger(
        JsonFormatter(
            redact_fields=[
                ("data", "users", 0, "secret"),
                ("data", "users", 1, "secret"),
            ]
        )
    )
    logger.info("login event", extra={"data": payload})
    print("1) redacted JSON:")
    print("  ", buffer.getvalue().strip())

    # 2. Original interface regression: no redaction -> normal encoder
    logger2, buffer2 = make_logger(JsonFormatter())
    logger2.info("plain %s", "message", extra={"n": 3})
    print("2) normal encoder still used (no redact_fields):")
    print("  ", buffer2.getvalue().strip())

    # 3. Missing paths add nothing; verify original record untouched
    missing_payload: dict = {"users": [{"name": "Ada"}], "present": {"secret": "shh"}}
    logger3m, buffer3m = make_logger(
        JsonFormatter(
            redact_fields=[
                ("data", "users", 0, "secret"),  # key missing
                ("data", "users", 1),  # index missing
                ("data", "missing", "deep"),  # container missing
                ("data", "present", "secret"),  # present
            ]
        )
    )
    logger3m.info("x", extra={"data": missing_payload})
    missing_result = json.loads(buffer3m.getvalue())
    assert "secret" not in missing_result["data"]["users"][0]
    assert len(missing_result["data"]["users"]) == 1
    assert "missing" not in missing_result["data"]
    assert missing_result["data"]["present"]["secret"] == "[REDACTED]"
    print("3) missing paths add no fields; present path redacted: OK")

    assert payload == snapshot, "source record payload was modified!"
    assert payload["users"][0]["secret"] == "111-22-3333"
    print("   source record payload unchanged after formatting: OK")

    # 4. Circular value -> this library's formatting error
    cyclic: dict = {"name": "loop"}
    cyclic["self"] = cyclic
    record = logging.LogRecord(
        name="demo",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="x",
        args=(),
        exc_info=None,
    )
    # attach extras the way logging does for logger.info(extra=...)
    record.data = cyclic
    formatter = JsonFormatter(redact_fields=[("data", "self", "name")])
    try:
        formatter.format(record)
        raise AssertionError("expected RedactionError to be raised")
    except PythonJsonLoggerError as error:
        assert isinstance(error, RedactionError)
        print(f"4) circular value raises {type(error).__name__}: {error}")
    assert cyclic["name"] == "loop" and cyclic["self"] is cyclic
    print("   record data unchanged after failed format: OK")

    # 5. Temp-file fixture showing the same result on disk
    with tempfile.TemporaryDirectory(prefix="pjl-redact-") as tmp:
        path = Path(tmp) / "event.json"
        with path.open("w", encoding="utf-8") as handle:
            file_handler = logging.StreamHandler(handle)
            file_handler.setFormatter(JsonFormatter(redact_fields=[("data", "users", 0, "secret")]))
            logger3 = logging.getLogger("demo.redaction.file")
            logger3.handlers.clear()
            logger3.addHandler(file_handler)
            logger3.setLevel(logging.INFO)
            logger3.info("file event", extra={"data": copy.deepcopy(payload)})
            file_handler.flush()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        print("5) temp-file fixture JSON:", json.dumps(on_disk, sort_keys=True))


if __name__ == "__main__":
    main()
