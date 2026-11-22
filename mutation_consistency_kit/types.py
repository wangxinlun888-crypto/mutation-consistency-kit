"""Public records and exceptions for the local mutation API."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Status(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Snapshot:
    target: str
    revision: int
    value: dict[str, Any]


@dataclass(frozen=True)
class Receipt:
    key: str
    target: str
    expected_revision: int | None
    status: Status
    previous_revision: int | None
    result_revision: int | None
    reason: str


class KeyReuseError(ValueError):
    """An existing request key is bound to a different request."""


class StaleRevisionError(ValueError):
    """The requested data revision is missing or obsolete."""


class InconsistentStoreError(RuntimeError):
    """Stored evidence cannot support a verified read."""


class TargetExistsError(ValueError):
    """The specified target already has a history."""
