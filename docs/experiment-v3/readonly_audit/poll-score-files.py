"""Small file-only progress check; does not execute or restart scoring."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"D:\GIR\results")
RUN = ROOT / "pilot24-recovery-score-forward51-executed-20261012-001"
CLAIM = ROOT / "pilot24-zero-start-claims/f360ed6ed5d0cc1b3166b26874817dd05c5f61e3f1962315b69750dc872c3565"
events = sorted(CLAIM.glob("event-*.json"))
directories = sorted((p for p in RUN.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
latest_directory = directories[-1] if directories else None
print(json.dumps({
    "observed_utc": datetime.now(timezone.utc).isoformat(),
    "last_event": json.loads(events[-1].read_bytes()) if events else None,
    "durable_result_files": len(list(RUN.glob("result-*.json"))),
    "primary_reservations": len(list(CLAIM.glob("case-*.json"))),
    "terminal_receipt_present": (RUN / "summary.json").is_file(),
    "latest_work_directory": latest_directory.name if latest_directory else None,
    "latest_directory_modified_utc": datetime.fromtimestamp(latest_directory.stat().st_mtime, timezone.utc).isoformat() if latest_directory else None,
    "live_process_state": "NOT_QUERIED_ACCESS_DENIED",
    "candidate_executions_by_this_poll": 0
}, ensure_ascii=False))
