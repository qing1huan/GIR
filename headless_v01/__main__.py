"""Offline CLI: run, step, inspect, and verify a recorded replay."""
import argparse
import json
from pathlib import Path

from .compiler import compile_program, make_plan
from .runtime import Runtime, replay
from .types import Fault, canonical_json


def load(path):
    text = Path(path).read_text()
    if len(text.encode("utf-8")) > 16 * 1048576: raise Fault("BudgetExceeded", {"resource": "CLI_file_bytes"}, "parse")
    def reject(value): raise Fault("NumericOverflow", {"constant": value}, "parse")
    return json.loads(text, parse_constant=reject)


def write(directory, name, value):
    (directory / name).write_text(canonical_json(value)+"\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Candidate GameIR v0.1 headless runtime (development only)")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ["run", "step", "replay"]:
        p = sub.add_parser(name)
        p.add_argument("case"); p.add_argument("--output", required=True)
        if name == "step": p.add_argument("--snapshot"); p.add_argument("--request")
        if name == "replay": p.add_argument("--journal", required=True); p.add_argument("--snapshot")
    sub.add_parser("inspect").add_argument("snapshot")
    args = parser.parse_args(argv)
    try:
        if args.action == "inspect": print(canonical_json(load(args.snapshot))); return 0
        case = load(args.case); core = compile_program(case["program"]); plan = make_plan(core)
        output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
        options = {"seed": case.get("seed", 1), "budgets": case.get("budgets"), "capabilities": case.get("capabilities")}
        if args.action == "replay":
            checkpoint = load(args.snapshot) if args.snapshot else None
            driver_path = Path(args.snapshot).parent / "driver.json" if args.snapshot else None
            driver = load(driver_path) if driver_path and driver_path.is_file() else None
            runtime = replay(plan, case["initial"], load(args.journal), checkpoint=checkpoint, driver=driver, **options)
            results = [x["result"] for x in runtime.history]
        else:
            runtime = Runtime.restore(plan, load(args.snapshot), capabilities=options["capabilities"]) if args.action == "step" and args.snapshot else Runtime(plan, case["initial"], **options)
            if args.action == "step" and args.snapshot:
                driver_path = Path(args.snapshot).parent / "driver.json"
                if driver_path.is_file():
                    driver = load(driver_path)
                    runtime.restore_driver(driver)
            requests = [load(args.request) if args.request else case["steps"][0]] if args.action == "step" else case["steps"]
            results = []
            for request in requests:
                result = runtime.step(**request); results.append(result)
                if result["status"] == "ABORTED": break
        for name, value in [("core.json", core), ("plan.json", plan), ("snapshot.json", runtime.snapshot()),
                            ("journal.json", runtime.history), ("results.json", results),
                            ("driver.json", {"halted": runtime.halted, "failed_request": runtime.failed_request})]: write(output, name, value)
        print(canonical_json({"status": results[-1]["status"] if results else "EMPTY", "ticks_committed": runtime.snapshot()["tick"],
                              "action": args.action, "snapshot": str(output/"snapshot.json"), "model_runs": 0}))
        # Expected failure fixtures deliberately exit 2; assertions are external test oracles.
        return 2 if results and results[-1]["status"] == "ABORTED" else 0
    except (Fault, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError, RecursionError) as error:
        print(canonical_json({"status": "ERROR", "code": error.code if isinstance(error, Fault) else type(error).__name__,
                              "details": error.details if isinstance(error, Fault) else str(error)}))
        return 2


if __name__ == "__main__": raise SystemExit(main())
