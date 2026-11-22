"""Integration tests using real SQLite files and fictional inventory entries."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from mutation_consistency_kit import (
    InconsistentStoreError, KeyReuseError, StaleRevisionError, Status, Store,
)


TARGET = "shelf-amber"
ORIGINAL = {"item": "paper clips", "boxes": 4}
UPDATED = {"item": "paper clips", "boxes": 5}
LATER = {"item": "paper clips", "boxes": 6}


def receipt_fields(receipt):
    return (
        receipt.key,
        receipt.target,
        receipt.expected_revision,
        receipt.status,
        receipt.result_revision,
        receipt.reason,
    )


class InventoryCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "inventory.sqlite"
        self.store = Store(self.path)
        self.store.create(TARGET, ORIGINAL)

    def check_entry(self, store, revision, value):
        snapshot = store.read(TARGET)
        self.assertEqual((snapshot.target, snapshot.revision, snapshot.value),
                         (TARGET, revision, value))

    def version_rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT revision, body, op_key FROM versions "
                "WHERE target = ? ORDER BY revision", (TARGET,)
            ).fetchall()

    def evidence(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return (
                connection.execute("SELECT * FROM entries ORDER BY target").fetchall(),
                connection.execute(
                    "SELECT * FROM versions ORDER BY target, revision"
                ).fetchall(),
                connection.execute("SELECT * FROM operations ORDER BY op_key").fetchall(),
            )


class ReceiptTests(InventoryCase):
    def test_create_read_and_missing_receipt(self):
        self.check_entry(self.store, 1, ORIGINAL)
        other = self.store.create("shelf-blue", {"boxes": 8})
        self.assertEqual((other.target, other.revision, other.value),
                         ("shelf-blue", 1, {"boxes": 8}))
        self.assertIsNone(Store(self.path).recover("unknown-order"))

    def test_colliding_unicode_object_keys_are_rejected_before_seeding(self):
        scalar = chr(0x1F4E6)
        surrogate_pair = chr(0xD83D) + chr(0xDCE6)
        collision = {scalar: 1, surrogate_pair: 2}
        self.assertEqual(len(collision), 2)
        before = self.evidence()
        with self.assertRaises(ValueError):
            self.store.create("shelf-blue", collision)
        self.assertEqual(self.evidence(), before)

    def test_success_replay_identifies_the_original_revision_after_a_later_change(self):
        receipt = self.store.replace("count-a", TARGET, 1, UPDATED)
        self.assertEqual(receipt_fields(receipt)[:-1],
                         ("count-a", TARGET, 1, Status.SUCCEEDED, 2))
        self.assertIsInstance(receipt.reason, str)
        self.assertEqual(self.store.replace("count-b", TARGET, 2, LATER).status,
                         Status.SUCCEEDED)

        reopened = Store(self.path)
        replay = reopened.replace("count-a", TARGET, 1, dict(reversed(UPDATED.items())))
        self.assertEqual(receipt_fields(replay), receipt_fields(receipt))
        self.assertEqual(receipt_fields(reopened.recover("count-a")), receipt_fields(receipt))
        self.check_entry(reopened, 3, LATER)
        self.assertEqual([row[0] for row in self.version_rows()], [1, 2, 3])

    def test_failed_precondition_stays_failed_when_the_revision_later_matches(self):
        denied = self.store.replace("early-count", TARGET, 2, LATER)
        self.assertEqual(receipt_fields(denied)[:-1],
                         ("early-count", TARGET, 2, Status.FAILED, None))
        self.assertIsInstance(denied.reason, str)
        self.check_entry(self.store, 1, ORIGINAL)
        self.assertEqual(self.store.replace("first-count", TARGET, 1, UPDATED).status,
                         Status.SUCCEEDED)

        reopened = Store(self.path)
        self.assertEqual(receipt_fields(reopened.replace("early-count", TARGET, 2, LATER)),
                         receipt_fields(denied))
        self.assertEqual(receipt_fields(reopened.recover("early-count")), receipt_fields(denied))
        self.check_entry(reopened, 2, UPDATED)
        self.assertEqual([row[0] for row in self.version_rows()], [1, 2])

    def test_key_binds_target_revision_and_value_for_success_and_failure(self):
        self.store.create("shelf-blue", ORIGINAL)
        success = self.store.replace("count-ok", TARGET, 1, UPDATED)
        failure = self.store.replace("count-denied", TARGET, 1, UPDATED)
        self.assertEqual((success.status, failure.status), (Status.SUCCEEDED, Status.FAILED))
        for original in (success, failure):
            for target, revision, value in (
                ("shelf-blue", 1, UPDATED), (TARGET, 2, UPDATED), (TARGET, 1, LATER)
            ):
                with self.subTest(key=original.key, target=target, revision=revision, value=value):
                    with self.assertRaises(KeyReuseError):
                        self.store.replace(original.key, target, revision, value)
                    self.assertEqual(receipt_fields(self.store.recover(original.key)),
                                     receipt_fields(original))
        self.check_entry(self.store, 2, UPDATED)
        self.assertEqual(self.store.read("shelf-blue").revision, 1)
        self.assertEqual([row[0] for row in self.version_rows()], [1, 2])

    def test_dual_writers_from_one_revision_have_exactly_one_winner(self):
        barrier = threading.Barrier(2, timeout=10)

        def writer(key, value):
            store = Store(self.path)
            origin = store.read(TARGET)
            barrier.wait()
            result = store.replace(key, TARGET, origin.revision, value)
            return origin.revision, result, value

        with ThreadPoolExecutor(max_workers=2) as workers:
            jobs = [workers.submit(writer, "count-a", UPDATED),
                    workers.submit(writer, "count-b", LATER)]
            results = [job.result(timeout=25) for job in jobs]

        self.assertEqual([origin for origin, _, _ in results], [1, 1])
        self.assertCountEqual([receipt.status for _, receipt, _ in results],
                              [Status.SUCCEEDED, Status.FAILED])
        for _, receipt, proposed in results:
            self.assertEqual(receipt_fields(Store(self.path).recover(receipt.key)),
                             receipt_fields(receipt))
            if receipt.status == Status.SUCCEEDED:
                self.assertEqual(receipt.result_revision, 2)
                self.check_entry(self.store, 2, proposed)
                winner = receipt.key
            else:
                self.assertIsNone(receipt.result_revision)
        rows = self.version_rows()
        self.assertEqual([row[0] for row in rows], [1, 2])
        self.assertEqual(rows[-1][2], winner)

    def test_delivery_checks_the_revision_again_when_called(self):
        receipt = self.store.replace("count-a", TARGET, 1, UPDATED)
        summary = {"warehouse_label": "five boxes"}
        self.assertEqual(self.store.deliver(TARGET, receipt.result_revision, summary), summary)
        self.store.replace("count-b", TARGET, 2, LATER)
        with self.assertRaises(StaleRevisionError):
            Store(self.path).deliver(TARGET, receipt.result_revision, summary)
        self.check_entry(self.store, 3, LATER)


class EvidenceTests(InventoryCase):
    def setUp(self):
        super().setUp()
        result = self.store.replace("count-a", TARGET, 1, UPDATED)
        self.assertEqual(result.status, Status.SUCCEEDED)

    def alter(self, sql, params):
        # Each case has a new fixture. Direct SQL simulates disjoint evidence
        # that normal public operations cannot produce.
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            cursor = connection.execute(sql, params)
            self.assertEqual(cursor.rowcount, 1)

    def check_unknown_without_writes(self):
        evidence = self.evidence()
        reopened = Store(self.path)
        receipt = reopened.recover("count-a")
        self.assertEqual(receipt.status, Status.UNKNOWN)
        self.assertIsNone(receipt.result_revision)
        self.assertEqual(reopened.replace("count-a", TARGET, 1, UPDATED).status,
                         Status.UNKNOWN)
        self.assertEqual(self.evidence(), evidence)

    def test_mismatched_current_body_disallows_success_and_delivery(self):
        self.alter("UPDATE entries SET body = ? WHERE target = ?", ('{"boxes":91}', TARGET))
        self.check_unknown_without_writes()
        with self.assertRaises(InconsistentStoreError):
            self.store.read(TARGET)
        with self.assertRaises(InconsistentStoreError):
            self.store.deliver(TARGET, 2, {"warehouse_label": "five boxes"})

    def test_missing_historical_version_disallows_success_and_reapplication(self):
        self.store.replace("count-b", TARGET, 2, LATER)
        self.alter("DELETE FROM versions WHERE target = ? AND revision = ?", (TARGET, 2))
        self.check_unknown_without_writes()

    def test_missing_receipt_with_surviving_version_keeps_the_key_unknown(self):
        self.alter("DELETE FROM operations WHERE op_key = ?", ("count-a",))
        self.check_unknown_without_writes()
        self.assertEqual(self.store.recover("count-a").reason, "inconsistent_evidence")

    def test_malformed_historical_seed_invalidates_the_chain_after_an_update(self):
        self.alter("UPDATE versions SET body = ? WHERE target = ? AND revision = 1",
                   ("{broken", TARGET))
        self.check_unknown_without_writes()
        with self.assertRaises(InconsistentStoreError):
            self.store.read(TARGET)


class ReservationTests(InventoryCase):
    def test_changed_reservation_cannot_apply_the_original_mutation(self):
        for field, altered in (("proposal", '{"boxes":42}'), ("state", "FAILED")):
            with self.subTest(field=field):
                class AlteredReservation(Store):
                    def _apply(self, conn, key, target, expected, body):
                        if field == "proposal":
                            sql = "UPDATE operations SET proposal = ? WHERE op_key = ?"
                        else:
                            sql = "UPDATE operations SET state = ? WHERE op_key = ?"
                        changed = conn.execute(sql, (altered, key)).rowcount
                        if changed != 1:
                            raise AssertionError("fixture did not change exactly one reservation")
                        return super()._apply(conn, key, target, expected, body)

                key = "count-drift-" + field
                result = AlteredReservation(self.path).replace(key, TARGET, 1, UPDATED)
                self.assertEqual(result.status, Status.UNKNOWN)
                self.assertIsNone(result.result_revision)
                reopened = Store(self.path)
                self.assertEqual(reopened.recover(key).status, Status.UNKNOWN)
                self.check_entry(reopened, 1, ORIGINAL)
                self.assertEqual([row[0] for row in self.version_rows()], [1])


# Running this in a newly launched interpreter leaves no shared Python state
# between the writer and the process that subsequently recovers the operation.
CRASH_WRITER = """
import os
import sys
from mutation_consistency_kit import Store


