"""File-backed document replacement. All correctness state lives in one SQLite file."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator, TypeVar

from .types import (
    InconsistentStoreError,
    KeyReuseError,
    Receipt,
    Snapshot,
    StaleRevisionError,
    Status,
    TargetExistsError,
)

_APPLICATION_ID = 0x4D434B31
_OUTPUT = TypeVar("_OUTPUT")

_SCHEMA = (
    """CREATE TABLE entries (
        target TEXT PRIMARY KEY NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        body TEXT NOT NULL
    )""",
    """CREATE TABLE operations (
        op_key TEXT PRIMARY KEY NOT NULL,
        target TEXT NOT NULL,
        expected INTEGER NOT NULL CHECK (expected >= 1),
        proposal TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('UNKNOWN','FAILED','SUCCEEDED')),
        reason TEXT NOT NULL,
        previous_rev INTEGER,
        result_rev INTEGER
    )""",
    """CREATE INDEX operations_by_target ON operations(target, state)""",
    """CREATE TABLE versions (
        target TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        body TEXT NOT NULL,
        op_key TEXT UNIQUE REFERENCES operations(op_key),
        PRIMARY KEY (target, revision),
        CHECK ((revision = 1 AND op_key IS NULL)
            OR (revision > 1 AND op_key IS NOT NULL))
    )""",
)


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("keys and targets must be nonempty strings")
    value.encode("utf-8")


def _revision(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError("revision must be a positive SQLite integer")


def _validate_members(value: Any) -> None:
    # json.dumps accepts tuples and coerces numeric object keys. Neither is an
    # input JSON type here: both could collide with another caller's request.
    if isinstance(value, dict):
        for key, member in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            key.encode("utf-8")
            _validate_members(member)
    elif isinstance(value, list):
        for member in value:
            _validate_members(member)
    elif isinstance(value, str):
        value.encode("utf-8")
    elif value is not None and type(value) not in (int, float, bool):
        raise ValueError("value contains a non-JSON type")


def _encode(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        raise ValueError("value must be a JSON object")
    # Serialize first to reject cycles and non-finite numbers.
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    _validate_members(value)
    return body


def _decode(body: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
        if _encode(value) == body:
            return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise InconsistentStoreError("invalid stored document") from exc
    raise InconsistentStoreError("noncanonical stored document")


def _unknown(key: str, target: str, expected: int | None, reason: str) -> Receipt:
    return Receipt(key, target, expected, Status.UNKNOWN, None, None, reason)


class Store:
    """One local database; share the path across threads or processes."""

    def __init__(self, path: str | Path):
        if str(path) in ("", ":memory:"):
            raise ValueError("a filesystem database is required")
        self.path = Path(path).resolve()
        self._uri = self.path.as_uri()
        with closing(self._connect("rwc")) as conn:
            # A single consistent snapshot for header/objects. Upgrade only a
            # truly empty DB; concurrent initializers recheck under writer lock.
            with self._transaction(conn):
                initialized = self._initialized(conn)
            if not initialized:
                with self._transaction(conn, write=True):
                    if not self._initialized(conn):
                        for statement in _SCHEMA:
                            conn.execute(statement)
                        conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                        conn.execute("PRAGMA user_version=1")

    @staticmethod
    def _initialized(conn: sqlite3.Connection) -> bool:
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if (app_id, version) == (_APPLICATION_ID, 1):
            return True
        if (app_id, version) == (0, 0) and not conn.execute(
            "SELECT 1 FROM sqlite_master LIMIT 1"
        ).fetchone():
            return False
        raise InconsistentStoreError("unrecognized database or schema version")

    def _connect(self, mode: str) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"{self._uri}?mode={mode}", uri=True, timeout=5.0, isolation_level=None
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            if mode != "ro":
                conn.execute("PRAGMA synchronous=FULL")
            return conn
        except sqlite3.Error:
            conn.close()
            raise

    @staticmethod
    @contextmanager
    def _transaction(conn: sqlite3.Connection, write: bool = False) -> Iterator[None]:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            conn.commit()
        finally:
            if conn.in_transaction:
                conn.rollback()

    @staticmethod
    def _snapshot(conn: sqlite3.Connection, target: str) -> Snapshot | None:
        """Cross-check head, revision sequence, and both directions of receipt links."""
        orphan = conn.execute(
            """SELECT 1 FROM operations o WHERE o.target=? AND o.state='SUCCEEDED'
               AND NOT EXISTS (SELECT 1 FROM versions v
                               WHERE v.op_key=o.op_key AND v.target=o.target) LIMIT 1""",
            (target,),
        ).fetchone()
        if orphan:
            raise InconsistentStoreError("successful receipt has no matching version")
        rows = conn.execute(
            """SELECT v.revision, v.body, v.op_key, o.target, o.expected, o.proposal,
                      o.state, o.reason, o.previous_rev, o.result_rev
               FROM versions v LEFT JOIN operations o ON o.op_key=v.op_key
               WHERE v.target=? ORDER BY v.revision""",
            (target,),
        )
        latest = None
        for revision, row in enumerate(rows, start=1):
            if revision != row["revision"]:
                raise InconsistentStoreError("revision gap")
            value = _decode(row["body"])
            if revision == 1:
                if row["op_key"] is not None:
                    raise InconsistentStoreError("seed version has a mutation key")
            elif (
                row["state"] != "SUCCEEDED" or row["reason"] != "applied"
                or row["target"] != target or row["expected"] != revision - 1
                or row["previous_rev"] != revision - 1 or row["result_rev"] != revision
                or row["proposal"] != row["body"]
            ):
                raise InconsistentStoreError("version and receipt disagree")
            latest = row
        head = conn.execute("SELECT revision,body FROM entries WHERE target=?", (target,)).fetchone()
        if latest is None and head is None:
            return None
        if head is None or latest is None or (
            head["revision"] != latest["revision"] or head["body"] != latest["body"]
        ):
            raise InconsistentStoreError("current document and history disagree")
        return Snapshot(target, head["revision"], value)

    def create(self, target: str, value: dict[str, Any]) -> Snapshot:
        """Seed a new target at revision 1. An existing target is never reset."""
        _identifier(target)
        body = _encode(value)
        with closing(self._connect("rw")) as conn, self._transaction(conn, write=True):
            if self._snapshot(conn, target) is not None:
                raise TargetExistsError(target)
            conn.execute("INSERT INTO entries VALUES (?,1,?)", (target, body))
            conn.execute("INSERT INTO versions VALUES (?,1,?,NULL)", (target, body))
        return Snapshot(target, 1, json.loads(body))

    def read(self, target: str) -> Snapshot:
        """Read and verify a target in one consistent SQLite snapshot."""
        _identifier(target)
        with closing(self._connect("ro")) as conn, self._transaction(conn):
            result = self._snapshot(conn, target)
            if result is None:
                raise KeyError(target)
            return result

    @staticmethod
    def _lookup(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM operations WHERE op_key=?", (key,)).fetchone()

    def _receipt(self, conn: sqlite3.Connection, key: str, row: sqlite3.Row | None) -> Receipt | None:
        linked = conn.execute(
            "SELECT target,revision FROM versions WHERE op_key=?", (key,)
        ).fetchone()
        if row is None:
            if linked is None:
                return None
            return _unknown(key, linked["target"], None, "inconsistent_evidence")
        target, expected = row["target"], row["expected"]
        try:
            snapshot = self._snapshot(conn, target)
            _decode(row["proposal"])
        except InconsistentStoreError:
            return _unknown(key, target, expected, "inconsistent_evidence")
        state, reason = row["state"], row["reason"]
        previous, result = row["previous_rev"], row["result_rev"]
        if state == "SUCCEEDED" and linked and linked["target"] == target:
            # _snapshot checked the linked version against the complete receipt.
            return Receipt(key, target, expected, Status.SUCCEEDED, previous, result, reason)
        if linked is None and result is None:
            if state == "FAILED" and (
                (reason == "target_missing" and previous is None)
                or (reason == "revision_conflict" and type(previous) is int
                    and previous != expected and snapshot and 1 <= previous <= snapshot.revision)
            ):
                return Receipt(key, target, expected, Status.FAILED, previous, None, reason)
            if state == "UNKNOWN" and reason == "reserved" and previous is None:
                return _unknown(key, target, expected, "reserved")
        return _unknown(key, target, expected, "inconsistent_evidence")

    def recover(self, key: str) -> Receipt | None:
        """Inspect durable evidence without applying, retrying, or repairing anything."""
        _identifier(key)
        with closing(self._connect("ro")) as conn, self._transaction(conn):
            return self._receipt(conn, key, self._lookup(conn, key))

    def replace(self, key: str, target: str, expected_revision: int, value: dict[str, Any]) -> Receipt:
        """Reserve an operation key, then conditionally replace the whole document."""
        _identifier(key)
        _identifier(target)
        _revision(expected_revision)
        body = _encode(value)
        try:
            with closing(self._connect("rw")) as conn:
                with self._transaction(conn, write=True):
                    row = self._lookup(conn, key)
                    prior = self._receipt(conn, key, row)
                    if prior is not None and prior.reason == "inconsistent_evidence":
                        return prior
                    if row is not None and (
                        row["target"], row["expected"], row["proposal"]
                    ) != (target, expected_revision, body):
                        raise KeyReuseError(key)
                    if prior is not None:
                        return prior
                    conn.execute(
                        """INSERT INTO operations
                           (op_key,target,expected,proposal,state,reason)
                           VALUES (?,?,?,?,'UNKNOWN','reserved')""",
                        (key, target, expected_revision, body),
                    )
                # Only the caller that committed the *new* reservation reaches
                # this block. Reopening a store never resumes a reservation.
                with self._transaction(conn, write=True):
                    self._apply(conn, key, target, expected_revision, body)
                self._after_commit()
        except InconsistentStoreError:
            return _unknown(key, target, expected_revision, "inconsistent_evidence")
        except sqlite3.Error:
            # For example, a lock or an I/O error around COMMIT. An exception
            # cannot attest whether the transaction reached durable storage.
            return _unknown(key, target, expected_revision, "storage_unconfirmed")
        try:
            receipt = self.recover(key)
        except sqlite3.Error:
            return _unknown(key, target, expected_revision, "storage_unconfirmed")
        if receipt is None:
            return _unknown(key, target, expected_revision, "inconsistent_evidence")
        return receipt

    def _apply(self, conn: sqlite3.Connection, key: str, target: str, expected: int, body: str) -> None:
        reservation = self._lookup(conn, key)
        if reservation is None or (
            reservation["target"], reservation["expected"], reservation["proposal"],
            reservation["state"], reservation["reason"],
            reservation["previous_rev"], reservation["result_rev"],
        ) != (target, expected, body, "UNKNOWN", "reserved", None, None):
            raise InconsistentStoreError("reservation changed before application")
        current = self._snapshot(conn, target)
        if current is None or current.revision != expected:
            previous = current.revision if current else None
            reason = "revision_conflict" if current else "target_missing"
            finalized = conn.execute(
                "UPDATE operations SET state='FAILED',reason=?,previous_rev=? WHERE op_key=?",
                (reason, previous, key),
            ).rowcount
            if finalized != 1:
                raise InconsistentStoreError("failure receipt could not be recorded")
            return
        changed = conn.execute(
            "UPDATE entries SET body=?,revision=revision+1 WHERE target=? AND revision=?",
            (body, target, expected),
        ).rowcount
        if changed != 1:
            raise InconsistentStoreError("compare-and-set did not affect exactly one row")
        conn.execute("INSERT INTO versions VALUES (?,?,?,?)", (target, expected + 1, body, key))
        finalized = conn.execute(
            """UPDATE operations SET state='SUCCEEDED',reason='applied',
               previous_rev=?,result_rev=? WHERE op_key=?""",
            (expected, expected + 1, key),
        ).rowcount
        if finalized != 1:
            raise InconsistentStoreError("success receipt could not be recorded")

    def _after_commit(self) -> None:
        """Internal boundary between business COMMIT and returning its receipt."""

    def deliver(self, target: str, revision: int, output: _OUTPUT) -> _OUTPUT:
        """Release a caller-supplied local output only for a verified current revision."""
        _revision(revision)
        try:
            current = self.read(target)
        except KeyError as exc:
            raise StaleRevisionError(target) from exc
        if current.revision != revision:
            raise StaleRevisionError(target)
        return output
