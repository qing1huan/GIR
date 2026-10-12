"""Reproduce phase 1/2 evidence with reviewed IR and in-memory callback controls."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from headless_v01.compiler import digest
from .adapter import MODEL, SimulatedTransport, prepare
from .budget import Ledger, verify_chain
from .callback_contract import callback_pair, callback_protocol, model_records, static_js_preflight, validate_callback_pair
from .host import DoorMockRunner, HostRuntime, replay_host
from .oracle import final_cases, observations
from .offline import simulate_attempt
from .reference import control_program
from .tasks import TASKS, base_program, task


def callback_manifest():
    p = base_program()
    p["handlers"] = [{"id": "open", "event": "Open", "priority": 0, "body": []}]
    return p


def control_check(task_id, representation, inverted=False):
    reports = []
    for case in final_cases(task_id):
        if representation == "gameir_text":
            program = control_program(task_id, inverted)
            r = HostRuntime(program, case["initial"], seed=case["seed"])
        else:
            program = callback_manifest()
            r = HostRuntime(program, case["initial"], seed=case["seed"],
                            runners={"open": DoorMockRunner(task(task_id)["cost"], inverted)})
        results = [r.step(**s) for s in case["steps"]]
        expected = observations(r, results) == case["expected"]
        played = replay_host(program, case["initial"], r.history, r.execution_history,
                             seed=case["seed"], callback=representation == "js_callback")
        deterministic = played.snapshot() == r.snapshot()
        reports.append({"case_id": case["id"], "status": "PASS" if expected and deterministic else "FAIL",
                        "oracle_match": expected, "replay_match": deterministic,
                        "execution_audits": r.execution_history, "results": results})
    return reports


def evidence():
    before = callback_protocol()
    controls = []; pairs = []; checks = []; audits = []; requests = []
    for task_index, tid in enumerate(TASKS):
        arms = callback_pair(tid); validate_callback_pair(arms); pairs.extend(arms)
        checks.append({"task_id": tid, **static_js_preflight(arms[1]["starter"])})
        for arm in arms if task_index % 2 == 0 else arms[::-1]:
            request = prepare(arm["messages"], arm["shared"]["max_output_tokens"])
            ledger = Ledger()
            response = json.dumps({"model": MODEL, "choices": [{"message": {"role": "assistant", "content": "offline-placeholder"},
                                   "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}})
            imported = simulate_attempt(ledger, request, SimulatedTransport([response]))
            audit = ledger.export()
            audits.append({"task_id": tid, "representation": arm["representation"],
                           "kind": "SYNTHETIC_ADAPTER_AUDIT_NOT_MODEL_RUN",
                           "status": "PASS" if imported is not None and not audit["halted"] and verify_chain(audit["events"]) else "FAIL",
                           **audit})
            requests.append({"task_id": tid, "representation": arm["representation"], "body": request.body,
                             "request_digest": request.request_digest, "prompt_units": request.prompt_units})
        for rep in ("gameir_text", "js_callback"):
            controls.append({"task_id": tid, "representation": rep,
                             "kind": "REVIEWED_IR_OR_PYTHON_CALLBACK_SIMULATOR_NO_JS_EXECUTION",
                             "tests": control_check(tid, rep)})
    mutants = [{"representation": rep, "tests": control_check("P-C02-debug", rep, True)}
               for rep in ("gameir_text", "js_callback")]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", "ark_eval", "headless_v01"], text=True))
    except (FileNotFoundError, subprocess.CalledProcessError): commit, dirty = None, True
    tests = [t for c in controls for t in c["tests"]]
    bug_tests = [t for c in mutants for t in c["tests"]]
    summary = {"kind": "PHASE_1_2_OFFLINE_ENGINEERING_ONLY", "source_commit": commit, "dirty": dirty,
               "python": sys.version, "scope": "SAME_TRUSTED_GAME_HOST_GAMEIR_VS_JS_CALLBACK",
               "control_counts": {s: sum(t["status"] == s for t in tests) for s in ("PASS", "FAIL", "NOT_RUN")},
               "known_bug_counts": {s: sum(t["status"] == s for t in bug_tests) for s in ("PASS", "FAIL", "NOT_RUN")},
               "model_counts": {"PASS": 0, "FAIL": 0, "NOT_RUN": 6}, "source_executions": 0,
               "real_model_calls": 0, "simulated_attempts": len(audits),
               "simulated_audit_counts": {s: sum(a["status"] == s for a in audits) for s in ("PASS", "FAIL")},
               "js_compute_units": None, "ts_status": "DEFERRED",
               "isolation": "PHASE12_SIMULATION_NO_OS_ISOLATION_CLAIM", "plan_permission": "UNKNOWN",
               "a12": "NOT_RUN", "ir_frozen": False, "protocol_digest": digest(before),
               "protocol_drift": callback_protocol() != before}
    return {"summary": summary, "protocol": before, "controls": controls, "known_bug_controls": mutants,
            "public_pairs": pairs, "static_source_checks": checks, "model_runs": model_records(),
            "audits": audits, "public_requests": requests}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--output", required=True)
    args = p.parse_args(argv); output = Path(args.output).resolve()
    if output.exists(): p.error("Output exists; choose a new directory to preserve evidence.")
    data = evidence(); output.mkdir(parents=True)
    for key, value in data.items():
        (output/(key+".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps(data["summary"], ensure_ascii=False))
    return int(data["summary"]["control_counts"]["FAIL"] != 0 or data["summary"]["protocol_drift"]
               or data["summary"]["simulated_audit_counts"]["FAIL"] != 0
               or data["summary"]["known_bug_counts"]["FAIL"] == 0)


if __name__ == "__main__": raise SystemExit(main())
