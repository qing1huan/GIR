"""Prepare derived Pilot24 reporting material from a validated observer snapshot.

File reads only; no candidate execution, imports from GIR, process query or API.
Does not turn unfinished rows into successes or declare scoring complete.
"""
import collections
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"D:\GIR")
OBS = ROOT / "results/pilot24-forward51-file-observation-20261012-001"


def read(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def counts(rows):
    c = collections.Counter(r["status"] for r in rows)
    return {k: c[k] for k in ("PASS", "FAIL", "NOTRUN", "HARNESS_ERROR")}


static_path = ROOT / "docs/experiment-v3/pilot24-static-manifest.json"
assert sha(static_path) == "403c429a6475c4459b3bc5757d2503d80b5b9957ba35dd4455e326b1cf610a28"
m = read(static_path)
freeze = read(ROOT / "ark_eval/pilot24_recovery.v5_1.freeze.json")
snapshot_path = sorted(OBS.glob("snapshot-*.json"))[-1]
snapshot = read(snapshot_path)
assert snapshot["scope_digest"] == digest(freeze)
admin_path = ROOT / "results/pilot24-forward60-interruption-review-20261012-001/combined-cases-admin.json"
original = read(admin_path)
assert digest(original) == freeze["preserved_rows_digest"]
rows = {(r["slot_id"], r["case_id"]): copy.deepcopy(r) for r in original}
for record in snapshot["durable_new_results"]:
    p = ROOT / record["path"]
    assert p.resolve().is_relative_to(ROOT) and sha(p) == record["sha256"]
    value = read(p)
    rows[(record["slot_id"], record["case_id"])].update(value)
if snapshot["terminal"] is not None:
    rows = {(r["slot_id"], r["case_id"]): r for r in read(ROOT / "results/pilot24-recovery-score-forward51-executed-20261012-001/combined-cases.json")}
assert len(rows) == 288 and counts(rows.values()) == snapshot["fixed_288_counts"]
slot_report = []
for slot in m["slots"]:
    selected = [r for r in rows.values() if r["slot_id"] == slot["slot_id"]]
    c = counts(selected)
    outcome = ("SCENARIOS_ALL_PASS" if c["PASS"] == 12 else "CANDIDATE_BEHAVIOR_FAILURE" if c["FAIL"]
        else "CANDIDATE_SYNTAX_REJECTION" if all(r["phase"] == "STATIC_REJECTED" for r in selected)
        else "HARNESS_OUTCOME_UNKNOWN" if c["HARNESS_ERROR"] else "SCORING_PENDING")
    slot_report.append({"slot_id": slot["slot_id"], "task_id": slot["task_id"],
        "representation": slot["representation"], "source_sha256": slot["source_sha256"],
        "counts": c, "outcome": outcome, "usage": slot["usage"]})
arms = {}
for rep in ("gameir_text", "js_callback"):
    selected = [s for s in slot_report if s["representation"] == rep]
    usage = collections.Counter()
    for slot in selected:
        usage.update(slot["usage"])
    arms[rep] = {"generation_slots": len(selected), "unique_source_hashes": len({s["source_sha256"] for s in selected}),
        "case_counts": counts(r for r in rows.values() if r["representation"] == rep),
        "slot_outcomes": dict(collections.Counter(s["outcome"] for s in selected)), "known_collection_usage": dict(usage)}
print(json.dumps({
    "kind": "DERIVED_REPORTING_MATERIAL", "prepared_utc": datetime.now(timezone.utc).isoformat(),
    "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "validated_snapshot": {"path": str(snapshot_path), "sha256": sha(snapshot_path)},
    "terminal_scoring_receipt_validated": snapshot["terminal"] is not None,
    "score": counts(rows.values()), "arms": arms, "slots": slot_report,
    "task_cluster_count": 6, "generation_slot_count": 24, "scenario_count": 288,
    "scenario_count_is_not_independent_sample_count": True,
    "study_kind": "TWO_REPRESENTATION_NO_FEEDBACK_EXPLORATION_NOT_ABC",
    "known_collection_tokens": sum(s["usage"]["total_tokens"] for s in slot_report),
    "currency_cost": None, "unreported_thinking_or_cache_usage": None,
    "fully_passing_slot_means_only_fixed_scenarios_not_full_contract": True,
    "observer_or_report_candidate_executions": 0, "new_model_calls": 0
}, ensure_ascii=False, indent=2))