class InterruptedWriter(Store):
    def _apply(self, conn, *args, **kwargs):
        result = super()._apply(conn, *args, **kwargs)
        if sys.argv[2] == "before-commit":
            assert conn.in_transaction
            revision, = conn.execute(
                "SELECT revision FROM entries WHERE target = ?", ("shelf-amber",)
            ).fetchone()
            assert revision == 2
            os._exit(73)
        return result

    def _after_commit(self):
        if sys.argv[2] == "after-commit":
            os._exit(74)


InterruptedWriter(sys.argv[1]).replace(
    "count-interrupted", "shelf-amber", 1, {"item": "paper clips", "boxes": 5}
)
"""


class CrashTests(InventoryCase):
    def interrupt_writer(self, point, code):
        child = subprocess.run(
            [sys.executable, "-B", "-c", CRASH_WRITER, str(self.path), point],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(child.returncode, code, child.stdout + child.stderr)

    def test_exit_after_sql_before_commit_does_not_persist_the_inventory_update(self):
        self.interrupt_writer("before-commit", 73)
        reopened = Store(self.path)
        self.check_entry(reopened, 1, ORIGINAL)
        self.assertEqual([row[0] for row in self.version_rows()], [1])
        receipt = reopened.recover("count-interrupted")
        self.assertEqual(receipt.status, Status.UNKNOWN)
        self.assertIsNone(receipt.result_revision)
        evidence = self.evidence()
        with self.assertRaises(KeyReuseError):
            reopened.replace("count-interrupted", TARGET, 1, LATER)
        again = reopened.replace("count-interrupted", TARGET, 1, UPDATED)
        self.assertEqual(receipt_fields(again), receipt_fields(receipt))
        self.check_entry(reopened, 1, ORIGINAL)
        self.assertEqual(self.evidence(), evidence)

    def test_exit_after_commit_before_response_recovers_exactly_one_update(self):
        self.interrupt_writer("after-commit", 74)
        reopened = Store(self.path)
        self.check_entry(reopened, 2, UPDATED)
        receipt = reopened.recover("count-interrupted")
        self.assertEqual(receipt_fields(receipt)[:-1],
                         ("count-interrupted", TARGET, 1, Status.SUCCEEDED, 2))
        again = reopened.replace("count-interrupted", TARGET, 1, UPDATED)
        self.assertEqual(receipt_fields(again), receipt_fields(receipt))
        self.assertEqual([row[0] for row in self.version_rows()], [1, 2])


if __name__ == "__main__":
    unittest.main()
