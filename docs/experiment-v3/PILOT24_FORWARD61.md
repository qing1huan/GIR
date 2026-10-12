# Pilot24 forward 61 revision

This revision binds the reviewed owned-container cleanup fix and seven explicit integration
source changes to a new v3 manifest. It reuses the existing detached controller, atomic
claim, per-case ledger, admission, isolation checks, evaluators, shared oracle and replay.
There is no drift bypass. The old execution and recovery manifests and default entry
points remain strict and unchanged. The 701-file IR public package is unchanged.
Forward launch and controller preflight also require the sealed consumed controller's
PID/start-ticks identity to be valid and stopped; missing or unreadable evidence blocks.

The original 82 eligible rows minus the consumed continuation's 21 reservations yields
exactly 61: C1-r2-js_callback/R04; all 12 cases for C1-r2-gameir_text,
M2-r2-gameir_text, M2-r2-js_callback, M1-r2-js_callback and M1-r2-gameir_text.
214 determinate results, the original C2-r2-js_callback/N02 unknown, and 12 static syntax
NOTRUN rows are retained. Before any new run, the combined 288 rows remain
212 PASS, 2 FAIL, 73 NOTRUN and 1 HARNESS_ERROR. No result is rerun or reclassified.

The v3 logical recovery key binds the historical scope and consumed evidence plus the
exact 61 source/case pairs, independently of integration source hashes. One-use launch
and claim directories use that new key. The old permit is rejected. A separately reviewed
explicit forward61 permit must match the v3 digest and exact range; no permit is supplied
or written by preparation. A reservation remains consumed even if execution or persistence
fails. Unconfirmed outcomes remain UNKNOWN and cannot be retried.

The JS precheck collects the same profile and all 12 fixed faults before constructing a
Pilot24 receipt; the legacy pair receipt retains its old binding gate. Trusted fixture
diagnostics retain the first infrastructure exception as fixed stage/type metadata, never
raw exception text. Missing audit evidence blocks with an explicit diagnostic gap.
The historical validation-003 first internal exception was not recorded: this revision
does not claim to establish that historical cause. Cleanup exceptions disable the backend
and cannot produce fixture PASS, even if an older audit claimed zero survivors.

Safe preparation (does not execute candidates, take a claim or write approval):

```powershell
D:\python3.11\python.exe -B -X utf8 -m ark_eval.pilot24_recovery --forward61 --output results/pilot24-recovery-score-forward61-prepared-001
D:\python3.11\python.exe -B -X utf8 -m ark_eval.pilot24_recovery --forward61 --status
```

Future execution, only after focused review and a separately authorized exact permit at
`results/pilot24-forward61-permit.json`, uses the existing launcher with `--forward61
--execute-authorized-zero-start`. It must run in the qualified direct WSL context. This
implementation task does not execute that command, create that permit, execute collected
candidates, access API credentials, change machine permissions or publish anything.

Validation is limited to mocks and existing trusted controls. Mock PASS is a controller
check, not a model result. Old regression assertions blocked by old source freezes remain
FAIL/ERROR/NOTRUN with their original evidence; they are not changed to PASS.
