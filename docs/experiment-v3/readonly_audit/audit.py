"""File-only takeover audit. No GIR imports, candidate execution or process query.

Writes nothing itself. Print a derived snapshot; it is not a controller receipt,
execution permission, isolation verification or replacement scoring gate.
"""
import collections
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

ROOT = Path(r"D:\GIR")
STATIC = "docs/experiment-v3/pilot24-static-manifest.json"
FREEZE = "ark_eval/pilot24_recovery.v5_1.freeze.json"
ADMIN = "results/pilot24-forward60-interruption-review-20261012-001"
KEY = "f360ed6ed5d0cc1b3166b26874817dd05c5f61e3f1962315b69750dc872c3565"
LIVE = "results/pilot24-recovery-score-forward51-executed-20261012-001"
CLAIM = "results/pilot24-zero-start-claims/" + KEY
LAUNCH = "results/pilot24-zero-start-launches/" + KEY
STARTED = datetime.now(timezone.utc).isoformat()


def path(name):
    p = ROOT / name
    if (Path(name).is_absolute() or PureWindowsPath(name).is_absolute()
            or ".." in Path(name).parts or p.resolve() != p
            or p.name.lower() in {"env.txt", ".env"}
            or p.suffix.lower() in {".key", ".pem"}):
        raise ValueError("Non-public or non-canonical path rejected: " + name)
    p.relative_to(ROOT)
    return p


def sha(name):
    return hashlib.sha256(path(name).read_bytes()).hexdigest()


def read(name):
    return json.loads(path(name).read_bytes())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def mapping_check(mapping):
    drift = []
    for name, expected in mapping.items():
        actual = sha(name)
        if actual != expected:
            drift.append({"path": name, "expected": expected, "actual": actual})
    return {"files": len(mapping), "matched": len(mapping) - len(drift), "drift": drift}


def inventory(base):
    p = path(base)
    return [{"name": x.name, "bytes": x.stat().st_size,
        "modified_utc": datetime.fromtimestamp(x.stat().st_mtime, timezone.utc).isoformat()}
        for x in sorted(p.iterdir()) if x.is_file()]


static = read(STATIC)
freeze = read(FREEZE)
assert sha(STATIC) == "403c429a6475c4459b3bc5757d2503d80b5b9957ba35dd4455e326b1cf610a28"
assert sha(FREEZE) == "fc22a6b6b71ed2ea5d15db24922be12092a84440e617e9a95004baa03cc3d0f9"
assert digest(freeze) == "63d2c9432483f0a9843eda05436a077a838ccd09493556e7ffcf99de68506628"
bundle = static["bundle_root"]
bundle_names = {p.relative_to(path(bundle)).as_posix()
    for p in path(bundle).rglob("*") if p.is_file()}
assert bundle_names == set(static["bundle_files_sha256"])
raw = mapping_check({bundle + "/" + n: h for n, h in static["bundle_files_sha256"].items()})
assert raw["matched"] == raw["files"] == 172
assert len(static["slots"]) == 24
slots = {s["slot_id"]: s for s in static["slots"]}
cases = {task: {x["id"]: x for x in read(entry["path"])}
    for task, entry in static["task_cases"].items()}
for entry in static["task_cases"].values():
    assert sha(entry["path"]) == entry["sha256"]
anchors = mapping_check(freeze["administrative_anchors"])
assert not anchors["drift"]
initial = read("results/pilot24-forward60-execution-review-20261012-001/initial-bindings.json")
immutable = mapping_check(initial["immutable"])
registry = read(ADMIN + "/production-evidence-sha256.json")
assert digest(registry["files"]) == registry["files_digest"] == freeze["prior_evidence_digest"]
production = mapping_check(registry["files"])
current_bindings = mapping_check(freeze["bindings"])
rows = read(ADMIN + "/combined-cases-admin.json")
assert digest(rows) == freeze["preserved_rows_digest"]
by_id = {(r["slot_id"], r["case_id"]): r for r in rows}
assert len(rows) == len(by_id) == 288
assert set(by_id) == {(s["slot_id"], cid) for s in static["slots"] for cid in s["primary_case_ids"]}


def validate(sid, cid, value):
    slot = slots[sid]
    case = cases[slot["task_id"]][cid]
    expected = case["expected_ticks"]
    assert value["expected_ticks"] == expected
    assert value["status"] == ("PASS" if value["actual_ticks"] == expected else "FAIL")
    assert value["oracle_match"] == (value["actual_ticks"] == expected)
    assert value["replay_match"] is True and value["cleanup_verified"] is True
    assert type(value["primary_case_executions"]) is int and value["primary_case_executions"] == 1
    assert value["IR_case_replays"] == int(slot["representation"] == "gameir_text")
    assert value["JS_transcript_replays"] == int(slot["representation"] == "js_callback")
    assert type(value["candidate_executions"]) is int and value["candidate_executions"] >= 0
    if slot["representation"] == "gameir_text":
        assert value["isolated_result"]["source_sha256"] == slot["source_sha256"]
        assert value["isolated_result"]["source_modified"] is False


durable = []
for base in ("results/pilot24-isolated-score-001", "results/pilot24-recovery-score-direct-001",
        "results/pilot24-recovery-score-forward61-executed-001",
        "results/pilot24-recovery-score-forward60-executed-20261012-001"):
    for p in sorted(path(base).glob("result-*.json")):
        sid, cid = p.stem[len("result-"):].rsplit("-", 1)
        value = read(p.relative_to(ROOT).as_posix())
        assert path(base + "/reservation-" + sid + "-" + cid + ".json").is_file()
        validate(sid, cid, value)
        assert all(by_id[(sid, cid)][k] == v for k, v in value.items())
        durable.append((sid, cid))
