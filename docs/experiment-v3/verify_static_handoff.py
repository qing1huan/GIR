"""Read-only, standard-library verification of one exact static handoff.

No candidate imports, compilation, evaluation, subprocesses, network, environment
inspection, credential access, or writes. A PASS is not execution authorization.
"""
import collections
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "docs/experiment-v3/pilot24-static-manifest.json"
MANIFEST_SHA = "403c429a6475c4459b3bc5757d2503d80b5b9957ba35dd4455e326b1cf610a28"
BUNDLE = "results/pilot24-user-local-001"
SCOPE = "44f5882a92ecabb7f22f7d6296d049c4332fab358ad08646548159a6b0bc45c5"


def require(ok, label):
    if not ok:
        raise ValueError(label)


def content(relative):
    p = ROOT / relative
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
            "RELATIVE_PATH_REQUIRED")
    require(p.resolve() == p and p.is_file(), "REGULAR_IN_REPOSITORY_FILE_REQUIRED")
    require(p.name.lower() not in {"env.txt", ".env"} and
            p.suffix.lower() not in {".key", ".pem"}, "SECRET_PATH_FORBIDDEN")
    return p.read_bytes()


def sha(relative):
    return hashlib.sha256(content(relative)).hexdigest()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("NONFINITE_JSON_CONSTANT")


