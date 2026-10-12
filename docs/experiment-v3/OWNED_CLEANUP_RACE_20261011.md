# Owned container cleanup exit race: local patch and evidence

This change fixes a witnessed `inspect` → `top` exit race in the existing
`ark_eval.node_backend.WorkerProcess.close`. It does not execute external
candidates, change the runtime/oracle, or grant an execution permit.

## Root cause and evidence boundary

At the unchanged base `e6cdc6ced6564c2b0e35a3d82dd7fb230e5b00cf`, bounded
trusted disconnect diagnosis ran 30 controls: 29 PASS and one cleanup FAIL.
Trial 030 inspected the same owned container as running with host PID 247534;
its next `top` returned 125. The 50 stderr bytes have SHA256
`a284e11ae9972fd028e8161d5a9f25f36549ac833c557173725da2392411aeb3`, exactly
matching `Error: top can only be used on running containers\n`. A subsequent
read-only inspection found exited, PID 0, exit code 1, and no OOM. The profile
checks and rollback/replay passed. The cleanup code treated the normal exit
between its checks as a generic control failure and disabled the backend.

This is an observed check/exit race, not evidence of resource exhaustion.
The earlier production failure saved only the generic cleanup exception,
so its exact internal command cannot be recovered. The reproduced signature
supports this narrow repair; it does not retrospectively identify that command.

Evidence directory:
`results/pilot24-owned-cleanup-diagnosis-20261011-001/`.
Relevant files are `old-trial-030.json`, `old-trial-030-followup.json`, and
`trusted-cgroup-path-probe.json`. The latter establishes that systemd removes
both the container cgroup child and its libpod scope on exit, while the ancestor
remains accessible. Original failure evidence was preserved before exact-ID,
non-force removal of the exited diagnostic container.

## Minimal repair and safety conditions

The adapter now pins the container ID returned by `create`, checks that same
ID, image, worker-source label and existing strict profile, and uses that ID
for process listing, kill and removal. Only the exact bounded
`top/125/not-running` error enters the exit-race path. It re-inspects the owned
container and requires strictly non-running, integer PID 0, exited/stopped,
and no OOM/paused/restarting/dead state.

The bound cgroup must show `cgroup.events populated 0` for its whole subtree,
or its removed branch must be absent in the first accessible existing ancestor
under `/sys/fs/cgroup`. Missing events in an existing scope, unreadable paths,
path drift, a reappearing branch, changed identity/profile, other `top` errors
and removal failures continue to block. Zero survivors are recorded only after
successful removal. Diagnostics save fixed stage/code/return-code categories;
CLI stderr, credentials and host environment are not logged.

The disconnect test, existing isolation profile, resource bounds, runtime,
oracle, candidates, historical freezes, receipts, claims and permits are unchanged.
No new recovery gate or framework was created.

## Final validation and retained incomplete attempts

Final source SHA256:

- `ark_eval/node_backend.py`: `d4186cf55b5348835a8bf446faf05afb622629acd87dd3d11d2749951dfbf833`
- `ark_eval/tests/test_owned_cleanup.py`: `0856b4fc88bbd99cffd6f7e6a83499812142e671a2239d8ef24d6d6880a626bc`

Python 3.11.6 on Windows passed 16 targeted tests. The original headless verifier
passed all 63 unique methods, including 12 end-to-end cases and 7 CLI commands;
acceptance remains 10 PASS / 0 FAIL / 2 NOTRUN (A09 and A12), IR not frozen.

Final trusted validation 004 used WSL Ubuntu-24.04 Python 3.12.3, rootless
Podman 4.9.3, cgroup v2/systemd and the existing locked Node v24.21.0 image.
It passed all 12 fault controls, one real inspect/top exit window, and 30 natural
disconnect controls; five profile checks passed and the owned-container list
was empty. The 42 fault/disconnect controls have 41 passing replays and one
specified duplicate-key refusal. Generated-source/candidate/model calls were
zero; trusted reviewed catalog calls were four.

The final summary is
`fixed-validation-004/fixed-validation-summary.json`, SHA256
`ab857ee185b192d64527d6a9e129e589b1c657658c71f9d6d5bbbebc07593cef`.
Astra reviewed the final code and all 48 files of 004: code PASS, trusted
controls PASS, overall execution admission BLOCKED.