assert len(durable) == len(set(durable)) == 223
assert set(durable) == {k for k, r in by_id.items() if r["status"] in ("PASS", "FAIL")}
assert {k for k, r in by_id.items() if r["status"] == "HARNESS_ERROR"} == {
    ("C2-r2-js_callback", "N02"), ("C1-r2-js_callback", "R04")}

syntax = []
for slot in static["slots"]:
    assert sha(slot["source_path"]) == slot["source_sha256"]
    if slot["representation"] == "gameir_text":
        try:
            read(slot["source_path"])
        except json.JSONDecodeError as exc:
            syntax.append({"slot_id": slot["slot_id"], "line": exc.lineno,
                "column": exc.colno, "message": exc.msg, "source_sha256": slot["source_sha256"]})
assert [s["slot_id"] for s in syntax] == ["R1-r1-gameir_text"]

live_begin = {n: inventory(n) for n in (LIVE, CLAIM, LAUNCH)}
permit = read("results/pilot24-forward51-permit.json")
intent = read(LAUNCH + "/intent.json")
claim = read(CLAIM + "/claim.json")
assert intent["scope_digest"] == claim["scope_digest"] == digest(freeze)
assert intent["permit_digest"] == claim["permit_digest"] == digest(permit)
assert claim["recovery_key"] == KEY and claim["retry_or_resume_allowed"] is False
assert digest(read(LIVE + "/scope.json")) == digest(freeze)
allowed = {(r["slot_id"], r["case_id"]): r for r in freeze["allowed_rows"]}
new_results, reserved_unknown, pending = [], [], []
merged = copy.deepcopy(by_id)
for (sid, cid), row in allowed.items():
    case = cases[slots[sid]["task_id"]][cid]
    assert row["case_digest"] == digest(case) and row["source_sha256"] == slots[sid]["source_sha256"]
    suffix = sid + "-" + cid + ".json"
    reserved = path(LIVE + "/reservation-" + suffix).is_file() or path(CLAIM + "/case-" + suffix).is_file()
    if path(LIVE + "/result-" + suffix).is_file():
        assert reserved
        value = read(LIVE + "/result-" + suffix)
        validate(sid, cid, value)
        merged[(sid, cid)].update(value)
        new_results.append({"slot_id": sid, "case_id": cid, "status": value["status"]})
    elif reserved:
        reserved_unknown.append([sid, cid])
    else:
        pending.append([sid, cid])
events = [{"file": p.name, "value": read(p.relative_to(ROOT).as_posix())}
    for p in sorted(path(CLAIM).glob("event-*.json"))]
live_end = {n: inventory(n) for n in (LIVE, CLAIM, LAUNCH)}
static_maps = {n: mapping_check(static[n]) for n in ("anchor_sha256", "material_bindings",
    "runtime_bindings", "material_validation_evidence", "sender_artifact_bindings",
    "historical_engineering_evidence", "additional_maintained_source_bindings")}
report = {
    "kind": "DERIVED_FILE_ONLY_TAKEOVER_AUDIT_NOT_CONTROLLER_RECEIPT",
    "started_utc": STARTED, "finished_utc": datetime.now(timezone.utc).isoformat(),
    "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "observed_local_HEAD": "73f05ab479a17781295016e94446fa32dbd1ca5a",
    "observed_local_branch": "ark-js-phase3-20261011",
    "published_snapshot_commit_from_handoff": "97328d4807192414766beacd35ad05b3fc19417f",
    "new_model_calls_by_this_audit": 0, "candidate_executions_by_this_audit": 0,
    "runtime_process_state": "UNKNOWN_ACCESS_DENIED_NO_ALTERNATE_QUERY",
    "raw_172_files": raw, "raw_generation_slots": 24,
    "unique_candidate_hashes": len({s["source_sha256"] for s in static["slots"]}),
    "original_static_maps": static_maps, "current_v5_1_bindings": current_bindings,
    "immutable_1509_files": immutable, "previous_production_127_files": production,
    "administrative_anchors": anchors,
    "validated_prior_durable_rows": len(durable),
    "prior_fixed_288_counts": dict(collections.Counter(r["status"] for r in rows)),
    "derived_current_288_durable_counts": dict(collections.Counter(r["status"] for r in merged.values())),
    "counts_are_not_terminal_scoring_completion": True,
    "syntax_failures": syntax,
    "failure_rows": [{k: r[k] for k in ("slot_id", "case_id", "status", "source_sha256", "actual_ticks", "expected_ticks")}
        for r in rows if r["status"] == "FAIL"],
    "historical_unknown_rows": [{k: r[k] for k in ("slot_id", "case_id", "status", "reason", "phase", "primary_case_executions")}
        for r in rows if r["status"] == "HARNESS_ERROR"],
    "live": {"claim_consumed": True, "controller_identity_from_file_only": claim["controller_identity"],
        "retry_or_resume_allowed": False, "new_durable_results": new_results,
        "new_reserved_unconfirmed": reserved_unknown, "not_reserved": pending,
        "events": events, "summary_present": path(LIVE + "/summary.json").is_file(),
        "top_level_inventory_stable_during_live_read": live_begin == live_end,
        "inventory_begin": live_begin, "inventory_end": live_end},
}
print(json.dumps(report, ensure_ascii=False, indent=2))
