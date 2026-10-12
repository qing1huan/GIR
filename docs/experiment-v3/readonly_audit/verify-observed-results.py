"""Check persistent result fingerprints across independent file observations."""
import hashlib
import json
from pathlib import Path, PureWindowsPath

ROOT = Path(r"D:\GIR")
OBS = ROOT / "results/pilot24-forward51-file-observation-20261012-001"
SCOPE = "63d2c9432483f0a9843eda05436a077a838ccd09493556e7ffcf99de68506628"
first = {}
snapshots = sorted(OBS.glob("snapshot-*.json"))
for snapshot in snapshots:
    value = json.loads(snapshot.read_bytes())
    assert value["scope_digest"] == SCOPE
    for record in value["durable_new_results"]:
        key = record["slot_id"], record["case_id"]
        identity = record["path"], record["sha256"], record["status"]
        assert first.setdefault(key, identity) == identity, "RECORDED_RESULT_FINGERPRINT_CHANGED"
for key, (relative, expected, status) in first.items():
    assert not PureWindowsPath(relative).is_absolute() and ".." not in Path(relative).parts
    p = ROOT / relative
    assert p.resolve() == p and p.is_relative_to(ROOT / "results/pilot24-recovery-score-forward51-executed-20261012-001")
    assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, "DURABLE_RESULT_BYTES_CHANGED"
print(json.dumps({"kind": "READ_ONLY_OBSERVED_RESULT_CONTINUITY", "status": "PASS",
    "snapshots_checked": len(snapshots), "distinct_durable_rows": len(first),
    "candidate_executions": 0, "new_model_calls": 0}))
