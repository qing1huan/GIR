# Pilot24 private CMD collection entry

This entry is collection-only and remains blocked pending one focused sending review. Do not run the sending command before that review is recorded as PASS for the exact entry digest. Agents may prepare requests and run mock tests, but may not read the actual key, invoke the sending flag, or execute collected candidates. The material freeze stays unchanged at `5db1fbc7855755e0cae1c033294bee8a46c43192c669419da781350d4e2aefa1`.

After the focused review passes, the human opens a private CMD and runs these exact commands. The proposed output directory must not exist; it has not been created by the agent. The key-file path is the user-provided path and is not inspected in this development phase.

```cmd
cd /d D:\GIR
D:\python3.11\python.exe -X utf8 -m ark_eval.manual_pilot24 --user-local-send-pilot24 --plan-status-confirmed --key-file D:\GIR-secrets\ark-plan.key --output D:\GIR\results\pilot24-user-local-001
```

The entry shows the model, material digest, output, scope and caps. Enter `SEND_PILOT24` only when the existing personal Plan permits the script, current quota is sufficient and postpaid is disabled. The explicit key file is read using the existing `local_key_file.read_local_key` helper only after this confirmation in an interactive terminal. No key value is requested in chat, printed, logged, persisted, or passed to a child process. No key environment discovery, ordinary paid endpoint, model alias fallback, upgrade, purchase, reset card or SDK retry is available. Only the fixed Plan HTTPS transport is reused.

The six specifications each have two representations and two fresh independent two-message requests: 24 slots in `docs/pilot24/schedule.json`. There are 12 distinct public request bodies, each used twice with no answer/history feedback. Every request has `max_tokens=4096`, temperature zero, thinking disabled and streaming disabled. The total output reservation is 98,304 tokens. Input reservation remains the frozen 24,000 byte-plus-overhead estimate units; no compatible tokenizer verifies strict input token matching or Plan quota consumption. Actual trusted usage is recorded separately. There are no repairs, tools, retries, extra probes or adaptive additions.

A fixed one-use claim under `results/pilot24-send-claims/<scope_digest>/` binds the original 24-slot approval ID and material digest, independently of output directory and entry version. It is created atomically before any HTTP request and never released. Another output directory cannot reuse this approval. Each attempted slot has a flushed exclusive reservation and an append-only checkpoint before transport. An interruption during transport or subsequent file I/O retains the claim, the last unknown-outcome checkpoint and all remaining initial NOTRUN records. The checkpoint reports model-call lower/upper bounds; a reserved unknown request is 0..1, never silently zero. Completed trusted responses narrow the bound to 1..1. Inspect these artifacts after a crash; do not remove the claim, rerun, resume, change directories or automatically resend.

All response-processing exceptions explicitly clear continuation and return no candidate, including cancellation after response classification. Already validated call counts and usage are retained; an interruption before validation remains 0..1. An exclusive `response-receipt.json` records the validated outcome with `RESPONSE_RECEIVED_PERSISTENCE_PENDING` before source or final audit writes. After a persistence failure, use this receipt's known counts when present; otherwise the earlier reservation/checkpoint remains the conservative bound. A receipt does not claim that candidate saving or the batch completed. Persistence errors propagate to the fixed outer cancellation/error handler and cannot dispatch the next slot; no audit or checkpoint is overwritten.

The frozen response policy distinguishes trusted answer defects from provider uncertainty. Valid model/usage/finish envelopes with truncation, empty content or source size failure are generation failures and the fixed remaining schedule continues. A truncated source within the source byte cap is saved inert. Provider/auth/quota/redirect errors, mismatched model, invalid or missing usage, reasoning content, unknown transport outcome, secret echo, or frozen binding drift halt the remaining slots as NOTRUN. Provider raw JSON, error text, headers and exception messages are discarded. Safe candidate source text is saved as `candidate.txt`; it is never imported, parsed as executable code, compiled, evaluated or sent to a worker. Secret echoes are rejected before any text persistence. Future syntax/ABI/functional classification and execution require a separate exact candidate review and scope approval; this entry performs none of them.

There are **six formal specifications and 72 fixed primary scoring scenario definitions** (12 per specification). The 24 future generations would produce 288 primary case evaluations. The separate 48 v0 checks test old contracts plus preserved behavior of the authored starters. Therefore the 120 total control scenario definitions are 72 primary plus 48 old-contract controls, not 120 formal questions or primary cases. Running these across both representations produced 240 positive control observations; 36 negative checks and replay observations are additional engineering evidence. No collection result is itself gameplay PASS; all 288 planned primary evaluations remain NOTRUN and full contract NOT_CHECKED here.

Offline preparation uses a different fresh directory and cannot consume the send approval or read a key:

```cmd
D:\python3.11\python.exe -X utf8 -m ark_eval.manual_pilot24 --output D:\GIR\results\pilot24-offline-preview-NEW
D:\python3.11\python.exe -X utf8 -m unittest ark_eval.tests.test_manual_pilot24 -v
```

Preserve the old v4 bundle, claim, execution evidence and material archives. This new entry does not change the material freeze, existing runtime, candidate gates, oracle or prior approval. No public push is authorized.
