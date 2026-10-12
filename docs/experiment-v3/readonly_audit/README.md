# Pilot24 file-only audit utilities

These five standard-library readers are byte-for-byte copies of the completed
local takeover tools. They neither import the GIR evaluator nor execute any
candidate, call model APIs, inspect processes, launch/resume scoring, or change
production results, claims, reservations, permits, source answers or freezes.
They are independent audits, not scoring gates or controller receipts.

The original local experiment at `D:\GIR` is required. A public clone lacks the
raw answers, reservations and immutable evidence; missing artifacts must remain
missing and the audit must fail closed. Do not regenerate artifacts, reuse a
consumed claim or lower a hash gate to make a public clone pass.

| File | Purpose |
|---|---|
| `poll-score-files.py` | Small file-only progress inventory; counts are not verified scores. |
| `verify-observed-results.py` | Verify first-recorded result hashes across derived snapshots. |
| `observe-forward51.py` | Check original source/case/freeze bindings, reservations, oracle, replay, cleanup and limits; default single round writes a new independent snapshot. |
| `audit.py` | Full original 172-file, 1509-file sealed evidence, 127 prior-production and current 30-binding audit; stdout only. |
| `prepare-score-material.py` | Produce reporting material from a validated snapshot; stdout only. |

Run on the retained local bundle, with Python bytecode writes disabled:

```powershell
& D:\python3.11\python.exe -B D:\GIR\docs\experiment-v3\readonly_audit\poll-score-files.py
& D:\python3.11\python.exe -B D:\GIR\docs\experiment-v3\readonly_audit\verify-observed-results.py
& D:\python3.11\python.exe -B D:\GIR\docs\experiment-v3\readonly_audit\observe-forward51.py --output results/pilot24-forward51-file-observation-20261012-001
& D:\python3.11\python.exe -B D:\GIR\docs\experiment-v3\readonly_audit\audit.py
& D:\python3.11\python.exe -B D:\GIR\docs\experiment-v3\readonly_audit\prepare-score-material.py
```

Use the observer's default one-round mode only. Do not use `--watch`, start a
background reader, query WSL/CIM/processes, request escalation or start another
scorer. The output directory is separate from production and snapshots are
exclusive new files. For a read-only environment, do not invoke its writing
`main()`; `Observer().snapshot()` can be called in memory without persisting
anything. The user-report cursor remains a separate local artifact and must be
reconciled before reporting an old row again.

`KEY`/`scope_digest` in these sources are experiment identity hashes, not account
credentials. No env/key files or local secret directory is accessed. Public
source hashes identify exact code; they do not independently verify private
production observations.

Exact SHA256 of the published source copies:

| File | SHA256 |
|---|---|
| `audit.py` | `e77916b24d92382a46602a03a436ebbd49b26077dbca39c4131a456342601629` |
| `observe-forward51.py` | `9014ddb66f0a5769ee9da6abae4610f6c98276ac0c323ac288c5899476b7b04a` |
| `verify-observed-results.py` | `164ec92769388adb3928ff5e7f25a02c6992a2bf819a03be15dfd436da743e79` |
| `poll-score-files.py` | `0f347b2fd04cb3f537947265e71d94d5a5f528a99ab4a058d5f2606bbbd0a322` |
| `prepare-score-material.py` | `5a7227f2d6d645e8619fab1510e182df8d3f0c587892fa1aac588951f053963b` |
