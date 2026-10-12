"""Evaluator-owned independent expected observations, never prompt input."""
import json
from pathlib import Path

from headless_v01.runtime import Runtime, replay
from headless_v01.types import Fault, canonical_json
from .adapter import EvaluationError
from .bridge import decode_data_program
from .tasks import base_program, task

SUITE = Path(__file__).with_name("final_tests.json")


def final_cases(task_id):
    task(task_id)
    return [c for c in json.loads(SUITE.read_text(encoding="utf-8"))["cases"] if c["task_id"] == task_id]


def observations(runtime, results):
    return {"entities": runtime.snapshot()["entities"],
            "statuses": [r["status"] for r in results],
            "events": [{"name": e["name"], "payload": e["payload"]}
                       for r in results if r["status"] == "COMMITTED" for e in r["events"]
                       if not e["id"].startswith("root:")],
            "commands": [c for r in results for c in r["commands"]],
            "errors": [r["error"]["code"] if r["error"] else None for r in results]}


def score_authored_control(task_id, program_text, representation):
    """Engineering-only path for reviewed, hand-written controls in this repository.

    Only exact repository-authored control text is accepted. This does not provide
    OS isolation; the public candidate execution path remains disabled in bridge.py.
    """
    from .reference import control_program
    authored = [base_program(), control_program(task_id), control_program(task_id, inverted=True)]
    permitted = [canonical_json(p) for p in authored]
    if representation == "wrapper_format":
        permitted = ["export default defineGame(" + p + ");" for p in permitted]
    if (not isinstance(program_text, str) or representation not in {"gameir_text", "wrapper_format"}
            or program_text not in permitted):
        return [{"case_id": c["id"], "status": "NOT_RUN", "reason": "UNREVIEWED_CONTROL_BLOCKED"}
                for c in final_cases(task_id)]
    reports = []
    try:
        core = decode_data_program(program_text, representation)
    except EvaluationError:
        return [{"case_id": c["id"], "status": "FAIL", "reason": "STATIC_REJECTION"}
                for c in final_cases(task_id)]
    for case in final_cases(task_id):
        try:
            runtime = Runtime(core, case["initial"], seed=case["seed"])
            results = [runtime.step(**step) for step in case["steps"]]
            equal = observations(runtime, results) == case["expected"]
            deterministic = replay(core, case["initial"], runtime.history, seed=case["seed"]).snapshot() == runtime.snapshot()
            reports.append({"case_id": case["id"], "status": "PASS" if equal and deterministic else "FAIL",
                            "reason": None if equal and deterministic else "ORACLE_OR_REPLAY_MISMATCH"})
        except (Fault, ValueError, TypeError, KeyError):
            reports.append({"case_id": case["id"], "status": "FAIL", "reason": "CONTROL_RUNTIME_FAULT"})
    return reports