def decode(raw):
    return json.loads(raw, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def read(relative):
    return decode(content(relative).decode("utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def verify():
    checks = []
    require(sha(MANIFEST) == MANIFEST_SHA, "STATIC_MANIFEST_DRIFT")
    m = read(MANIFEST)
    require(m["permissions"] == {"new_model_requests": 0, "generated_candidate_executions": 0,
            "execution_approval_granted": False, "preserve_raw_candidates": True,
            "reuse_consumed_send_claim": False, "push": False}, "PERMISSION_DRIFT")
    require(m["status"] == "STATIC_PREPARATION_NOT_EXECUTION_AUTHORIZATION", "STATUS_DRIFT")
    checks.append("manifest_and_no_execution_boundary")

    maps = ("anchor_sha256", "material_bindings", "runtime_bindings",
            "material_validation_evidence", "sender_artifact_bindings",
            "historical_engineering_evidence", "additional_maintained_source_bindings")
    for name in maps:
        for path, expected in m[name].items():
            require(sha(path) == expected, "BOUND_FILE_DRIFT: " + path)
    f = read("ark_eval/pilot24/freeze.json")
    for target, original in (("material_bindings", "bindings"), ("runtime_bindings", "runtime_bindings"),
                             ("material_validation_evidence", "validation_evidence_sha256")):
        require(m[target] == f[original], "FREEZE_MAP_DRIFT")
    require((len(m["material_bindings"]), len(m["runtime_bindings"]),
             len(m["material_validation_evidence"])) == (106, 74, 13), "FREEZE_COUNTS")
    checks.append("frozen_material_runtime_sender_and_historical_evidence")

    names = {p.relative_to(ROOT / BUNDLE).as_posix() for p in (ROOT / BUNDLE).rglob("*") if p.is_file()}
    require(names == set(m["bundle_files_sha256"]) and len(names) == 172, "BUNDLE_FILE_SET_DRIFT")
    actual = {p: sha(BUNDLE + "/" + p) for p in sorted(names)}
    require(actual == m["bundle_files_sha256"], "BUNDLE_CONTENT_DRIFT")
    require(digest(actual) == m["bundle_posix_manifest_digest"], "POSIX_DIGEST_DRIFT")
    require(digest({p.replace("/", "\\"): value for p, value in actual.items()}) ==
            m["prior_windows_key_manifest_digest"], "WINDOWS_DIGEST_DRIFT")
    checks.append("all_172_collection_files_and_both_path_conventions")

    summary = read(BUNDLE + "/summary.json")
    start = read("results/pilot24-send-claims/" + SCOPE + "/start.json")
    complete = read("results/pilot24-send-claims/" + SCOPE + "/completion.json")
    require(start["resume_allowed"] is False and start["reservation_retained"] is True,
            "CONSUMED_CLAIM_STATE")
    require(complete["summary_sha256"] == sha(BUNDLE + "/summary.json") and
            complete["request_attempts_reserved"] == 24 and
            complete["state"] == "FIXED_SCHEDULE_COLLECTED_UNREVIEWED", "COMPLETION_DRIFT")
    checks.append("consumed_collection_claim_and_terminal_receipt")

    groups = collections.defaultdict(list)
    usage = collections.Counter()
    json_valid = 0
    syntax = []
    require(len(summary["slots"]) == len(m["slots"]) == 24, "SLOT_COUNT")
    for index, (a, slot) in enumerate(zip(summary["slots"], m["slots"]), 1):
        sid = a["slot"]["slot_id"]
        require(slot["index"] == a["slot"]["index"] == index and slot["slot_id"] == sid, "SLOT_ORDER")
        source_path = BUNDLE + "/" + sid + "/candidate.txt"
        require(slot["source_path"] == source_path, "SOURCE_PATH_DRIFT")
        raw = content(source_path)
        h = hashlib.sha256(raw).hexdigest()
        require(h == slot["source_sha256"] == a["candidate_sha256"] and
                len(raw) == slot["source_bytes"] == a["candidate_bytes"], "SOURCE_DRIFT")
        require(read(BUNDLE + "/" + sid + "/audit.json") == a, "AUDIT_DRIFT")
        receipt = read(BUNDLE + "/" + sid + "/response-receipt.json")["audit"]
        require({k: v for k, v in receipt.items() if k != "candidate_file"} ==
                {k: v for k, v in a.items() if k not in {"candidate_file", "candidate_sha256"}},
                "RECEIPT_AUDIT_MISMATCH")
        cp = read(BUNDLE + f"/checkpoint-{index:02}.json")
        require(cp["slots"][:index - 1] == summary["slots"][:index - 1] and
                cp["slots"][index - 1]["generation_status"] == "OUTCOME_UNKNOWN" and
                cp["request_attempts_reserved"] == index, "CHECKPOINT_PREFIX_DRIFT")
        require(a["http_status"] == 200 and a["finish_reason"] == "stop" and
                a["request_attempts"] == a["model_calls_lower"] == a["model_calls_upper"] == 1,
                "REQUEST_ACCOUNTING")
        require(a["execute_source"] is False and a["generated_source_executions"] == 0 and
                a["case_statuses"] == ["NOTRUN"] * 12 and a["contract_status"] == "NOT_CHECKED",
                "UNEXECUTED_SOURCE_STATUS")
        require(slot["usage"] == a["usage"], "USAGE_DRIFT")
        usage.update(a["usage"])
        groups[h].append(sid)
        if slot["representation"] == "gameir_text":
            try:
                decode(raw.decode("utf-8"))
                json_valid += 1
                require(slot["static_json_status"] == "VALID_JSON_ONLY", "JSON_STATUS")
            except json.JSONDecodeError as exc:
                syntax.append(sid)
                require(slot["static_json_status"] == "FAIL_SYNTAX" and
                        slot["syntax_error"] == {"line": exc.lineno, "column": exc.colno,
                        "offset": exc.pos, "message": exc.msg}, "SYNTAX_FINDING_DRIFT")
        task = slot["task_id"]
        cases = read(m["task_cases"][task]["path"])
        require(slot["primary_case_ids"] == [c["id"] for c in cases] and len(cases) == 12,
                "CASE_MAPPING_DRIFT")
        require(all(c["seed"] == 1 and c["task_id"] == task for c in cases), "CASE_SCOPE")
    require(dict(groups) == m["unique_source_slots"] and len(groups) == 14, "SOURCE_GROUPS")
    checks.append("24_ordered_slots_audits_receipts_and_checkpoint_prefixes")
    require(dict(usage) == summary["known_usage"] == m["collection"]["known_usage"] ==
            {"prompt_tokens": 52934, "completion_tokens": 19091, "total_tokens": 72025}, "BATCH_USAGE")
    checks.append("24_requests_and_72025_actual_tokens")
    require(json_valid == 11 and syntax == ["R1-r1-gameir_text"], "STATIC_JSON_FINDINGS")
    checks.append("11_valid_IR_JSON_and_1_preserved_syntax_failure")
    require(summary["case_counts"] == {"PASS": 0, "FAIL": 0, "NOTRUN": 288} and
            summary["generated_source_executions"] == 0 and
            summary["candidate_execution"] == "BLOCKED", "PRIMARY_STATUS")
    require(sum(t["count"] for t in m["task_cases"].values()) == 72 and
            sum(len(s["primary_case_ids"]) for s in m["slots"]) == 288, "CASE_DENOMINATOR")
    checks.append("72_fixed_cases_288_NOTRUN_and_zero_candidate_execution")
    return {"kind": "STATIC_HANDOFF_VERIFICATION_ONLY", "python": sys.version.split()[0],
            "checks": {c: "PASS" for c in checks}, "counts": {"PASS": len(checks), "FAIL": 0, "NOTRUN": 0},
            "generation_slots": 24, "unique_source_hashes": 14, "primary_case_counts": summary["case_counts"],
            "new_model_calls": 0, "candidate_executions": 0, "execution_authorization": False,
            "manifest_sha256": MANIFEST_SHA, "bundle_posix_digest": digest(actual)}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "FAIL_STATIC_VERIFICATION", "reason": str(exc),
                          "new_model_calls": 0, "candidate_executions": 0}))
        raise SystemExit(1)
