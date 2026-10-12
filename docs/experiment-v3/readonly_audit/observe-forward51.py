"""Observe the existing consumed forward51 run through allowed files only.

No GIR imports, subprocesses, process inspection, network, credentials, candidate
execution, launch, retry, cleanup or changes to production files. Writes only
derived snapshots to the explicitly separate observer output directory.
"""
import argparse
import collections
import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

ROOT = Path(r"D:\GIR")
STATIC = "docs/experiment-v3/pilot24-static-manifest.json"
FREEZE = "ark_eval/pilot24_recovery.v5_1.freeze.json"
LIVE = "results/pilot24-recovery-score-forward51-executed-20261012-001"
KEY = "f360ed6ed5d0cc1b3166b26874817dd05c5f61e3f1962315b69750dc872c3565"
CLAIM = "results/pilot24-zero-start-claims/" + KEY
ADMIN = "results/pilot24-forward60-interruption-review-20261012-001/combined-cases-admin.json"
SCOPE_DIGEST = "63d2c9432483f0a9843eda05436a077a838ccd09493556e7ffcf99de68506628"


def utc():
    return datetime.now(timezone.utc).isoformat()


def path(n):
    p = ROOT / n
    if PureWindowsPath(n).is_absolute() or ".." in Path(n).parts or p.resolve() != p:
        raise ValueError("Non-canonical repository path")
    p.relative_to(ROOT)
    if p.name.lower() in {"env.txt", ".env"} or p.suffix.lower() in {".key", ".pem"}:
        raise ValueError("Secret path rejected")
    return p


def raw(n):
    return path(n).read_bytes()


def sha(n):
    return hashlib.sha256(raw(n)).hexdigest()


def read(n):
    return json.loads(raw(n))


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def need(ok, label):
    if not ok:
        raise ValueError(label)


def counts(rows):
    c = collections.Counter(r["status"] for r in rows)
    return {k: c[k] for k in ("PASS", "FAIL", "NOTRUN", "HARNESS_ERROR")}


