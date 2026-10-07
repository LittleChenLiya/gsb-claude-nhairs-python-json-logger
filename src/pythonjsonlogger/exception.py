### IMPORTS
### ============================================================================
## Future
from __future__ import annotations

## Standard Library

## Installed

## Application


### CLASSES
### ============================================================================
class PythonJsonLoggerError(Exception):
    "Generic base clas for all Python JSON Logger exceptions"


class MissingPackageError(ImportError, PythonJsonLoggerError):
    "A required package is missing"

    def __init__(self, name: str, extras_name: str | None = None) -> None:
        msg = f"The {name!r} package is required but could not be found."
        if extras_name is not None:
            msg += f" It can be installed using 'python-json-logger[{extras_name}]'."
        super().__init__(msg)
        return


class RedactionError(PythonJsonLoggerError):
    """Raised when redacting fields fails.

    For example when a circular reference is encountered whilst traversing
    the containers holding the explicitly configured field paths.

    *New in 4.2.1*
    """
