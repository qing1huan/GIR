> Historical pre-launch snapshot. The exact-51 claim has since been consumed. Current verified progress is **235 PASS / 2 FAIL / 49 NOTRUN / 2 historical unknown**, with 14/51 durable results and no terminal receipt, as of 2026-10-12 16:43:54 UTC. See [the takeover report](PILOT24_TAKEOVER_20261012.md). Do not follow the historical launch instruction below or start the consumed scorer again.

# Pilot24 progress snapshot — 2026-10-12

This is an incomplete research/engineering progress snapshot, not a completed
288-case result or a stable release. The original 24 answers remain unchanged
in local evidence. No model request, reset, candidate regeneration or repair was
made during this scoring continuation.

## Current result

| Fixed 288 rows | Count |
|---|---:|
| PASS | 221 |
| FAIL | 2 |
| NOTRUN | 63 |
| HARNESS_ERROR / reserved unknown | 2 |

The latest consumed 60-row controller stopped without a terminal summary after
nine reservations, each with a durable PASS and verified replay/cleanup. There
are no new reserved unknowns. Independent administrative reconciliation leaves
all other 279 rows unchanged. Kernel uptime and absent-process observations show
that the execution environment restarted; the trigger is unknown.

Of the 63 NOTRUN rows, twelve are preserved static syntax failures and 51 have
never been reserved. The latter alone are eligible for the new forward51 gate.
Both historical reserved unknowns remain excluded and will not be retried. The
two FAIL results remain unchanged. Scoring is not yet complete.

## Included work

- Headless Game IR and the paired JavaScript callback evaluation code, authored
  cases, control fixtures, public task material, offline tests and experiment plan.
- Receipt-age handling that performs real isolation rechecks after slow metadata
  validation, with the original ten-second worker deadline and resource limits.
- Recovery of the exact original 701-file public runtime payload from a retained
  immutable image, enforced by archive and per-file hashes; no system package
  replacement or relaxed runtime gate.
- Exact remainder providers and immutable freeze records. The latest v5_1 adds
  only the 51 never-reserved rows, preserves all historical freeze checks, and
  uses the existing one-use launch/claim/reservation mechanism.

The current v5_1 scope digest is
`63d2c9432483f0a9843eda05436a077a838ccd09493556e7ffcf99de68506628`;
its freeze SHA-256 is
`fc22a6b6b71ed2ea5d15db24922be12092a84440e617e9a95004baa03cc3d0f9`.
The consumed run's 127 production files have inventory digest
`c3e9e0a96c64241d1021a92d945b153ecc802113f8d360208a183c5149713ef6`.
The original imported-answer manifest SHA-256 remains
`403c429a6475c4459b3bc5757d2503d80b5b9957ba35dd4455e326b1cf610a28`.

## Validation and limits

On the original local evidence bundle, all 27 focused tests passed for v5_1:

```text
python -B -m unittest ark_eval.tests.test_pilot24_forward51 ark_eval.tests.test_pilot24_receipt_timing ark_eval.tests.test_pilot24_public_payload -v
```

Independent Astra read-only review passed after fixing a missing transitive
v4/v4_1 freeze check. The superseded v5 freeze was retained byte-for-byte, and the
logical exact-51 key did not change. This review does not replace fresh runtime
isolation checks before launch.

The earlier v4_2 full discovery was not green: 501 discovered, 445 run, 309 PASS,
16 FAIL, 120 ERROR and 56 NOTRUN. Preserved historical freeze-gate failures and a
historical mock instrumentation error are not reclassified as passes. The full
suite has not been rerun for this snapshot.

For the public checkout, the receipt timing regression tests use authored mocks:

```text
python -B -m unittest ark_eval.tests.test_pilot24_receipt_timing -v
```

The full exact-evidence tests and production scoring require the original local
bundle and fail closed when it is absent. This upload intentionally omits raw
model answers, raw run logs, permits, claims, launch receipts, administrative
evidence, runtime archives/images, account-status records and legacy account/run
narratives. References to those local-only artifacts remain explicit; their
absence is not an instruction to regenerate them or loosen checks. No credential
or env file is included.

The snapshot is based on the remote main commit and does not publish the 39
intermediate local development commits. Those commits and all original evidence
remain unchanged locally. `main` is not updated by publishing this progress branch.

## Next step

Perform fresh original isolation controls on the same computer, then consume the
exact-51 gate once. Keep the existing `/mnt/d` path binding for this continuation.
A Linux-local storage migration would need a separate path/comparability review.
Do not infer A/B/C or multi-round maintenance results from this no-feedback pilot;
follow [PLAN.md](PLAN.md) and [PILOT24_FORWARD51.md](PILOT24_FORWARD51.md).
