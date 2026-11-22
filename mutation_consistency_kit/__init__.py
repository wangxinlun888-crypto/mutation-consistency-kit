"""A local SQLite mutation store. Each Store method uses its own connection."""

from .store import Store
from .types import (
    InconsistentStoreError,
    KeyReuseError,
    Receipt,
    Snapshot,
    StaleRevisionError,
    Status,
    TargetExistsError,
)

__all__ = [
    "Store",
    "Receipt",
    "Snapshot",
    "Status",
    "KeyReuseError",
    "StaleRevisionError",
    "InconsistentStoreError",
    "TargetExistsError",
]
