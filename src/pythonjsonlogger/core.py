"""Core functionality shared by all JSON loggers"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from datetime import datetime, timezone
import logging
import re
import string
import sys
from typing import TypeAlias, Any
from collections.abc import Container, Sequence

## Installed

## Application
from .exception import RedactionError

### CONSTANTS
### ============================================================================
_REDACTION_TERMINAL: Any = object()
"""Sentinel marking the end of a configured redaction path in the path trie."""

RESERVED_ATTRS: list[str] = [
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
]
"""Default reserved attributes.

These come from the [default attributes of `LogRecord` objects](http://docs.python.org/library/logging.html#logrecord-attributes).

Note:
    Although considered a constant, this list is dependent on the Python version due to
    different `LogRecord` objects having different attributes in different Python versions.

*Changed in 3.0*: `RESERVED_ATTRS` is now `list[str]` instead of `tuple[str, ...]`.
"""

if sys.version_info >= (3, 12):
    # taskName added in python 3.12
    RESERVED_ATTRS.append("taskName")
    RESERVED_ATTRS.sort()


STYLE_STRING_TEMPLATE_REGEX = re.compile(
    r"\$(?:\$|\{(?P<braced>.+?)\}|(?P<named>[_a-z][_a-z0-9]*))", re.IGNORECASE
)  # $ style
STYLE_PERCENT_REGEX = re.compile(r"%(?:%|\((?P<named>.+?)\))", re.IGNORECASE)  # % style

## Type Aliases
## -----------------------------------------------------------------------------
LogData: TypeAlias = dict[str, Any]
"""Type alias

*Changed in 4.0*: renamed from `LogRecord` to `LogData`
"""

RedactionPath: TypeAlias = Sequence[str | int]
"""An explicit path to a field that should be redacted.

Each element is a `dict` key. Elements that are integers (or strings of
digits) additionally address a single `list` item by its position.
"""

RedactionTrie: TypeAlias = dict[Any, Any]
"""Trie mapping explicit redaction paths to their nested containers."""


### FUNCTIONS
### ============================================================================
def merge_record_extra(
    record: logging.LogRecord,
    target: dict[Any, Any],
    reserved: Container[str],
    rename_fields: dict[str, str] | None = None,
) -> dict[Any, Any]:
    """
    Merges extra attributes from LogRecord object into target dictionary

    Args:
        record: logging.LogRecord
        target: dict to update
        reserved: dict or list with reserved keys to skip
        rename_fields: an optional dict, used to rename field names in the output.
            e.g. Rename `levelname` to `log.level`: `{'levelname': 'log.level'}`

    *Changed in 3.1*: `reserved` is now `Container[str]`.
    """
    if rename_fields is None:
        rename_fields = {}
    for key, value in record.__dict__.items():
        # this allows to have numeric keys
        if key not in reserved and not (hasattr(key, "startswith") and key.startswith("_")):
            target[rename_fields.get(key, key)] = value
    return target


def _build_redaction_trie(paths: Any, replacement: Any) -> RedactionTrie:
    """Build a lookup trie from the explicitly configured redaction paths.

    Terminal nodes map `_REDACTION_TERMINAL` to the replacement value.
    """
    trie: RedactionTrie = {}
    for path in paths:
        if isinstance(path, (str, int)) or not isinstance(path, Sequence):
            raise TypeError(f"redact_fields paths must be sequences of str/int, got {path!r}")
        if not path:
            raise ValueError("redact_fields paths must not be empty")
        node = trie
        for segment in path:
            if not isinstance(segment, (str, int)) or isinstance(segment, bool):
                raise TypeError(f"redact_fields path segments must be str or int, got {segment!r}")
            # all-digit strings address a list item just like an integer
            if isinstance(segment, str) and segment.isdigit():
                segment = int(segment)
            node = node.setdefault(segment, {})
        node[_REDACTION_TERMINAL] = replacement
    return trie


def _format_redaction_path(path: Sequence[Any]) -> str:
    """Render a traversed container path for use in error messages."""
    return ".".join(str(segment) for segment in path)


def _dict_child_trie(trie: RedactionTrie, key: Any) -> RedactionTrie | None:
    """Return the trie for a dict key.

    An all-digit string key also matches the equivalent integer segment (the
    same segment can target a list item elsewhere in the structure).
    """
    if key in trie:
        return trie[key]
    if isinstance(key, str) and key.isdigit() and int(key) in trie:
        return trie[int(key)]
    return None


def _redact_container(
    value: Any,
    trie: RedactionTrie,
    path: list[Any],
    seen: set[int],
) -> Any:
    """Recursively replace values at explicit paths.

    Only `dict` and `list` containers are traversed. Containers that are
    modified are copied; unchanged containers are shared with the input so
    the source `LogRecord` data is never mutated.
    """
    if not isinstance(value, (dict, list)):
        # a configured path ends here (possibly alongside deeper paths)
        if _REDACTION_TERMINAL in trie:
            return trie[_REDACTION_TERMINAL]
        return value

    value_id = id(value)
    if value_id in seen:
        raise RedactionError(
            f"Circular reference detected while redacting field path"
            f" '{_format_redaction_path(path)}'"
        )
    seen.add(value_id)

    if isinstance(value, dict):
        # (path segment, configured child trie or None, key for writing back)
        entries = [(key, _dict_child_trie(trie, key), key) for key in value]
    else:
        entries = [(index, trie.get(index), index) for index in range(len(value))]

    result: dict[Any, Any] | list[Any] | None = None
    for segment, child_trie, write_key in entries:
        item = value[write_key]
        if child_trie is None:
            # Cycle detection applies to the whole traversed structure so
            # cycles outside the configured paths are reported by this library
            # rather than leaking as encoder errors.
            new_item = _redact_container(item, {}, [*path, segment], seen)
        else:
            path.append(segment)
            if _REDACTION_TERMINAL in child_trie and len(child_trie) == 1:
                new_item = child_trie[_REDACTION_TERMINAL]
            else:
                # the terminal may coexist with deeper paths (a configured
                # path prefix); the sentinel key is never a real container key
                new_item = _redact_container(item, child_trie, path, seen)
            path.pop()

        if result is None and new_item is not item:
            result = dict(value) if isinstance(value, dict) else list(value)
        if result is not None:
            result[write_key] = new_item

    seen.remove(value_id)
    return value if result is None else result


### CLASSES
### ============================================================================
class BaseJsonFormatter(logging.Formatter):
    """Base class for all formatters

    Must not be used directly.

    *New in 3.1*
    """

    _style: logging.PercentStyle | str  # type: ignore[assignment]

    ## Parent Methods
    ## -------------------------------------------------------------------------
    # pylint: disable=too-many-arguments,super-init-not-called,too-many-locals
    def __init__(
        self,
        fmt: str | Sequence[str] | None = None,
        datefmt: str | None = None,
        style: str = "%",
        validate: bool = True,
        *,
        prefix: str = "",
        rename_fields: dict[str, str] | None = None,
        rename_fields_keep_missing: bool = False,
        static_fields: dict[str, Any] | None = None,
        reserved_attrs: Sequence[str] | None = None,
        timestamp: bool | str = False,
        defaults: dict[str, Any] | None = None,
        exc_info_as_array: bool = False,
        stack_info_as_array: bool = False,
        redact_fields: Sequence[RedactionPath] | None = None,
        redact_value: Any = "[REDACTED]",
    ) -> None:
        """
        Args:
            fmt: String format or `Sequence` of field names of fields to log.
            datefmt: format to use when formatting `asctime` field
            style: how to extract log fields from `fmt`. Ignored if `fmt` is a `Sequence[str]`.
            validate: validate `fmt` against style, if implementing a custom `style` you
                must set this to `False`. Ignored if `fmt` is a `Sequence[str]`.
            defaults: a dictionary containing default fields that are added before all other fields and
                may be overridden. The supplied fields are still subject to `rename_fields`.
            prefix: an optional string prefix added at the beginning of
                the formatted string
            rename_fields: an optional dict, used to rename field names in the output.
                Rename `message` to `@message`: `{'message': '@message'}`
            rename_fields_keep_missing: When renaming fields, include missing fields in the output.
            static_fields: an optional dict, used to add fields with static values to all logs
            reserved_attrs: an optional list of fields that will be skipped when
                outputting json log record. Defaults to [all log record attributes][pythonjsonlogger.core.RESERVED_ATTRS].
            timestamp: an optional string/boolean field to add a timestamp when
                outputting the json log record. If string is passed, timestamp will be added
                to log record using string as key. If True boolean is passed, timestamp key
                will be "timestamp". Defaults to False/off.
            exc_info_as_array: break the exc_info into a list of lines based on line breaks.
            stack_info_as_array: break the stack_info into a list of lines based on line breaks.
            redact_fields: an optional sequence of explicit field paths whose values are
                replaced with `redact_value` before serialization. Each path is a sequence of
                `dict` keys; integer (or all-digit string) segments address a single `list`
                item by position. Paths that do not exist are ignored (no fields are added).
                `dict` and `list` containers holding redacted values are copied, so the
                original `LogRecord` data is never modified. A circular reference encountered
                while traversing raises a
                [RedactionError][pythonjsonlogger.exception.RedactionError].
            redact_value: the value used to replace redacted fields. Defaults to `"[REDACTED]"`.

        *Changed in 3.1*:

        - you can now use custom values for style by setting validate to `False`.
          The value is stored in `self._style` as a string. The `parse` method will need to be
          overridden in order to support the new style.
        - Renaming fields now preserves the order that fields were added in and avoids adding
          missing fields. The original behaviour, missing fields have a value of `None`, is still
          available by setting `rename_fields_keep_missing` to `True`.

        *Changed in 3.2*: `defaults` argument is no longer ignored.

        *Added in 3.3*: `exc_info_as_array` and `stack_info_as_array` options are added.

        *Added in 4.0*:

        - `fmt` now supports comma separated lists (`style=","`). Note that this style is specific
          to `python-json-logger` and thus care should be taken to not to pass this format to other
          logging Formatter implementations.
        - `fmt` now supports sequences of strings (e.g. lists and tuples) of field names.
        """
        ## logging.Formatter compatibility
        ## ---------------------------------------------------------------------
        if fmt is None or isinstance(fmt, str):
            if style in logging._STYLES:
                _style = logging._STYLES[style][0](fmt)  # type: ignore[operator]
                if validate:
                    _style.validate()
                self._style = _style
                self._fmt = _style._fmt

            elif style == "," or not validate:
                self._style = style
                self._fmt = fmt
                # TODO: Validate comma format

            else:
                raise ValueError("Style must be one of: '%{$,'")

            self._required_fields = self.parse()

        # Note: we do this check second as string is still a Sequence[str]
        elif isinstance(fmt, Sequence):
            self._style = "__sequence__"
            self._fmt = str(fmt)
            self._required_fields = list(fmt)

        self.datefmt = datefmt

        ## JSON Logging specific
        ## ---------------------------------------------------------------------
        self.prefix = prefix

        # We recreate the dict in rename_fields and static_fields to support internal/external
        # references which require getting the item to do the conversion.
        # For more details see: https://github.com/nhairs/python-json-logger/pull/45
        self.rename_fields = (
            {key: rename_fields[key] for key in rename_fields} if rename_fields is not None else {}
        )
        self.static_fields = (
            {key: static_fields[key] for key in static_fields} if static_fields is not None else {}
        )

        self.rename_fields_keep_missing = rename_fields_keep_missing
        self.reserved_attrs = set(reserved_attrs if reserved_attrs is not None else RESERVED_ATTRS)
        self.timestamp = timestamp

        self._skip_fields = set(self._required_fields)
        self._skip_fields.update(self.reserved_attrs)
        self.defaults = defaults if defaults is not None else {}
        self.exc_info_as_array = exc_info_as_array
        self.stack_info_as_array = stack_info_as_array

        # Redaction configuration (explicit field paths only): the replacement
        # value is stored at each path's terminal node
        self._redact_trie = (
            _build_redaction_trie(redact_fields, redact_value) if redact_fields is not None else {}
        )
        return

    def format(self, record: logging.LogRecord) -> str:
        """Formats a log record and serializes to json

        When `record.msg` is a `dict`, adding `exc_info` and `stack_info` does not
        modify the caller's dict.

        Args:
            record: the record to format

        *Changed in 4.2.0*: a `dict` `record.msg` is copied instead of modified
        in place.
        """
        message_dict: dict[str, Any] = {}
        # TODO: logging.LogRecord.msg and logging.LogRecord.message in typeshed
        #        are always type of str. We shouldn't need to override that.
        if isinstance(record.msg, dict):
            message_dict = record.msg.copy()
            record.message = ""
        else:
            record.message = record.getMessage()

        # only format time if needed
        if "asctime" in self._required_fields:
            record.asctime = self.formatTime(record, self.datefmt)

        # Display formatted exception, but allow overriding it in the
        # user-supplied dict.
        if record.exc_info and not message_dict.get("exc_info"):
            message_dict["exc_info"] = self.formatException(record.exc_info)
        if not message_dict.get("exc_info") and record.exc_text:
            message_dict["exc_info"] = record.exc_text

        # Display formatted record of stack frames
        # default format is a string returned from :func:`traceback.print_stack`
        if record.stack_info and not message_dict.get("stack_info"):
            message_dict["stack_info"] = self.formatStack(record.stack_info)

        log_data: LogData = {}
        self.add_fields(log_data, record, message_dict)
        log_data = self.process_log_record(log_data)
        log_data = self.redact_log_data(log_data)

        return self.serialize_log_record(log_data)

    ## JSON Formatter Specific Methods
    ## -------------------------------------------------------------------------
    def parse(self) -> list[str]:
        """Parses format string looking for substitutions

        This method is responsible for returning a list of fields (as strings)
        to include in all log messages.

        You can support custom styles by overriding this method.

        Returns:
            list of fields to be extracted and serialized
        """
        if self._fmt is None:
            return []

        if isinstance(self._style, str):
            if self._style == "__sequence__":
                raise RuntimeError("Must not call parse when fmt is a sequence of strings")

            if self._style == ",":
                return [field.strip() for field in self._fmt.split(",") if field.strip()]

            raise ValueError(f"Style {self._style!r} is not supported")

        if isinstance(self._style, logging.StringTemplateStyle):
            # String templates support both ${name} and $name, and $$ is an escaped literal
            return [
                match.group("braced") or match.group("named")
                for match in STYLE_STRING_TEMPLATE_REGEX.finditer(self._fmt)
                if match.group("braced") or match.group("named")
            ]

        if isinstance(self._style, logging.StrFormatStyle):
            return [
                field_name
                for _, field_name, _, _ in string.Formatter().parse(self._fmt)
                if field_name
            ]

        if isinstance(self._style, logging.PercentStyle):
            # PercentStyle is parent class of StringTemplateStyle and StrFormatStyle
            # so it must be checked last.
            return [
                match.group("named")
                for match in STYLE_PERCENT_REGEX.finditer(self._fmt)
                if match.group("named")
            ]

        raise ValueError(f"Style {self._style!r} is not supported")

    def serialize_log_record(self, log_data: LogData) -> str:
        """Returns the final representation of the data to be logged

        Args:
            log_data: the data

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        return self.prefix + self.jsonify_log_record(log_data)

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Extract fields from a LogRecord for logging

        This method can be overridden to implement custom logic for adding fields.

        Args:
            log_data: data that will be logged
            record: the record to extract data from
            message_dict: dictionary that was logged instead of a message. e.g
                `logger.info({"is_this_message_dict": True})`

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        for field in self.defaults:
            log_data[self._get_rename(field)] = self.defaults[field]

        for field in self._required_fields:
            log_data[self._get_rename(field)] = record.__dict__.get(field)

        for data_dict in [self.static_fields, message_dict]:
            for key, value in data_dict.items():
                log_data[self._get_rename(key)] = value

        merge_record_extra(
            record,
            log_data,
            reserved=self._skip_fields,
            rename_fields=self.rename_fields,
        )

        if self.timestamp:
            key = self.timestamp if isinstance(self.timestamp, str) else "timestamp"
            log_data[self._get_rename(key)] = datetime.fromtimestamp(
                record.created, tz=timezone.utc
            )

        if self.rename_fields_keep_missing:
            for field in self.rename_fields.values():
                if field not in log_data:
                    log_data[field] = None
        return

    def _get_rename(self, key: str) -> str:
        return self.rename_fields.get(key, key)

    # Child Methods
    # ..........................................................................
    def jsonify_log_record(self, log_data: LogData) -> str:
        """Convert the log data into a JSON string.

        Child classes MUST override this method.

        Args:
            log_data: the data to serialize

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        raise NotImplementedError()

    def process_log_record(self, log_data: LogData) -> LogData:
        """Custom processing of the data to be logged.

        Child classes can override this method to alter the log record before it
        is serialized.

        Args:
            log_data: incoming data

        *Changed in 4.0*: `log_record` renamed to `log_data`
        """
        return log_data

    def redact_log_data(self, log_data: LogData) -> LogData:
        """Replace values at the configured explicit field paths.

        Only paths given via `redact_fields` are modified; missing paths do
        not cause fields to be added. Traversed `dict` and `list` containers
        are copied when modified, leaving the source `LogRecord` data
        untouched.

        Args:
            log_data: data that will be logged

        Raises:
            RedactionError: if a circular reference is encountered while
                traversing the containers.

        *New in 4.2.1*
        """
        if not self._redact_trie:
            return log_data
        return _redact_container(log_data, self._redact_trie, path=[], seen=set())

    def formatException(self, ei) -> str | list[str]:  # type: ignore[override]
        """Format and return the specified exception information.

        If exc_info_as_array is set to True, This method returns an array of strings.
        """
        exception_info_str = super().formatException(ei)
        return exception_info_str.splitlines() if self.exc_info_as_array else exception_info_str

    def formatStack(self, stack_info) -> str | list[str]:  # type: ignore[override]
        """Format and return the specified stack information.

        If stack_info_as_array is set to True, This method returns an array of strings.
        """
        stack_info_str = super().formatStack(stack_info)
        return stack_info_str.splitlines() if self.stack_info_as_array else stack_info_str
