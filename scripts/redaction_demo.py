"""In-memory acceptance demo for nested field redaction.

Run with the package on the path, e.g. (from the repository root):

    PYTHONPATH=src python scripts/redaction_demo.py

The demo uses only an in-memory ``io.StringIO`` stream (no files or external
services) and demonstrates:

1. redacting a named field inside nested list elements,
2. leaving same-named non-target fields untouched,
3. raising the library error on a circular value,
4. never modifying the source ``LogRecord`` data.
"""

### IMPORTS
### ============================================================================
## Standard Library
import io
import json
import logging

## Application
from pythonjsonlogger.exception import JsonFormatError
from pythonjsonlogger.json import JsonFormatter


### MAIN
### ============================================================================
def main() -> None:
    """Run the in-memory redaction acceptance checks."""
    # 1 & 2. nested-list target field + same-named non-target fields --------
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter(redact_fields={"accounts.password": "***"}))

    logger = logging.getLogger("redaction.demo")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    accounts = [
        {"user": "alice", "password": "alice-secret"},
        {"user": "bob", "password": "bob-secret"},
    ]
    logger.info("login", extra={"accounts": accounts, "password": "top-level-secret"})

    output = json.loads(buffer.getvalue())
    print("JSON output:")
    print(json.dumps(output, indent=2, ensure_ascii=False))

    assert output["accounts"][0]["password"] == "***"
    assert output["accounts"][1]["password"] == "***"
    assert output["password"] == "top-level-secret"
    # 4. original LogRecord data is untouched
    assert accounts[0]["password"] == "alice-secret"
    assert accounts[1]["password"] == "bob-secret"
    print("original record passwords:", [a["password"] for a in accounts])

    # 3. circular value raises the library error -----------------------------
    cyclic: dict = {}
    cyclic["loop"] = cyclic
    record = logging.LogRecord(
        name="redaction.demo",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg="login",
        args=None,
        exc_info=None,
    )
    record.accounts = [{"password": "secret"}]
    record.bad = cyclic

    try:
        JsonFormatter(redact_fields=["accounts.password"]).format(record)
    except JsonFormatError as error:
        print("circular value error:", error)
    else:
        raise AssertionError("expected JsonFormatError for circular value")

    # the record data is still intact after the failed format
    assert record.accounts[0]["password"] == "secret"
    print("OK")
    return


if __name__ == "__main__":
    main()
