# Pilot24 offline materials

Six modification specifications × two native representations × two independent sessions = at most 24 future model requests. This package has 12 correct v0 starters, 12 authored v1 controls, 72 fixed primary scene definitions, 48 v0 contract checks and 36 paired defect programs. No model generation or external candidate execution has occurred in this material phase.

`public/` contains only shared semantics, paired syntax documentation, requirements, fixed ABI and each arm's own starter. `private/` contains independently authored literal expected worlds/ticks, v0 checks, target controls and mutants. The public-only request builder uses five explicit paths per arm. Evaluator-side leakage checks are separate from construction. `protocol.draft.json` records 12 full two-message requests and 24 deterministic slots; repeated slots use identical bodies and fresh sessions. It is a preview with send and candidate execution disabled.

`pilot24_authored.py` constructs repository-authored IR and separately written native JS; JS never wraps or interprets IR. `pilot24_oracles.py` imports no control, compiler or runtime and expands only explicit hand-written fixture deltas. Expected results never come from running a control. `pilot24_trusted.py` admits only exact disk matches for its finite authored catalog (task, version, defect index); it has no source/path argument for external candidates. Its small HostRuntime test configuration installs the same task ABI and uses the unchanged NodeBackend/worker/profile/IPC. Existing candidate gates, workers, runtime, permissions and v4 evidence are unchanged.

## Verified results and limits

- Windows Python 3.11.6: 256 original Ark methods and 27 new methods passed, deduplicated by test name across recorded runs; 63 original headless methods passed including the 12 end-to-end fixtures. Seven CLI checks passed; acceptance remains 10 PASS / 0 FAIL / 2 NOTRUN (A09/A12).
- WSL Python 3.12.3, Node v24.21.0, rootless Podman 4.9.3: 96 v0 and 144 v1 scene/control evaluations passed; 36 negative evaluations detected the predeclared defects; all 276 replays passed. Normal observations and 18 paired defect observations match across representations. These are engineering controls, not AI results.
- 156 physical JS worker invocations have clean audit/cleanup. A separate read-only Podman inventory found no remaining worker containers. Maximum measured host operations/tick=17, wire requests/handler=16, wire requests/tick=24; bootstrap=1108 bytes, request line=249 bytes, response line=168 bytes. Existing hard limits remain unchanged.
- Target IR: 44–71 nodes, 4640–7405 bytes using readable one-space JSON; byte/3 planning estimates 1547–2469 tokens. This heuristic is not the Plan model tokenizer. Actual 4096-token fit remains NOT_VERIFIED. Every public request is 7104–12399 byte+overhead estimate units, below 24000; units are not input tokens. No probe request, budget increase, task reduction or added model request was used.

`freeze.json` seals materials and existing runtime dependencies; status is MATERIALS_SEALED_PENDING_INDEPENDENT_REVIEW. Its digest excludes the digest field itself, using the existing canonical digest routine. It binds the exact trusted validation summary and evidence. A seal does not authorize sending or generated-source execution, does not establish Plan availability or strict equal total token usage, and does not prove the full contract or IR advantage. Independent review must examine paired starter quality, literal oracle correctness, public information symmetry, the fixed-control adapter and the tokenizer limitation before a user-local batch entry is attached.

## Reproduction

Use fresh evidence output directories, retaining prior results. No credentials are needed for these commands:

```powershell
& 'D:\python3.11\python.exe' -X utf8 -m ark_eval.pilot24_materials --preview
& 'D:\python3.11\python.exe' -X utf8 -m unittest ark_eval.tests.test_pilot24_materials ark_eval.tests.test_pilot24_trusted_controls -v
& 'D:\python3.11\python.exe' -X utf8 -m headless_v01.verify --output results/NEW_HEADLESS_OUTPUT
```

Existing WSL isolated-control command (with a new output directory when reproducing):

```powershell
& 'C:\Windows\System32\wsl.exe' -d Ubuntu-24.04 --cd /mnt/d/GIR --exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C /usr/bin/python3 -m ark_eval.pilot24_trusted --output results/NEW_TRUSTED_OUTPUT
```

Actual first-run control evidence: `results/pilot24-trusted-controls-20261011-001/summary.json` and six per-task JSON files. Unit evidence: `results/pilot24-engineering-regression-20261011-001/tests.json`, `results/pilot24-material-tests-20261011-003/tests.json`; headless evidence: `results/pilot24-headless-regression-20261011-001/summary.json`. Final combined integrity/material evidence is `results/pilot24-materials-validation-20261011-v4.json`; earlier reports and material seals/archives are retained. The final seal conservatively reuses all 74 old execution bindings to include transitive imports without granting old execution permissions. All model calls, credential reads, reset-card uses and new generated-source executions in this phase are zero; no push.
