"""Small paired development protocol and explicit readiness/NOT_RUN records."""
from dataclasses import asdict
import copy
from pathlib import Path

from headless_v01.compiler import digest
from .adapter import EvaluationError, MODEL, PLAN_URL
from .budget import Budget
from .tasks import SHARED_API, TASKS, public_input

ROOT = Path(__file__).resolve().parents[1]
SHARED_FILES = ["headless_v01/compiler.py", "headless_v01/runtime.py", "headless_v01/types.py",
                "headless_v01/services.py", "headless_v01/plan_checks.py",
                "ark_eval/tasks.py", "ark_eval/oracle.py", "ark_eval/final_tests.json",
                "ark_eval/bridge.py", "ark_eval/adapter.py", "ark_eval/budget.py",
                "ark_eval/protocol.py", "ark_eval/reference.py", "ark_eval/offline.py",
                "ark_eval/__main__.py", "ark_eval/build_corpus.py", "ark_eval/callback_contract.py",
                "ark_eval/host.py", "ark_eval/callback_verify.py"]


def protocol():
    return {"schema": "ark-paired-pilot@1", "stage": "DEVELOPMENT_NOT_FROZEN",
            "representations": ["gameir_text", "wrapper_format"], "requested_model": MODEL,
            "exact_model_revision": None, "base_url": PLAN_URL,
            "budget_per_task_arm": asdict(Budget()), "max_output_tokens_per_attempt": 1024,
            "seeds": [1, 123], "task_ids": list(TASKS),
            "repetition": 1, "order": "alternate representation order by task index",
            "common_api_digest": digest(SHARED_API),
            "artifact_hashes": {p: digest((ROOT/p).read_text(encoding="utf-8")) for p in SHARED_FILES},
            "oracle": "explicit evaluator-owned state/event/status cases, not the reference runtime",
            "feedback": "development checks only; generic fixed error codes; final suite sealed during run",
            "token_accounting": "OFFLINE_ESTIMATE_ONLY; provider/tokenizer ceilings required before live",
            "timeout_seconds": 10, "runtime_units": 20000,
            "holdout": False, "same_runtime": "headless_v01.Runtime@current-hashes",
            "baseline": "WRAPPER_FORMAT_CONTROL_ONLY_NOT_GENERAL_JS_TS_OR_VITRIC",
            "model_role": "Ark tested model only; Codex engineers harness",
            "live_transport": "DISABLED", "generated_execution": "DISABLED",
            "purchase_upgrade_reset_paid_fallback": "FORBIDDEN"}


def pair(task_id, starter_core=None):
    p = protocol()
    if task_id not in TASKS:
        raise EvaluationError("UNKNOWN_TASK")
    shared = {"task_id": task_id, "requirement": TASKS[task_id]["requirement"],
              "model": p["requested_model"], "model_revision": p["exact_model_revision"],
              "budget": p["budget_per_task_arm"], "seeds": p["seeds"],
              "runtime_and_oracle": p["artifact_hashes"], "api_digest": p["common_api_digest"],
              "feedback": p["feedback"], "timeout": p["timeout_seconds"],
              "starter_digest": digest(starter_core), "max_output_tokens": p["max_output_tokens_per_attempt"]}
    return [{"representation": rep, "shared": copy.deepcopy(shared),
             "messages": public_input(task_id, rep, starter_core)} for rep in p["representations"]]


def validate_pair(arms):
    if (not isinstance(arms, list) or len(arms) != 2
            or any(not isinstance(a, dict) or not isinstance(a.get("shared"), dict) for a in arms)
            or {a.get("representation") for a in arms} != {"gameir_text", "wrapper_format"}
            or arms[0].get("shared") != arms[1].get("shared")):
        raise EvaluationError("UNMATCHED_PAIR")
    # Reject changed budgets/docs/messages, even if the caller edits both arms together.
    shared = arms[0]["shared"]
    if not isinstance(shared.get("task_id"), str) or shared["task_id"] not in TASKS:
        raise EvaluationError("UNKNOWN_TASK")
    if shared["task_id"] == "P-C02-create":
        expected = pair(shared["task_id"])
    else:
        from .reference import starter
        expected = pair(shared["task_id"], starter(shared["task_id"]))
    if arms != expected:
        raise EvaluationError("PROTOCOL_DRIFT")
    return True


def not_run_records():
    blockers = ["PLAN_AUTOMATED_EVAL_PERMISSION_UNKNOWN", "ACCOUNT_MODEL_REVISION_UNKNOWN",
                "PLAN_NO_POSTPAID_FALLBACK_UNCONFIRMED", "CREDENTIAL_OPERATION_USER_ONLY",
                "GENERATED_CODE_ISOLATION_UNVERIFIED", "FULL_JS_TS_BASELINE_ABSENT",
                "TOKENIZER_CEILINGS_UNVERIFIED"]
    return [{"task_id": tid, "representation": rep, "status": "NOT_RUN", "blockers": blockers,
             "kind": "LEGACY_FORMAT_RECORD_NOT_CALLBACK_BENCHMARK_ARM",
             "attempts": 0, "repairs": 0, "tool_calls": 0, "tokens_in": None, "tokens_out": None,
             "pass_at_1": None, "final_pass": None, "real_model_calls": 0,
             "requested_model": MODEL, "observed_model_revision": None}
            for tid in TASKS for rep in ("gameir_text", "wrapper_format")]
