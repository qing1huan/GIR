"""Offline evidence command. No credential/config-file input and no live mode."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from headless_v01.compiler import digest
from headless_v01.types import canonical_json
from .adapter import MODEL, prepare, SimulatedTransport
from .budget import Ledger
from .offline import simulate_attempt
from .oracle import score_authored_control
from .protocol import not_run_records, pair, protocol, validate_pair
from .reference import control_program, starter
from .tasks import TASKS


def build_evidence():
    p = protocol()
    controls = []
    audits = []
    requests = []
    for task_index, tid in enumerate(TASKS):
        arms = pair(tid, starter(tid))
        validate_pair(arms)
        if task_index % 2: arms.reverse()
        for arm in arms:
            rep = arm["representation"]
            req = prepare(arm["messages"], 1024)
            text = canonical_json(control_program(tid))
            if rep == "wrapper_format": text = "export default defineGame(" + text + ");"
            reports = score_authored_control(tid, text, rep)
            controls.append({"task_id": tid, "representation": rep, "kind": "AUTHORED_CONTROL_ONLY",
                             "program_digest": digest(text), "tests": reports})
            # This payload is explicitly synthetic; it is never attributed to Ark.
            response = json.dumps({"model": MODEL, "choices": [{"message": {"role": "assistant", "content": "offline-placeholder"},
                       "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}})
            ledger = Ledger()
            simulate_attempt(ledger, req, SimulatedTransport([response]))
            audits.append({"task_id": tid, "representation": rep, **ledger.export()})
            requests.append({"task_id": tid, "representation": rep, "body": req.body,
                             "request_digest": req.request_digest, "prompt_units": req.prompt_units})
    mutants = []
    for rep in p["representations"]:
        text = canonical_json(control_program("P-C02-debug", inverted=True))
        if rep == "wrapper_format": text = "export default defineGame(" + text + ");"
        reports = score_authored_control("P-C02-debug", text, rep)
        mutants.append({"representation": rep, "kind": "KNOWN_BUG_CONTROL", "tests": reports})
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", "ark_eval"], text=True))
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit, dirty = None, True
    observations = [r for c in controls for r in c["tests"]]
    summary = {"kind": "LEGACY_WRAPPER_FORMAT_CONTROLS_ONLY_NOT_CALLBACK_BENCHMARK", "source_commit": commit, "ark_eval_dirty": dirty,
               "python": sys.version, "control_checks": len(observations),
               "control_pass": sum(r["status"] == "PASS" for r in observations),
               "control_fail": sum(r["status"] == "FAIL" for r in observations),
               "known_bug_rejected_checks": sum(r["status"] == "FAIL" for c in mutants for r in c["tests"]),
               "simulated_attempts": len(audits), "real_model_calls": 0,
               "generated_code_executions": 0, "ai_pass_rate": None,
               "model_arm_counts": {"PASS": 0, "FAIL": 0, "NOT_RUN": 6},
               "a09": "NOT_RUN", "a12": "NOT_RUN", "ir_frozen": False,
               "protocol_digest": digest(p), "protocol_drift": protocol() != p}
    return {"summary": summary, "protocol": p, "controls": controls,
            "known_bug_controls": mutants, "audits": audits, "model_runs": not_run_records(),
            "public_requests": requests}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new evidence directory (must not exist)")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("Output already exists; preserve previous evidence and choose a new directory.")
    evidence = build_evidence()
    output.mkdir(parents=True)
    for key, value in evidence.items():
        (output / (key + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(canonical_json(evidence["summary"]))
    return int(evidence["summary"]["control_fail"] > 0 or evidence["summary"]["known_bug_rejected_checks"] == 0
               or evidence["summary"]["protocol_drift"])


if __name__ == "__main__": raise SystemExit(main())
