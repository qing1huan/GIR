"""Produce mechanical evidence and an honest full-contract acceptance gap matrix."""
import argparse
import csv
from datetime import datetime, UTC
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest

from .compiler import digest
from .coverage import audit
from .examples import cases
from .tests.test_end_to_end import assert_case
from .types import canonical_json

DESIGN_COMMIT = "8867abb421dc4bce70473ad55aa81ba8ec919305"
ROOT = Path(__file__).resolve().parents[1]


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw); self.records = []; self.traces = {}
    def addSuccess(self, test):
        super().addSuccess(test); self.records.append({"test": test.id(), "status": "PASS"})
        if hasattr(test, "evidence"): self.traces[test.id()] = test.evidence
    def addFailure(self, test, err):
        super().addFailure(test, err); self.records.append({"test": test.id(), "status": "FAIL"})
    def addError(self, test, err):
        super().addError(test, err); self.records.append({"test": test.id(), "status": "FAIL"})
    def addSkip(self, test, reason):
        super().addSkip(test, reason); self.records.append({"test": test.id(), "status": "NOT RUN", "reason": reason})


def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True); args = parser.parse_args()
    output = Path(args.output).resolve(); output.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "headless_v01"], cwd=ROOT, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError): commit, dirty = None, "unversioned source bundle"
    started = time.perf_counter()
    started_utc = datetime.now(UTC).isoformat()
    suite = unittest.defaultTestLoader.discover(str(ROOT/"headless_v01/tests"), top_level_dir=str(ROOT))
    with (output/"unit-tests.log").open("w") as log:
        log.write("command: python -m unittest discover -s headless_v01/tests -t . -v\n")
        log.write("Actually invoked by verify using unittest loader/TextTestRunner with the same suite; no model execution.\n")
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
    source_hash = digest({str(p.relative_to(ROOT)): p.read_text() for p in sorted((ROOT/"headless_v01").rglob("*.py"))})
    summary = {"kind": "REFERENCE_ENGINEERING_ONLY", "design_commit": DESIGN_COMMIT, "source_commit": commit,
               "headless_dirty": bool(dirty), "python": sys.version, "platform": platform.platform(),
               "source_text_digest": source_hash, "started_utc": started_utc,
               "test_count": result.testsRun, "passed": sum(r["status"] == "PASS" for r in result.records),
               "failed": len(result.failures)+len(result.errors), "skipped": len(result.skipped),
               "elapsed_seconds_including_CLI_subprocesses": time.perf_counter()-started,
               "ai_runs": 0, "tokens": None, "actual_gameplay_coverage": None, "gameplay_escape_rate": None,
               "first_ai_pass_rate": None, "final_ai_pass_rate": None, "modification_regression_rate": None,
               "debug_cost": None, "runtime_overhead_ratio": None, "ai_status": "NOT_RUN",
               "ai_blocker": "Model evaluation not run; this verifier performs reference engineering tests only"}
    save(output/"tests.json", {"summary": summary, "tests": result.records})
    if not result.wasSuccessful(): print(canonical_json(summary)); return 1
    accounting = audit(result.records)
    save(output/"coverage-audit.json", accounting)
    with (output/"coverage-audit.csv").open("w") as f:
        fields = ["test", "status", "kind", "criteria", "fixture", "first_slice"]
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        writer.writerows([{**r, "criteria": " ".join(r["criteria"])} for r in accounting["tests"]])
    for id_, trace in result.traces.items(): save(output/(id_+"-trace.json"), trace)
    traces = []; assertions = unittest.TestCase()
    for id_, fixture in cases().items():
        # The published serialized fixture must agree with its public generation source.
        assertions.assertEqual(json.loads((ROOT/"headless_v01/cases"/(id_+".json")).read_text()), fixture)
        runtime, results = assert_case(assertions, fixture)
        save(output/(id_+"-trace.json"), {"case": fixture, "results": results, "snapshot": runtime.snapshot(), "journal": runtime.history})
        traces.append({"id": id_, "development_set": True, "holdout_eligible": False, "assertions": "PASS",
                       "runtime_status": results[-1]["status"], "error": results[-1]["error"]["code"] if results[-1]["error"] else None,
                       "ticks": runtime.snapshot()["tick"], "microsteps_attempted": sum(r["budget_attempted"].get("microsteps", 0) for r in results),
                       "commands_committed": sum(len(r["commands"]) for r in results), "state_digest": digest(runtime.snapshot())})
    save(output/"development-cases.json", traces)
    with (output/"development-cases.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(traces[0])); writer.writeheader(); writer.writerows(traces)
    gaps = {
        "A01": ("PASS", "Closed supported types and numeric/resource corpus", ""),
        "A02": ("PASS", "Copy, queued values, special keys, map equality and key order", ""),
        "A03": ("PASS", "Reference authority, explicit reads/effects, pure extension restrictions", ""),
        "A04": ("PASS", "Root rollback including earlier microsteps, RNG/IDs/cursor/future/outbox and retry", ""),
        "A05": ("PASS", "Snapshot query including typed predicates; own overlay/other jobs, dynamic alias, stale targets, nearest tie and overflow rollback", ""),
        "A06": ("PASS", "Exclusive/add conflicts, priority tie/loser, float fold, clamp/death corpus", ""),
        "A07": ("PASS", "Root classes/BFS/future, limits and event cycles in initialized-cell subset", ""),
        "A08": ("PASS", "Door/Health to Core; cost/source preserving constant-fold Plan; float/RNG/observation mutant world differences; override re-expansion conflict", ""),
        "A09": ("NOT RUN", "Recorded services plus scripted delayed failed Ack, no resend on restore, provider-free replay, capability version and pure extension budget subchecks PASS", "Physical Backend change and real adapter capability validation not run; scripted mock does not satisfy the full original fixture"),
        "A10": ("PASS", "Typed instance namespaces; preserve Q1/Q2, explicit Burn rebase remaining6/nextPulse and pending events, Err/overflow/cumulative budget atomic rollback, restart cancellation, missing package/schema/ABI reject", ""),
        "A11": ("PASS", "Draft create/replace/delete/connect/query; typed event ports, revision guard, invalid control reject; conservative unknown impact; atomic boundary activation; draft discard and published restore as new revision", ""),
        "A12": ("NOT RUN", "No AI attribution; Core/Plan dispatch consistency; reviewed isolated JS pilot evidence is separate", "Fair matched-budget model evaluation, TS and original Vitric baseline remain absent; JS reference correctness does not satisfy A12"),
    }
    original = {entry["id"]: entry for entry in json.loads((ROOT/"design/architecture-v0.1/acceptance.json").read_text())["entries"]}
    matrix = []
    for id_, (status, checked, missing) in gaps.items():
        test_ids = sorted({r["test"] for r in accounting["tests"] if id_ in r["criteria"]})
        matrix.append({"id": id_, "status": status, "tested_scope": checked, "missing": missing,
                       "original_fixture_requirement": original[id_]["fixture_requirement"],
                       "original_oracle": original[id_]["future_executable_oracle"],
                       "test_ids": test_ids, "command": "python -m headless_v01.verify --output OUTPUT",
                       "source_commit": commit, "design_commit": DESIGN_COMMIT, "raw_log": "unit-tests.log",
                       "scope": "HEADLESS_CLOSURE; PASS is original acceptance corpus scoped, not full architecture/AI proof"})
    save(output/"acceptance-results.json", matrix)
    with (output/"acceptance-results.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(matrix[0])); writer.writeheader(); writer.writerows([{**r, "test_ids": json.dumps(r["test_ids"])} for r in matrix])
    commands = []
    def command(argv, expected=0):
        run = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
        commands.append({"argv": argv, "cwd": str(ROOT), "expected_exit": expected, "exit_code": run.returncode, "stdout": run.stdout, "stderr": run.stderr})
        assertions.assertEqual(run.returncode, expected, run.stdout+run.stderr)
    cli = output/"cli"
    command([sys.executable, "-m", "headless_v01", "run", "headless_v01/cases/PT11.json", "--output", str(cli/"death")])
    command([sys.executable, "-m", "headless_v01", "replay", "headless_v01/cases/PT11.json", "--journal", str(cli/"death/journal.json"), "--output", str(cli/"death-replay")])
    assertions.assertEqual((cli/"death/snapshot.json").read_bytes(), (cli/"death-replay/snapshot.json").read_bytes())
    command([sys.executable, "-m", "headless_v01", "inspect", str(cli/"death/snapshot.json")])
    command([sys.executable, "-m", "headless_v01", "step", "headless_v01/cases/PT13.json", "--output", str(cli/"formation")])
    reply_file = output/"request-pt13-reply.json"; save(reply_file, cases()["PT13"]["steps"][1])
    command([sys.executable, "-m", "headless_v01", "step", "headless_v01/cases/PT13.json", "--snapshot", str(cli/"formation/snapshot.json"), "--request", str(reply_file), "--output", str(cli/"formation-reply")])
    for id_ in ["PT14", "PT15"]:
        command([sys.executable, "-m", "headless_v01", "run", "headless_v01/cases/"+id_+".json", "--output", str(cli/id_)], 2)
    save(output/"commands.json", commands)
    (output/"cli-commands.log").write_text("\n".join("$ "+" ".join(c["argv"])+"\n"+c["stdout"]+c["stderr"]+"exit_code="+str(c["exit_code"]) for c in commands)+"\n")
    summary["cli_commands"] = len(commands); summary["end_to_end_asserted_cases"] = len(traces)
    summary["closure_raw_test_traces"] = len(result.traces)
    summary["unique_engineering_methods"] = accounting["unique_method_count"]
    summary["original_46_includes_12_fixtures"] = True
    summary["source_vs_plan_is_same_interpreter"] = True
    summary["acceptance_counts"] = {status: sum(r["status"] == status for r in matrix) for status in ["PASS", "FAIL", "NOT RUN"]}
    summary["gate"] = "HEADLESS_CLOSURE_PASS; FULL_ARCHITECTURE_NOT_PASSED; NOT_FROZEN"
    summary["completed_utc"] = datetime.now(UTC).isoformat()
    save(output/"summary.json", summary)
    print(canonical_json(summary)); return 0


if __name__ == "__main__": raise SystemExit(main())