class Observer:
    def __init__(self):
        need(sha(STATIC) == "403c429a6475c4459b3bc5757d2503d80b5b9957ba35dd4455e326b1cf610a28", "STATIC_DRIFT")
        need(sha(FREEZE) == "fc22a6b6b71ed2ea5d15db24922be12092a84440e617e9a95004baa03cc3d0f9", "FREEZE_DRIFT")
        self.static = read(STATIC)
        self.freeze = read(FREEZE)
        need(digest(self.freeze) == SCOPE_DIGEST and self.freeze["recovery_key"] == KEY, "SCOPE_DRIFT")
        self.check_bindings()
        self.baseline = read(ADMIN)
        need(digest(self.baseline) == self.freeze["preserved_rows_digest"], "BASELINE_DRIFT")
        self.by_id = {(r["slot_id"], r["case_id"]): r for r in self.baseline}
        need(len(self.baseline) == len(self.by_id) == 288, "DENOMINATOR")
        self.slots = {s["slot_id"]: s for s in self.static["slots"]}
        self.cases = {}
        for task, entry in self.static["task_cases"].items():
            need(sha(entry["path"]) == entry["sha256"], "CASE_FILE_DRIFT")
            self.cases[task] = {c["id"]: c for c in read(entry["path"])}
        for sid, slot in self.slots.items():
            need(sha(slot["source_path"]) == slot["source_sha256"], "RAW_SOURCE_DRIFT")
        self.allowed = {(r["slot_id"], r["case_id"]): r for r in self.freeze["allowed_rows"]}
        need(len(self.allowed) == 51, "EXACT_51")
        for (sid, cid), r in self.allowed.items():
            need(r["source_sha256"] == self.slots[sid]["source_sha256"] and
                r["case_digest"] == digest(self.cases[self.slots[sid]["task_id"]][cid]), "ROW_BINDING_DRIFT")
        self.cache = {}
        self.last_signature = None
        self.last_binding_check = utc()

    def check_bindings(self):
        need(digest(read(FREEZE)) == SCOPE_DIGEST, "LIVE_FREEZE_DRIFT")
        for n, h in {**self.freeze["bindings"], **self.freeze["administrative_anchors"]}.items():
            need(sha(n) == h, "LIVE_BINDING_DRIFT: " + n)

    def validate_reservation(self, n, sid, cid):
        v = read(n)
        need(all(v[k] == self.allowed[(sid, cid)][k] for k in ("slot_id", "case_id", "source_sha256")),
            "RESERVATION_BINDING")
        need(type(v["maximum_primary_runs"]) is int and v["maximum_primary_runs"] == 1 and
            v["unknown_outcome"] == "DO_NOT_RETRY", "RESERVATION_LIMIT")

    def validate_result(self, sid, cid, v):
        slot = self.slots[sid]
        case = self.cases[slot["task_id"]][cid]
        equal = v["actual_ticks"] == case["expected_ticks"]
        need(v["expected_ticks"] == case["expected_ticks"] and v["status"] == ("PASS" if equal else "FAIL")
            and v["oracle_match"] is equal, "ORACLE_RESULT")
        need(v["replay_match"] is True and v["cleanup_verified"] is True, "REPLAY_OR_CLEANUP")
        need(type(v["primary_case_executions"]) is int and v["primary_case_executions"] == 1,
            "PRIMARY_EXECUTION_COUNT")
        ir = slot["representation"] == "gameir_text"
        need(type(v["IR_case_replays"]) is int and v["IR_case_replays"] == int(ir) and
            type(v["JS_transcript_replays"]) is int and v["JS_transcript_replays"] == int(not ir), "REPLAY_COUNT")
        need(type(v["candidate_executions"]) is int and v["candidate_executions"] >= 0, "CANDIDATE_COUNT")
        if ir:
            result = v["isolated_result"]
            need(result["source_sha256"] == slot["source_sha256"] and result["source_modified"] is False
                and result["case_id"] == cid and result["observed"] == v["actual_ticks"], "IR_SOURCE_RESULT_BINDING")
            audits = [v["worker_audit"]]
        else:
            audits = v["worker_audits"]
            need(all(a["candidate_sha256"] == slot["source_sha256"] for a in audits), "JS_SOURCE_BINDING")
        for a in audits:
            need(a["surviving_owned_processes"] == 0 and a["cleanup_errors"] == [], "WORKER_CLEANUP")
            need(a["rootless_host"]["rootless"] is True and a["network_mode"] == "none"
                and a["readonly_rootfs"] is True and a["user_mounts"] == [], "WORKER_ISOLATION")
            need(a["wall_timeout_seconds"] == self.freeze["limits"]["seconds"], "WORKER_TIME_LIMIT")
            need(a["kernel_limits"] == {"cpu.max": self.freeze["limits"]["cpu"],
                "memory.max": str(self.freeze["limits"]["memory"]), "memory.swap.max": "0",
                "pids.max": str(self.freeze["limits"]["pids"])}, "WORKER_RESOURCE_LIMITS")
            need(a["kernel_security"] == {"CapEff": "0000000000000000", "NoNewPrivs": "1", "Seccomp": "2"},
                "WORKER_KERNEL_SECURITY")

    def snapshot(self):
        claim = read(CLAIM + "/claim.json")
        need(claim["scope_digest"] == SCOPE_DIGEST and claim["recovery_key"] == KEY and
            claim["retry_or_resume_allowed"] is False, "CONSUMED_CLAIM_DRIFT")
        need(digest(read(LIVE + "/scope.json")) == SCOPE_DIGEST, "OUTPUT_SCOPE_DRIFT")
        events = [read(p.relative_to(ROOT).as_posix()) for p in sorted(path(CLAIM).glob("event-*.json"))]
        completed_events = {(e["slot_id"], e["case_id"]) for e in events if e["state"] == "PRIMARY_COMPLETED"}
        reserved, unconfirmed, pending, durable, changed = [], [], [], [], []
        merged = copy.deepcopy(self.by_id)
        for (sid, cid), row in self.allowed.items():
            suffix = sid + "-" + cid + ".json"
            rs = [n for n in (LIVE + "/reservation-" + suffix, CLAIM + "/case-" + suffix) if path(n).is_file()]
            for n in rs:
                self.validate_reservation(n, sid, cid)
            result_name = LIVE + "/result-" + suffix
            if path(result_name).is_file():
                need(bool(rs), "RESULT_WITHOUT_RESERVATION")
                data = raw(result_name)
                h = hashlib.sha256(data).hexdigest()
                cached = self.cache.get((sid, cid))
                if cached:
                    need(cached[0] == h, "DURABLE_RESULT_MODIFIED")
                    v = cached[1]
                else:
                    self.check_bindings()
                    self.last_binding_check = utc()
                    v = json.loads(data)
                    self.validate_result(sid, cid, v)
                    self.cache[(sid, cid)] = (h, v)
                    changed.append({"slot_id": sid, "case_id": cid, "status": v["status"]})
                merged[(sid, cid)].update(copy.deepcopy(v), phase="DERIVED_OBSERVED_DURABLE_RESULT", reason=None)
                durable.append({"slot_id": sid, "case_id": cid, "status": v["status"], "path": result_name,
                    "sha256": h, "completion_event_present": (sid, cid) in completed_events})
            elif rs:
                unconfirmed.append([sid, cid])
            else:
                pending.append([sid, cid])
            if rs:
                reserved.append([sid, cid])
        for base, prefix in ((LIVE, "result-"), (LIVE, "reservation-"), (CLAIM, "case-")):
            actual = {p.name[len(prefix):-5] for p in path(base).glob(prefix + "*.json")}
            need(actual <= {sid + "-" + cid for sid, cid in self.allowed}, "OUT_OF_SCOPE_ARTIFACT")
        admissions = []
        for sid in dict.fromkeys(sid for sid, cid in self.allowed):
            n = LIVE + "/admission-result-" + sid + ".json"
            if path(n).is_file():
                v = read(n)
                if self.slots[sid]["representation"] == "gameir_text":
                    need(v["result"]["source_sha256"] == self.slots[sid]["source_sha256"] and
                        v["result"]["candidate_case_executions"] == 0, "ADMISSION_BINDING")
                    state = v["result"]["admission"]
                else:
                    state = "RESULT_PRESENT_RECORDED_SEPARATELY_FROM_PRIMARY_SCORE"
                admissions.append({"slot_id": sid, "state": state, "path": n, "sha256": sha(n)})
        terminal = None
        summary_name = LIVE + "/summary.json"
        if path(summary_name).is_file():
            self.check_bindings()
            summary = read(summary_name)
            need(summary["scope_digest"] == SCOPE_DIGEST and summary["recovery_key"] == KEY and
                summary["state"] in {"REMAINDER_COMPLETE", "STOPPED_ON_BLOCK"} and
                summary["retry_or_resume_allowed"] is False and summary["model_calls"] == summary["repairs"] == 0,
                "TERMINAL_BINDING")
            combined = read(LIVE + "/combined-cases.json")
            combined_by_id = {(r["slot_id"], r["case_id"]): r for r in combined}
            need(len(combined) == len(combined_by_id) == 288 and set(combined_by_id) == set(self.by_id),
                "TERMINAL_DENOMINATOR")
            for key, old in self.by_id.items():
                if key not in self.allowed:
                    need(combined_by_id[key] == old, "PRESERVED_ROW_CHANGED")
            for key, (h, value) in self.cache.items():
                need(all(combined_by_id[key][k] == x for k, x in value.items()), "TERMINAL_DURABLE_ROW_DRIFT")
            for sid, cid in unconfirmed:
                r = combined_by_id[(sid, cid)]
                need(r["status"] == "HARNESS_ERROR" and r["primary_case_executions"] == "UNKNOWN_0_OR_1",
                    "TERMINAL_UNKNOWN_ACCOUNTING")
            for sid, cid in pending:
                r = combined_by_id[(sid, cid)]
                need(r["status"] == "NOTRUN" and r["primary_case_executions"] == 0, "TERMINAL_NOTRUN_ACCOUNTING")
            need(counts(combined) == summary["combined_fixed_288"]["counts"], "TERMINAL_COUNTS")
            merged = combined_by_id
            terminal = {"path": summary_name, "sha256": sha(summary_name), "receipt": summary,
                "row_reconciliation": "PASS", "current_live_process_state": "NOT_QUERIED_ACCESS_DENIED"}
        state = {
            "kind": "DERIVED_FILE_OBSERVATION_NOT_SCORING_CONTROLLER",
            "observed_utc": utc(), "scope_digest": SCOPE_DIGEST, "code_freeze_sha256": sha(FREEZE),
            "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "baseline_rows_digest": self.freeze["preserved_rows_digest"],
            "last_current_binding_check_utc": self.last_binding_check,
            "claim_consumed": True, "retry_or_resume_allowed": False,
            "runtime_process_state": "UNKNOWN_NO_QUERY_AFTER_ACCESS_DENIED",
            "events": events, "admissions": admissions, "last_event": events[-1] if events else None,
            "durable_new_results": durable, "newly_validated_this_poll": changed,
            "primary_reservations": reserved, "reserved_unconfirmed": unconfirmed,
            "not_reserved": pending, "fixed_288_counts": counts(merged.values()),
            "counts_final_only_if_terminal_receipt_validated": terminal is not None,
            "terminal": terminal, "observer_model_calls": 0, "observer_candidate_executions": 0,
            "original_experiment_artifacts_written": False,
        }
        signature = digest({k: v for k, v in state.items() if k not in
            {"observed_utc", "newly_validated_this_poll", "last_current_binding_check_utc"}})
        changed_state = signature != self.last_signature
        self.last_signature = signature
        return state, changed_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--output", default="results/pilot24-forward51-file-observation-20261012-001")
    args = parser.parse_args()
    need(args.output.startswith("results/pilot24-forward51-file-observation-"), "SEPARATE_OBSERVER_OUTPUT_REQUIRED")
    out = path(args.output)
    need(out != path(LIVE) and not out.is_relative_to(path(LIVE)) and not out.is_relative_to(path(CLAIM)),
        "OBSERVER_OUTPUT_OVERLAP")
    out.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    consecutive_unreadable = 0
    last_heartbeat = 0.0
    while True:
        try:
            state, changed = observer.snapshot()
            consecutive_unreadable = 0
            if changed:
                filename = datetime.now(timezone.utc).strftime("snapshot-%Y%m%dT%H%M%S-%f.json")
                with (out / filename).open("x", encoding="utf-8") as handle:
                    json.dump(state, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                print(json.dumps({"observed_utc": state["observed_utc"], "event": "FILE_STATE_CHANGED",
                    "last_event": state["last_event"], "newly_validated": state["newly_validated_this_poll"],
                    "new_results": len(state["durable_new_results"]), "reserved_unconfirmed": len(state["reserved_unconfirmed"]),
                    "not_reserved": len(state["not_reserved"]), "fixed_288_counts": state["fixed_288_counts"],
                    "terminal": state["terminal"] is not None, "evidence": str(out / filename)}), flush=True)
            elif time.monotonic() - last_heartbeat >= 50:
                print(json.dumps({"observed_utc": utc(), "event": "FILE_OBSERVER_HEARTBEAT",
                    "last_event": state["last_event"], "new_results": len(state["durable_new_results"]),
                    "reserved_unconfirmed": len(state["reserved_unconfirmed"])}), flush=True)
                last_heartbeat = time.monotonic()
            if not args.watch or state["terminal"] is not None:
                return 0
        except (OSError, json.JSONDecodeError) as error:
            consecutive_unreadable += 1
            print(json.dumps({"observed_utc": utc(), "event": "FILE_READ_INCOMPLETE",
                "exception_type": type(error).__name__, "consecutive_observations": consecutive_unreadable}), flush=True)
            if consecutive_unreadable >= 3:
                return 2
        except ValueError as error:
            print(json.dumps({"observed_utc": utc(), "event": "OBSERVER_VALIDATION_STOPPED",
                "reason": str(error), "candidate_executions": 0, "controller_action": "NONE"}), flush=True)
            return 3
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
