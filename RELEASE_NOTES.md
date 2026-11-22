# Mutation Consistency Kit 0.1.1

## What changes

Compared with 0.1.1rc1, the 0.1.1 source tree updates packaging and installation notes and includes a [comparison of alternatives](COMPARISON.md) and [license text](LICENSE). The three runtime modules, tests and end-to-end example are unchanged. There is no schema change or data migration.

## Behavior

- Replace a complete JSON document only at its expected revision. Identical requests reuse their original operation receipt.
- If the response is lost, retain the operation key. Recover the durable result without reapplying the change; unresolved evidence stays unknown.
- Reject local output based on a revision that is no longer current.

The example uses fictional inventory and a fresh local database. It intentionally terminates a writer after commit and recovers in a different interpreter. These guarantees cover one local SQLite file, not power loss, an external system or delivery to a recipient.

## Verification

From a matching source archive, use the [install guide](docs/INSTALL.md) to run the regression tests and the two-process example against the installed wheel. The [previous acceptance record](docs/TEST_RESULTS.md) identifies its own version; it is not a test report for a newly built distribution.
