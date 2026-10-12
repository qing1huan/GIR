# Pilot24 unreserved 60 continuation

The consumed v3 controller exited after reserving C1-r2-js_callback/R04, without
a durable case result. Preserve it as HARNESS_ERROR / UNKNOWN_0_OR_1. Preserve the
original C2-r2-js_callback/N02 unknown, all 214 determinate results, the 12 static
NOTRUN rows, every old freeze, permit, launch and claim. The fixed denominator is
288: 212 PASS, 2 FAIL, 72 NOTRUN and 2 HARNESS_ERROR before this continuation.

The first internal exception from v3 was discarded by its diagnostic whitelist,
and its detached controller stderr went to /dev/null. Do not claim a recovered
root cause. Trusted virtual-clock controls reproduce two facility defects:
post-reservation scope validation can age a 580-second proof beyond its unchanged
600-second TTL; JS metadata validation after WorkerProcess construction can
consume its unchanged 10-second deadline. The narrow repair keeps scope/parser/
source/case/ABI checks, prepares metadata before invocation, and renews only an
age-expired proof through actual trusted profile and fixed fault checks. Changed
backend identity, image, hash, disabled state or scope still rejects. Never extend
created timestamps or worker/resource budgets. Record fixed first code/stage,
never raw messages. Existing authored JS/IR C1 R04 controls and real forced proof
renewal validate the repair without executing any collected answer.

`pilot24_forward60_scope.py` pins the v3 freeze and terminal evidence registry,
verifies all 33 production evidence files and the unchanged review evidence,
reconciles its exact one reservation and zero results, and excludes R04. Its only
allowed rows are the original remaining 60, not retries. Logical identity is
independent of code hashes; changed code alone cannot create another opportunity
to run a consumed row. The current v4_2 freeze has its own bindings and permit.
The unconsumed v4 freeze is retained byte-for-byte; v4_1 adds real age-only renewal
for IR admission after metadata checks, using exclusive numbered evidence
directories. This closes a conditional completion risk, not a proven historical
cause. The logical 60-row key is unchanged, so this revision cannot create another
execution opportunity. The v4_1 freeze is also retained byte-for-byte. A real
trusted control identified 106 changed files in the installed public Python
package; no repository runtime source changed. v4_2 restores all 701 original
public runtime bytes from the retained, previously audited immutable local image,
without executing it or changing system packages. The fixed archive has an exact
SHA, file allowlist and per-file hashes; limits and path/link checks apply, with
no fallback to the changed system Python. Current repository public sources are
still checked against the old package, and require_public_package remains intact.
The original public runtime freeze, worker bytes, image fingerprint algorithm,
resource budgets, ABI and observation semantics are unchanged. Defaults
and v3 cannot acquire execution authority under changed code. v1-v3 freezes are
never rewritten. The only additional historical integration delta is the thin
Pilot24 adapter, whose worker source, public runtime package and NodeBackend are
unchanged. All historical hashes and exact file-set checks remain enforced.

Prepare/status are read-only with respect to authorization and claims:

```
python -B -m ark_eval.pilot24_recovery --forward60 --output results/pilot24-recovery-score-forward60-prepared-001
python -B -m ark_eval.pilot24_recovery --forward60 --status
```

Execution uses the existing atomic direct WSL launcher and controller. It requires
an exact separately reviewed v4 permit derived from the existing user instruction
to finish the original 288 isolation scores, plus a stopped prior PID/start-tick
identity, fresh resource isolation, parser/admission and per-case ledger. It stops
on the first infrastructure/persistence failure; never delete a claim, retry an
unknown case, generate new model answers or make an API request. Refresh workers
are trusted isolation overhead, not scored primary cases or model calls.
