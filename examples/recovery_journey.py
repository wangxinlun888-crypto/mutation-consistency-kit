"""Run one local stock correction, response loss and recovery in order."""
import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import uuid

from mutation_consistency_kit import Receipt, StaleRevisionError, Status, Store


TARGET = "rack-lilac"
FIRST, SECOND = "recount-01", "recount-02"
BEFORE, AFTER = ("create", "query", "correct", "duplicate", "original_receipt",
                 "after_commit_before_reply"), ("recover", "final_state", "old_revision")
STEPS = (*BEFORE, "writer_exit", *AFTER, "reader_exit")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def stock(count):
    return {"item": "felt pads", "packs": count}


def expected(key, previous):
    return Receipt(key, TARGET, previous, Status.SUCCEEDED, previous, previous + 1, "applied")


def event(step, run_id, **fields):
    return dict(step=step, run=run_id, pid=os.getpid(), parent_pid=os.getppid(),
                utc=datetime.now(timezone.utc).isoformat(), **fields)


def worker(role, database, run_id):
    def emit(step, **fields):
        print(json.dumps(event(step, run_id, **fields)), flush=True)

    if role == "writer":
        require(not database.exists(), "database must be new")
        store = Store(database)
        first = store.create(TARGET, stock(4))
        require((first.target, first.revision, first.value) == (TARGET, 1, stock(4)), "create")
        emit("create", snapshot=asdict(first))
        read = store.read(TARGET)
        require(read == first, "read")
        emit("query", snapshot=asdict(read))
        receipt = store.replace(FIRST, TARGET, read.revision, stock(6))
        require(receipt == expected(FIRST, 1), "correction")
        emit("correct", receipt=asdict(receipt))
        duplicate = store.replace(FIRST, TARGET, 1, stock(6))
        head = store.read(TARGET)
        require(duplicate == receipt and (head.revision, head.value) == (2, stock(6)), "replay")
        emit("duplicate", receipt=asdict(duplicate), head_revision=head.revision)
        original = store.recover(FIRST)
        require(original == receipt, "original receipt")
        emit("original_receipt", receipt=asdict(original))

        class LostResponse(Store):
            def _after_commit(self):
                # Test observation only. The caller never receives a business receipt.
                emit("after_commit_before_reply", key=SECOND)
                os._exit(74)

        LostResponse(database).replace(SECOND, TARGET, head.revision, stock(9))
        raise AssertionError("lost-response operation unexpectedly returned")

    require(database.is_file(), "cannot recover without the writer's database")
    store = Store(database)
    recovered = store.recover(SECOND)
    require(recovered == expected(SECOND, 2), "recover without resubmission")
    require(store.recover(FIRST) == expected(FIRST, 1), "earlier receipt changed")
    emit("recover", receipt=asdict(recovered), prior_receipt=asdict(store.recover(FIRST)))
    final = store.read(TARGET)
    require((final.target, final.revision, final.value) == (TARGET, 3, stock(9)), "final head")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        history = [(v, json.loads(b), k) for v, b, k in conn.execute(
            "SELECT revision,body,op_key FROM versions WHERE target=? ORDER BY revision", (TARGET,))]
        operations = conn.execute("SELECT op_key,state FROM operations ORDER BY op_key").fetchall()
    require(history == [(1, stock(4), None), (2, stock(6), FIRST), (3, stock(9), SECOND)], "history")
    require(operations == [(FIRST, "SUCCEEDED"), (SECOND, "SUCCEEDED")], "unexpected operations")
    emit("final_state", snapshot=asdict(final), history=history, operations=operations)
    try:
        store.deliver(TARGET, 2, "six packs")
    except StaleRevisionError as exc:
        require(store.deliver(TARGET, 3, "nine packs") == "nine packs", "fresh delivery")
        emit("old_revision", rejected_revision=2, error=type(exc).__name__, accepted_revision=3)
    else:
        raise AssertionError("obsolete output was released")


def journey(directory):
    run_id = uuid.uuid4().hex
    database = directory / "stock.sqlite"
    report = dict(run=run_id, package=version("mutation-consistency-kit"), python=sys.version.split()[0],
                  sqlite=sqlite3.sqlite_version, steps=[], result="FAIL")
    rows = report["steps"]
    try:
        for role, names, exit_code in [("writer", BEFORE, 74), ("reader", AFTER, 0)]:
            with subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                   "--worker", role, str(database), run_id],
                                  cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                try:
                    out, err = child.communicate(timeout=40)
                except subprocess.TimeoutExpired:
                    child.kill()
                    out, err = child.communicate()
                    (directory / (role + ".stdout")).write_bytes(out)
                    (directory / (role + ".stderr")).write_bytes(err)
                    raise
                (directory / (role + ".stdout")).write_bytes(out)
                (directory / (role + ".stderr")).write_bytes(err)
                observations = [json.loads(line) for line in out.splitlines()]
                # On Windows a venv may launch the interpreter through a redirector.
                require(len({row["pid"] for row in observations}) == 1 and all(
                    row["run"] == run_id and (row["pid"] == child.pid or
                    (os.name == "nt" and row["parent_pid"] == child.pid))
                    for row in observations),
                        "wrong run or process")
                rows.extend(observations)
                require([row["step"] for row in observations] == list(names), "unexpected worker steps")
                require(not err and child.returncode == exit_code, "unexpected worker exit or stderr")
                rows.append(event(role + "_exit", run_id, launcher_pid=child.pid,
                                  worker_pid=observations[0]["pid"], exit_code=child.returncode,
                                  caller_outcome="UNKNOWN" if role == "writer" else "VERIFIED"))
        require([row["step"] for row in rows] == list(STEPS), "incomplete journey")
        report["result"] = "PASS"
    except Exception as exc:
        report["failure"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        report["not_observed"] = [step for step in STEPS if step not in {row["step"] for row in rows}]
        output = json.dumps(report, indent=2)
        (directory / "journey.json").write_text(output + "\n", encoding="utf-8")
        print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--worker", nargs=3, help=argparse.SUPPRESS)
    group.add_argument("--evidence-dir", type=Path, help="new directory for this run")
    args = parser.parse_args()
    if args.worker:
        role, path, run = args.worker
        require(role in ("writer", "reader"), "invalid worker role")
        worker(role, Path(path).resolve(), run)
    elif args.evidence_dir:
        dest = args.evidence_dir.resolve()
        dest.mkdir(exist_ok=False)
        journey(dest)
    else:
        with TemporaryDirectory() as tmp:
            journey(Path(tmp))
