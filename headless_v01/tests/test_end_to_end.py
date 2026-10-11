import tempfile
import unittest

from headless_v01.__main__ import main
from headless_v01.compiler import compile_program, make_plan
from headless_v01.examples import cases
from headless_v01.runtime import Runtime, replay


def assert_case(test, fixture):
    core = compile_program(fixture["program"])
    options = {"seed": fixture.get("seed", 1), "budgets": fixture.get("budgets"), "capabilities": fixture.get("capabilities")}
    runtime = Runtime(make_plan(core), fixture["initial"], **options); before = runtime.snapshot()
    results = [runtime.step(**step) for step in fixture["steps"]]
    expected = fixture["expected"]; snapshot = runtime.snapshot()
    test.assertEqual(results[-1]["status"], expected["status"])
    if expected.get("rollback"): test.assertEqual(snapshot, before)
    if "error" in expected: test.assertEqual(results[-1]["error"]["code"], expected["error"])
    for check in expected.get("values", []):
        entity = next(e for e in snapshot["entities"] if e["id"] == check["id"])
        test.assertEqual(entity["values"][check["field"]], check["value"])
    for name, count in expected.get("events", {}).items():
        test.assertEqual(sum(e["name"] == name for result in results for e in result["events"]), count)
    if "entity_count" in expected: test.assertEqual(len(snapshot["entities"]), expected["entity_count"])
    if "microsteps" in expected: test.assertEqual(results[-1]["budget_attempted"]["microsteps"], expected["microsteps"])
    if "commands" in expected: test.assertEqual(sum(len(r["commands"]) for r in results), expected["commands"])
    if "reachable_rooms" in expected:
        rooms = {tuple(e["id"]) for e in snapshot["entities"] if "position" in e["values"]}
        edges = [(tuple(e["values"]["a"]), tuple(e["values"]["b"])) for e in snapshot["entities"] if "a" in e["values"]]
        seen = {min(rooms)}
        for _ in range(len(rooms)):
            for a, b in edges:
                if a in seen: seen.add(b)
                if b in seen: seen.add(a)
        test.assertEqual(len(seen), expected["reachable_rooms"])
        test.assertEqual(sorted(e["values"]["position"] for e in snapshot["entities"] if "position" in e["values"]), [[0., 0.], [4., 0.], [4., 4.]])
    test.assertEqual(replay(core, fixture["initial"], runtime.history, **options).snapshot(), snapshot)
    # Compiled Core and the unoptimized Plan have one interpreter, so this is dispatch
    # consistency plus independent expected state assertions, not independent engine proof.
    direct = Runtime(core, fixture["initial"], **options)
    test.assertEqual([direct.step(**step) for step in fixture["steps"]], results)
    return runtime, results


class EndToEndTests(unittest.TestCase):
    pass


def bind(fixture):
    def test(self): assert_case(self, fixture)
    return test


for id_, fixture in cases().items():
    setattr(EndToEndTests, "test_"+id_.replace("-", "_"), bind(fixture))


if __name__ == "__main__": unittest.main()
