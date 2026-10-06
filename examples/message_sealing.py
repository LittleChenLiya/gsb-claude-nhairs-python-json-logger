"""Three-record example of one-shot message sealing in the JSON formatter.

Run with: `python examples/message_sealing.py`

Each of the three records is formatted once and printed as a JSON line:

1. A counting object used as `msg` with positional args is stringified once.
2. A plain string message with mapping args is interpolated normally.
3. A `message` attribute smuggled in via `extra` cannot replace the sealed
   message.
"""

from __future__ import annotations

import json
import logging

from pythonjsonlogger.json import JsonFormatter


class StrCounter:
    """Object that counts how many times `str()` is called on it."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.count = 0

    def __str__(self) -> str:
        self.count += 1
        return self.text


def make_record(msg, args=None, **attributes) -> logging.LogRecord:
    record = logging.LogRecord("example", logging.INFO, __file__, 1, msg, args, None)
    for key, value in attributes.items():
        record.__dict__[key] = value
    return record


def main() -> None:
    formatter = JsonFormatter("%(message)s")

    # Record 1: counting object, stringified exactly once during sealing
    counter = StrCounter("counted %s")
    record1 = make_record(counter, ("value",))
    output1 = json.loads(formatter.format(record1))
    print(json.dumps(output1))
    assert output1["message"] == "counted value"
    assert counter.count == 1, counter.count

    # Record 2: plain string message with mapping args
    record2 = make_record("value=%(value)s", ({"value": 3},))
    output2 = json.loads(formatter.format(record2))
    print(json.dumps(output2))
    assert output2["message"] == "value=3"

    # Record 3: an extra "message" attribute cannot replace the sealed message
    record3 = make_record("real %s", ("message",), message="extra message")
    output3 = json.loads(formatter.format(record3))
    print(json.dumps(output3))
    assert output3["message"] == "real message"


if __name__ == "__main__":
    main()
