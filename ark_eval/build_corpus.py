"""Deterministically author fixed expectations from the task contract, not Runtime.

Only used when intentionally revising the DEVELOPMENT protocol. Never rebuild the
suite from reference/model output, and never rebuild it during a scored run.
"""
import copy
import json
from pathlib import Path

from .tasks import DOOR, PLAYER, TASKS


def corpus():
    cases = []
    # Each tuple fixes input and expected state independently of the interpreter.
    # keys, open, valid_player, valid_door, number of repeated future-tick opens
    # Last two columns: same-tick input multiplicity, missing keys cell.
    scenarios = [(0, False, True, True, 2, 1, False), (1, False, True, True, 2, 1, False),
                 (2, False, True, True, 3, 1, False), (10, False, True, True, 2, 1, False),
                 (0, True, True, True, 2, 1, False), (10, True, True, True, 2, 1, False),
                 (2, False, False, True, 2, 1, False), (2, False, True, False, 2, 1, False),
                 (2, False, True, True, 2, 2, False), (10, True, False, True, 2, 1, False),
                 (0, True, True, True, 2, 1, True), (0, False, True, True, 1, 1, True),
                 (2, False, False, False, 2, 1, False)]
    for tid, spec in TASKS.items():
        for index, (keys, opened, vp, vd, repeats, multiplicity, missing_keys) in enumerate(scenarios):
            p, d = copy.deepcopy(PLAYER), copy.deepcopy(DOOR)
            initial = [{"id": p, "values": {} if missing_keys else {"keys": keys}},
                       {"id": d, "values": {"open": opened}}]
            target_p = p if vp else ["world", -1, "genesis", 99]
            target_d = d if vd else ["world", -1, "genesis", 98]
            steps = [{"inputs": [{"name": "Open", "payload": {"player": target_p, "door": target_d}}] * multiplicity}
                     for _ in range(repeats)]
            expected_keys, expected_open, events = keys, opened, []
            if vp and vd and not opened:
                if keys >= spec["cost"]:
                    expected_keys -= spec["cost"]
                    expected_open = True
                    events.append({"name": "Opened", "payload": {"door": d}})
                else:
                    events = [{"name": "MissingKey", "payload": {"player": p}} for _ in range(repeats * multiplicity)]
            failed = missing_keys and not opened and vp and vd
            if failed: events = []
            expected = {"entities": [{"id": p, "values": {} if missing_keys else {"keys": expected_keys}},
                                     {"id": d, "values": {"open": expected_open}}],
                        "statuses": ["ABORTED" if failed else "COMMITTED"] * repeats, "events": events,
                        "commands": [], "errors": ["InvalidEntity" if failed else None] * repeats}
            for seed in [1, 123]:
                cases.append({"id": f"{tid}-{index}-seed{seed}", "task_id": tid, "seed": seed,
                              "initial": initial, "steps": steps, "expected": expected})
    return {"schema": "paired-door-development-oracle@1", "holdout": False,
            "test_origin": "contract-authored expectations; no runtime or reference outputs",
            "cases": cases}


if __name__ == "__main__":
    document = corpus()
    lines = []
    for key, value in document.items():
        if key == "cases":
            encoded = "[\n" + ",\n".join("    " + json.dumps(c, ensure_ascii=False, separators=(",", ":"))
                                          for c in value) + "\n  ]"
        else:
            encoded = json.dumps(value, ensure_ascii=False)
        lines.append("  " + json.dumps(key) + ": " + encoded)
    Path(__file__).with_name("final_tests.json").write_text(
        "{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")
