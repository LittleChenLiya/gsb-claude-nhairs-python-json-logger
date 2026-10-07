"""Redaction of explicitly specified nested field paths.

Fields are addressed by an explicit path of dictionary keys. Lists are
transparent: a path passes through every element of a list. Paths never match
fields of the same name that occur at a different level in the data, and paths
that do not exist never cause fields to be added.

Redaction copies a container only when it (or one of its descendants) is
modified, so callers' original objects (including ``LogRecord`` data) are never
mutated. Circular references encountered while traversing ``dict`` and ``list``
containers raise :class:`~pythonjsonlogger.exception.JsonFormatError`.
"""

### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library
from collections.abc import Mapping, Sequence as SequenceABC
from dataclasses import dataclass, field
from typing import Any, TypeAlias

## Application
from .exception import JsonFormatError

### CONSTANTS
### ============================================================================
DEFAULT_REDACTION_VALUE: str = "***REDACTED***"
"""Value substituted for redacted fields when no replacement is configured."""

RedactFieldPath: TypeAlias = "str | tuple[Any, ...] | list[Any]"
"""A single explicit field path (dotted string or sequence of dict keys)."""

_MISSING = object()
_PENDING = object()


### CLASSES
### ============================================================================
@dataclass
class _RedactionNode:
    """A node in the redaction path trie.

    Attributes:
        terminal: replacement value when this node is the end of an explicit
            path. A sentinel is used because any value (including ``None``) is
            a valid replacement.
        children: the next path segment(s) keyed by dictionary key.
    """

    terminal: Any = _MISSING
    children: dict[Any, _RedactionNode] = field(default_factory=dict)


### FUNCTIONS
### ============================================================================
def _normalise_path(path: str | tuple[Any, ...] | list[Any]) -> tuple[Any, ...]:
    """Convert a configured field path into a tuple of path segments.

    String paths are split on dots (e.g. ``"a.b"`` becomes ``("a", "b")``).
    Sequences are used verbatim.
    """
    if isinstance(path, str):
        segments = tuple(part for part in path.split(".") if part != "")
        if not segments:
            raise ValueError(f"Redaction field path {path!r} does not contain any field names")
        return segments
    if isinstance(path, (tuple, list)):
        segments = tuple(path)
        if not segments:
            raise ValueError("Redaction field path does not contain any field names")
        return segments
    raise TypeError(
        "Redaction field path must be a dotted string or a sequence of keys, " f"got {type(path)!r}"
    )


def build_redaction_tree(
    redact_fields: Mapping[RedactFieldPath, Any] | SequenceABC[RedactFieldPath],
) -> _RedactionNode:
    """Build the path trie used for redaction.

    Args:
        redact_fields: either a mapping of explicit field paths to replacement
            values, or a sequence of field paths (each using
            `DEFAULT_REDACTION_VALUE`).

    Returns:
        Root node of the path trie.
    """
    root = _RedactionNode()

    if isinstance(redact_fields, Mapping):
        entries = list(redact_fields.items())
    else:
        # A bare string is a single dotted path, not a sequence of characters.
        if isinstance(redact_fields, str):
            entries = [(redact_fields, DEFAULT_REDACTION_VALUE)]
        else:
            entries = [(raw_path, DEFAULT_REDACTION_VALUE) for raw_path in redact_fields]

    for raw_path, replacement in entries:
        node = root
        for segment in _normalise_path(raw_path):
            node = node.children.setdefault(segment, _RedactionNode())
        node.terminal = replacement
    return root


def redact_log_data(log_data: Any, root: _RedactionNode) -> Any:
    """Return a copy of ``log_data`` with configured fields redacted.

    Containers are copied on write: only ``dict`` and ``list`` containers that
    actually contain a redacted value are duplicated. Missing paths never add
    fields and same-named fields outside an explicit path are left untouched.

    Args:
        log_data: the log data to redact.
        root: root of the path trie from :func:`build_redaction_tree`.

    Raises:
        JsonFormatError: if a circular reference is encountered while
            traversing ``dict`` or ``list`` containers.

    Returns:
        The redacted data (which is the original object when nothing was
        modified).
    """
    # A task is either a value to visit or a marker that finalises a container
    # once every child has been visited. The marker is pushed before its
    # children; children are pushed in reverse order so they are visited (and
    # emitted onto `output`) in their original order, with the marker resolved
    # last.
    visit_task: tuple[Any, dict[Any, _RedactionNode], tuple[Any, ...], frozenset[int]] = (
        log_data,
        root.children,
        (),
        frozenset(),
    )
    stack: list[Any] = [("visit", visit_task)]
    output: list[Any] = []

    while stack:
        kind, payload = stack.pop()

        if kind == "finish":
            container, pending = payload
            output.append(_resolve_container(container, pending, output))
            continue

        value, rules, field_path, ancestors = payload
        if not isinstance(value, (dict, list)):
            output.append(value)
            continue

        if id(value) in ancestors:
            raise JsonFormatError(
                "Circular reference detected while redacting log data", path=field_path
            )
        pending, children = _expand_container(value, rules, field_path, ancestors | {id(value)})

        stack.append(("finish", (value, pending)))
        stack.extend(reversed(children))

    return output[-1]


def _expand_container(
    value: dict[Any, Any] | list[Any],
    rules: dict[Any, _RedactionNode],
    field_path: tuple[Any, ...],
    child_ancestors: frozenset[int],
) -> tuple[list[tuple[Any, Any]], list[Any]]:
    """Build the pending item list and child visit tasks for one container."""
    items = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
    pending: list[tuple[Any, Any]] = []
    children: list[Any] = []
    for key, item in items:
        if isinstance(value, dict):
            rule = rules.get(key)
            if rule is not None and rule.terminal is not _MISSING:
                # The explicit path ends here: do not descend into the value.
                pending.append((key, rule.terminal))
                continue
            # Lists are transparent: carry the remaining path rules into each
            # element unchanged.
            child_rules = rule.children if rule is not None else {}
        else:
            child_rules = rules
        children.append(("visit", (item, child_rules, field_path + (key,), child_ancestors)))
        pending.append((key, _PENDING))
    return pending, children


def _resolve_container(
    container: dict[Any, Any] | list[Any],
    pending: list[tuple[Any, Any]],
    output: list[Any],
) -> Any:
    """Rebuild a container from resolved children, copying only when changed."""
    copy_: dict[Any, Any] | list[Any] | None = None
    for key, placeholder in reversed(pending):
        new_value = output.pop() if placeholder is _PENDING else placeholder
        if copy_ is None and new_value is not container[key]:
            copy_ = dict(container) if isinstance(container, dict) else list(container)
        if copy_ is not None:
            copy_[key] = new_value  # type: ignore[index]
    return copy_ if copy_ is not None else container
