# Acceptance record: 0.1.1rc1

Windows, CPython 3.14.0, SQLite 3.50.4. Tested the wheel installed without dependencies into an isolated environment. These are results of a local process-exit simulation, not power-failure, network delivery, hardware or multi-platform certification.

## One continuous journey: PASS

Both business interpreters used the same newly created synthetic SQLite file. The second was launched only after the first's exit was observed. Every row below belongs to that same run.

| Observed step | Actual result |
| --- | --- |
| Create and independently read | Both returned the same rack, 4 packs of felt pads, revision 1. |
| Correct | recount-01 committed 6 packs, 1 to 2. |
| Duplicate request | All seven receipt fields matched the first result. The head stayed at 2. |
| Recover original receipt | Read-only lookup returned the same full receipt. |
| Lose next response | recount-02 reached the after-COMMIT hook; the writer exited 74 before returning a receipt. Caller outcome was UNKNOWN. |
| New interpreter recovers | Read-only lookup of recount-02 returned SUCCEEDED/applied, 2 to 3. No write replay. The first operation still referred to revision 2. |
| Verify final state | 9 packs; exactly versions 1, 2, 3 with counts 4, 6, 9; exactly two successful operation rows. |
| Check stale output | Revision 2 raised StaleRevisionError; revision 3's output passed the local gate. Reader exited 0. |

A preliminary attempt with a Windows virtual-environment launcher mismatch stopped before recovery. It was not counted. The final harness validates the observed interpreter identity or its direct Windows launcher, plus run ID and ordered stages.

## Separate regression suite: 14 passed, 0 failed, 0 skipped

Each case below passed in the isolated installation. The crash tests use separate interpreters. They are not the evidence for the continuous journey.

- `test_exit_after_commit_before_response_recovers_exactly_one_update`: PASS
- `test_exit_after_sql_before_commit_does_not_persist_the_inventory_update`: PASS
- `test_malformed_historical_seed_invalidates_the_chain_after_an_update`: PASS
- `test_mismatched_current_body_disallows_success_and_delivery`: PASS
- `test_missing_historical_version_disallows_success_and_reapplication`: PASS
- `test_missing_receipt_with_surviving_version_keeps_the_key_unknown`: PASS
- `test_colliding_unicode_object_keys_are_rejected_before_seeding`: PASS
- `test_create_read_and_missing_receipt`: PASS
- `test_delivery_checks_the_revision_again_when_called`: PASS
- `test_dual_writers_from_one_revision_have_exactly_one_winner`: PASS
- `test_failed_precondition_stays_failed_when_the_revision_later_matches`: PASS
- `test_key_binds_target_revision_and_value_for_success_and_failure`: PASS
- `test_success_replay_identifies_the_original_revision_after_a_later_change`: PASS
- `test_changed_reservation_cannot_apply_the_original_mutation`: PASS