All earlier attempts remain recorded. Attempt 001 stopped on an incorrect
observer assertion about the trusted catalog counter, after its first fixture
passed. Attempt 002 passed 12 faults but its exit proof safely rejected the
simultaneous cgroup-parent removal; the actual path probe justified the final
ancestor check. Attempt 003 passed 12 faults, the real exit window and 17 natural
disconnects, then `fault_case` raised `IndexError` on missing runner audits at
trial 18 (one HARNESS_ERROR, 12 subsequent NOTRUN). That internal cause was
not recorded. Attempt 004 adds a pass-through close-error observer and passes
completely; it does not prove the 003 error was cured or change its status.

Full discovery has 450 methods (371 original ark methods, 63 headless methods,
16 new methods). 409 methods ran. By method the result is 274 PASS / 16 FAIL /
119 ERROR / 41 NOTRUN, with unittest reporting 21 failure and 215 error events
including subtests/class setup. The frozen dependencies reject the new backend
via `PAIR_CONTRACT_DRIFT`, `V4_HISTORY_CHANGED`,
`PILOT24_FROZEN_DEPENDENCY_DRIFT` and `P24_FROZEN_FILE_DRIFT`.
`final-regression-results-002.json` separates methods, subtest events and
class-setup NOTRUN. The earlier aggregate-only recorder omitted subtest outcomes
and is retained, superseded by this precise result. This is not a full regression
PASS. Historical freeze files were not rewritten to hide those failures.

## Historical scoring and remaining work

Independent SHA256 checks verified all historical registry sets:
1156 original files, 23 failed-zero-start files, 188 consumed-continuation files
and 99 continuation-audit files. There are still exactly 21 reservations and
21 PASS durable results from the consumed continuation; the old claims and
permits remain unchanged. Combined scoring stays **212 PASS / 2 FAIL /
73 NOTRUN / 1 HARNESS_ERROR**, denominator 288.

`remaining61.json` lists the unchanged 61 unreserved rows with source/case
digests; its file SHA256 is
`0d26e15eb3c6c0367f61cff15682d037cee6b35dc545ae24499da0563c8ee227`.

| Slot | Unreserved cases | Count |
|---|---|---:|
| C1-r2-js_callback | R04 | 1 |
| C1-r2-gameir_text | N01–N04, B01–B04, R01–R04 | 12 |
| M2-r2-gameir_text | N01–N04, B01–B04, R01–R04 | 12 |
| M2-r2-js_callback | N01–N04, B01–B04, R01–R04 | 12 |
| M1-r2-js_callback | N01–N04, B01–B04, R01–R04 | 12 |
| M1-r2-gameir_text | N01–N04, B01–B04, R01–R04 | 12 |

The original unknown C2-r2-js_callback/N02 is excluded and was not replayed.
No new execution qualification, reservation, claim, intent, candidate run or
model request occurred in this task. There was no credential access, provider
request, purchase, reset-card use or push. Trusted profile probes test a denied
network path inside the isolated container; they do not perform provider requests.

Before further scoring, the parent must review how to bind this changed backend
as a new execution version while preserving the immutable history and exact
remaining set. The consumed old permit cannot be reused; no new permit is
prepared here. The 003 observer gap is an explicit remaining limitation.
These controls establish engineering behavior and do not establish AI advantage.

## Commands actually used

Existing output directories are consumed evidence and must not be rerun.

```powershell
& D:\python3.11\python.exe -X utf8 -m unittest ark_eval.tests.test_owned_cleanup -v
& D:\python3.11\python.exe -X utf8 results/pilot24-owned-cleanup-diagnosis-20261011-001/record-regressions-002.py
& D:\python3.11\python.exe -X utf8 -m headless_v01.verify --output results/pilot24-owned-cleanup-headless-20261011-001
& D:\python3.11\python.exe -X utf8 results/pilot24-owned-cleanup-diagnosis-20261011-001/audit-history-and-remaining.py
wsl -d Ubuntu-24.04 --cd /mnt/d/GIR -- python3 results/pilot24-owned-cleanup-diagnosis-20261011-001/trusted-disconnect-probe.py
wsl -d Ubuntu-24.04 --cd /mnt/d/GIR -- python3 results/pilot24-owned-cleanup-diagnosis-20261011-001/trusted-cgroup-path-probe.py
wsl -d Ubuntu-24.04 --cd /mnt/d/GIR -- python3 results/pilot24-owned-cleanup-diagnosis-20261011-001/trusted-fixed-validation-004.py
```
