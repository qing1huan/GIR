import copy
import json
import math
import unittest

from headless_v01.compiler import compile_program, lower_semantic, make_plan
from headless_v01.runtime import Runtime, replay
from headless_v01.types import Fault, validate_value, validate_type, canonical_json
from .fixtures import *


class ContractTests(unittest.TestCase):
    def runtime(self, spec=None, entities=None, **kw):
        return Runtime(make_plan(compile_program(spec or program())), entities or initial(), **kw)

    def go(self, runtime, **kw):
        return runtime.step(inputs=[{"name": "Go", "payload": {}}], **kw)

    def value(self, runtime, field_="x", entity=P):
        return next(e["values"][field_] for e in runtime.snapshot()["entities"] if e["id"] == entity)

    def fault_value(self, type_, value, code):
        with self.assertRaises(Fault) as caught:
            validate_value(type_, value)
        self.assertEqual(caught.exception.code, code)

    def test_A01_types_numeric_and_depth(self):
        for value in [-2**53 + 1, 0, 2**53 - 1]:
            self.assertEqual(validate_value("int", value), value)
        for value in [-2**53, 2**53, True, 1.0]:
            self.fault_value("int", value, "TypeError" if isinstance(value, (bool, float)) else "NumericOverflow")
        for value in [float("inf"), float("nan")]:
            self.fault_value("number", value, "NumericOverflow")
        self.fault_value("bool", 1, "TypeError")
        self.fault_value("string", "\ud800", "TypeError")
        self.fault_value({"record": {"x": "int"}}, {}, "TypeError")
        self.fault_value({"record": {"x": "int"}}, {"x": 0, "extra": 1}, "TypeError")
        type_ = {"option": {"option": "int"}}
        self.assertEqual(validate_value(type_, {"some": {"none": True}}), {"some": {"none": True}})
        self.fault_value(type_, None, "TypeError")
        self.assertEqual(validate_value("number", -0.0), 0.0)
        self.assertEqual(math.copysign(1, validate_value("number", -0.0)), 1)
        for depth in [16, 17]:
            type_ = "int"
            for _ in range(depth - 1): type_ = {"list": type_}
            if depth == 16: validate_type(type_)
            else:
                with self.assertRaises(Fault) as caught: validate_type(type_)
                self.assertEqual(caught.exception.code, "BudgetExceeded")
        self.fault_value({"list": "int"}, [0] * 1025, "BudgetExceeded")
        self.fault_value("string", "a" * 65537, "BudgetExceeded")
        # Resource value depth is measured independently of its schema depth.
        validate_value({"list": "int"}, [0], limits={"value_depth": 2})
        with self.assertRaises(Fault): validate_value({"list": "int"}, [0], limits={"value_depth": 1})

    def test_A01_unknown_ast_and_static_types(self):
        for body in [[write("missing", lit(1))], [write("x", lit(True, "bool"))],
                     [{"op": "while", "body": []}], [write("x", {"op": "native", "source": "anything"})]]:
            with self.assertRaises(Fault): compile_program(program(handlers=[handler("a", "Go", body)]))
        spec = program(handlers=[handler("a", "Go", [write("x", lit(1))])])
        with self.assertRaises(Fault) as caught: compile_program(spec, limits={"ast_nodes": 2})
        self.assertEqual(caught.exception.code, "BudgetExceeded")

    def test_A02_special_keys_copy_and_order(self):
        keys = ["__proto__", "constructor", "$f64", "", "a.b", "😀", "é"]
        value = {k: {"items": [1]} for k in keys}
        type_ = {"map": {"record": {"items": {"list": "int"}}}}
        checked = validate_value(type_, value)
        value["__proto__"]["items"].append(2)
        self.assertEqual(checked["__proto__"]["items"], [1])
        self.assertEqual(canonical_json(checked), canonical_json(dict(reversed(list(checked.items())))))
        spec = program(fields={"bag": field(type_), "keys": field({"list": "string"})}, handlers=[handler("a", "Go", [
            {"op": "let", "name": "copy", "value": read("bag")},
            write("bag", var("copy")),
            {"op": "let", "name": "copy", "value": expr("map_put", value=var("copy"), key=lit("new", "string"), item=lit({"items": [8]}, {"record": {"items": {"list": "int"}}}))},
            write("keys", expr("map_keys", value=read("bag"))),
        ])])
        r = self.runtime(spec, initial(bag=checked, keys=[]))
        self.assertEqual(self.go(r)["status"], "COMMITTED")
        self.assertEqual(self.value(r, "keys"), sorted(keys))
        self.assertNotIn("new", self.value(r, "bag"))
        snapshot = r.snapshot(); snapshot["entities"][0]["values"]["bag"]["$f64"]["items"].append(7)
        self.assertEqual(self.value(r, "bag")["$f64"]["items"], [1])

    def test_A03_authority_and_explicit_logs(self):
        fields = {"x": field(), "pose": field("vec2", authority="Backend")}
        with self.assertRaises(Fault) as caught:
            compile_program(program(fields, [handler("bad", "Go", [write("pose", lit([0., 0.], "vec2"))])]))
        self.assertEqual(caught.exception.code, "AuthorityViolation")
        r = self.runtime(program(fields, [handler("ok", "Go", [
            {"op": "let", "name": "pose", "value": expr("observe", entity=lit(P, "EntityID"), field="pose")},
            write("x", binary("add", read("x"), lit(1))),
        ])]))
        result = self.go(r, observations=[{"id": P, "values": {"pose": [3., 4.]}}])
        self.assertEqual(result["status"], "COMMITTED")
        self.assertTrue(any(x["op"] == "observe" for x in result["reads"]))
        self.assertTrue(any(x["op"] == "read" for x in result["reads"]))
        self.assertTrue(all(x["source"]["originChain"] for x in result["effects"]))
        self.assertNotIn("pose", r.snapshot()["entities"][0]["values"])

    def test_A04_atomicity_rng_spawn_future_commands_retry(self):
        services = {"Visual@1": {"kind": "command", "input": {"record": {"target": "EntityID"}},
                                 "determinism": "BackendSemantic", "cost": 1, "delivery": "idempotent", "resource_field": "target"}}
        spec = program(handlers=[handler("reward", "Reward", [write("x", lit(3)),
                    {"op": "draw", "name": "n", "bound": lit(10)},
                    {"op": "spawn", "name": "born", "values": {"x": lit(9)}},
                    {"op": "schedule", "delay": lit(1), "event": "Later", "payload": lit({}, {"record": {}})},
                    {"op": "command", "service": "Visual@1", "args": lit({"target": P}, {"record": {"target": "EntityID"}})}]),
                handler("a", "Go", [write("x", lit(1))]), handler("b", "Go", [write("x", lit(2))])],
                events={"Reward": {"record": {}}, "Later": {"record": {}}}, services=services)
        r = self.runtime(spec, capabilities=["Visual@1"]); before = r.snapshot()
        inputs = [{"name": "Reward", "payload": {}}, {"name": "Go", "payload": {}}]
        failed = r.step(inputs=inputs)
        self.assertEqual(failed["error"]["code"], "WriteConflict")
        self.assertEqual(r.snapshot(), before); self.assertEqual(failed["commands"], [])
        self.assertTrue(failed["effects"]); self.assertEqual(failed["status"], "ABORTED")
        again = r.step(inputs=inputs, control="retry")
        self.assertEqual(again["error"]["code"], "WriteConflict")
        self.assertEqual([e["value"] for e in failed["effects"] if e["op"] == "spawn"],
                         [e["value"] for e in again["effects"] if e["op"] == "spawn"])
        self.assertEqual(r.snapshot(), before)
        blocked = r.step(inputs=[])
        self.assertEqual(blocked["error"]["code"], "RuntimeHalted")

    def test_A05_own_reads_other_job_and_invalid(self):
        spec = program(fields={"x": field(), "seen": field()}, handlers=[
            handler("a", "Go", [write("x", lit(7)), {"op": "assert", "condition": binary("eq", read("x"), lit(7))}]),
            handler("b", "Go", [write("seen", read("x"))])])
        r = self.runtime(spec, initial(x=1, seen=0)); self.go(r)
        self.assertEqual(self.value(r), 7); self.assertEqual(self.value(r, "seen"), 1)
        for id_ in [E, ["world", -1, "genesis", 99]]:
            r = self.runtime(program(handlers=[handler("a", "Go", [write("x", read("x", lit(id_, "EntityID")))])]))
            self.assertEqual(self.go(r)["error"]["code"], "InvalidEntity")

    def test_A05_snapshot_delete_spawn_and_alias(self):
        q = expr("query", fields=["x"])
        body = [{"op": "let", "name": "ids", "value": q},
                {"op": "foreach", "name": "id", "items": var("ids"), "max": 2, "body": [
                    {"op": "if", "condition": binary("eq", var("id"), lit(P, "EntityID")), "then": [
                        {"op": "despawn", "entity": lit(E, "EntityID")},
                        {"op": "spawn", "name": "c", "values": {"x": lit(3)}},
                        {"op": "assert", "condition": binary("eq", read("x", var("c")), lit(3))},
                    ], "else": [{"op": "assert", "condition": expr("not", value=expr("is_some", value=expr("resolve", entity=var("id"))))}]}
                ]}, {"op": "assert", "condition": binary("eq", q, var("ids"))}, emit("Check")]
        spec = program(handlers=[handler("a", "Go", body), handler("check", "Check", [
            {"op": "assert", "condition": binary("eq", expr("count", value=q), lit(2))}])], events={"Check": {"record": {}}})
        entities = [{"id": P, "values": {"x": 1}}, {"id": E, "values": {"x": 2}}]
        r = self.runtime(spec, entities); result = self.go(r)
        self.assertEqual(result["status"], "COMMITTED")
        self.assertNotIn(E, [e["id"] for e in r.snapshot()["entities"]])
        self.assertEqual([e["values"]["x"] for e in r.snapshot()["entities"]], [1, 3])
        spec["handlers"].append(handler("other", "Go", [write("x", lit(99), lit(E, "EntityID"))]))
        r = self.runtime(spec, entities); before = r.snapshot()
        self.assertEqual(self.go(r)["error"]["code"], "WriteConflict"); self.assertEqual(r.snapshot(), before)

    def test_A06_exclusive_add_priority_tie_invalid_loser(self):
        for op in [write, add]:
            r = self.runtime(program(handlers=[handler("a", "Go", [op("x", lit(1))]), handler("b", "Go", [op("x", lit(2))])]))
            self.assertEqual(self.go(r)["error"]["code"], "WriteConflict")
        spec = program(fields={"x": field(policy="priority_set"), "other": field()}, handlers=[
            handler("a", "Go", [write("x", lit(1)), write("other", lit(9))], 0),
            handler("b", "Go", [write("x", lit(2))], 1)])
        r = self.runtime(spec, initial(x=0, other=0)); self.go(r)
        self.assertEqual(self.value(r), 2); self.assertEqual(self.value(r, "other"), 9)
        spec["handlers"][1]["priority"] = 0
        self.assertEqual(self.go(self.runtime(spec, initial(x=0, other=0)))["error"]["code"], "WriteConflict")
        spec["fields"]["x"]["range"] = [0, 2]; spec["handlers"][1]["priority"] = 1
        spec["handlers"][0]["body"][0]["value"] = lit(3)
        self.assertEqual(self.go(self.runtime(spec, initial(x=0, other=0)))["error"]["code"], "InvariantViolation")

    def test_A06_float_fold_and_numeric_overflow(self):
        spec = program(fields={"x": field("number", policy="ordered_add")}, handlers=[
            handler(str(i), "Go", [add("x", lit(v, "number"))], i) for i, v in enumerate([1e16, -1e16, 1.])])
        r = self.runtime(spec, initial(x=0.)); self.go(r); self.assertEqual(self.value(r), 1.)
        spec["handlers"][1]["body"][0]["value"], spec["handlers"][2]["body"][0]["value"] = lit(1., "number"), lit(-1e16, "number")
        r = self.runtime(spec, initial(x=0.)); self.go(r); self.assertEqual(self.value(r), 0.)
        r = self.runtime(program(handlers=[handler("a", "Go", [add("x", lit(1))])]), initial(x=2**53-1))
        self.assertEqual(self.go(r)["error"]["code"], "NumericOverflow")
        mixed = program(fields={"x": field(policy="ordered_add")}, handlers=[handler("a", "Go", [write("x", lit(1))])])
        with self.assertRaises(Fault): compile_program(mixed)

    def test_A06_clamp_once_health_library(self):
        spec = health_semantic(); spec["base"]["handlers"] = [
            handler("a", "Damage", [add("hp", lit(-2), lit(E, "EntityID"))]),
            handler("b", "Damage", [add("hp", lit(1), lit(E, "EntityID"))])]
        r = self.runtime(lower_semantic(spec), [{"id": E, "values": {"hp": 1, "max": 1, "life": "Alive"}}])
        result = r.step(inputs=[{"name": "Damage", "payload": {}}])
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(self.value(r, "hp", E), 0); self.assertEqual(self.value(r, "life", E), "Dead")
        self.assertEqual(sum(e["name"] == "Died" for e in result["events"]), 1)
        result = r.step(inputs=[{"name": "Damage", "payload": {}}])
        self.assertEqual(sum(e["name"] == "Died" for e in result["events"]), 0)

    def test_A07_root_order_bfs_future_and_event_ids(self):
        spec = program(handlers=[handler("a", "Go", [emit("Child"),
                       {"op": "schedule", "delay": lit(1), "event": "Due", "payload": lit({}, {"record": {}})}, write("x", lit(1))])],
                       fields={"x": field(notify=True)}, events={n: {"record": {}} for n in ["Reply", "Obs", "Child", "Due"]})
        r = self.runtime(spec)
        result = r.step(inputs=[{"name": "Go", "payload": {}}], replies=[{"name": "Reply", "payload": {}}], observation_events=[{"name": "Obs", "payload": {}}])
        self.assertEqual([e["name"] for e in result["events"]], ["Reply", "Go", "Obs", "Tick", "Child", "FieldChanged:x"])
        due_id = r.snapshot()["future"][0]["event"]["id"]
        result = r.step(inputs=[{"name": "Go", "payload": {}}], replies=[{"name": "Reply", "payload": {}}], observation_events=[{"name": "Obs", "payload": {}}])
        self.assertEqual([e["name"] for e in result["events"]][:5], ["Reply", "Go", "Due", "Obs", "Tick"])
        self.assertEqual(result["events"][2]["id"], due_id)

    def test_A07_event_cycle_budget_and_no_truncation(self):
        spec = program(handlers=[handler("a", "Go", [add("x", lit(1)), emit("Go")])])
        r = self.runtime(spec, budgets={"microsteps": 4}); before = r.snapshot(); result = self.go(r)
        self.assertEqual(result["error"]["code"], "BudgetExceeded"); self.assertEqual(len(result["events"]), 4)
        self.assertEqual(r.snapshot(), before)
        for budget, value, body in [("future", 2, [{"op": "schedule", "delay": lit(1), "event": "Go", "payload": lit({}, {"record": {}})}] * 3),
                                    ("query", 1, [{"op": "let", "name": "q", "value": expr("query", fields=["x"])}]),
                                    ("newborn", 1, [{"op": "spawn", "name": "n", "values": {"x": lit(1)}}] * 2),
                                    ("events", 1, [emit("Go"), emit("Go")])]:
            r = self.runtime(program(handlers=[handler("a", "Go", body)]), [{"id": P, "values": {"x": 0}}, {"id": E, "values": {"x": 0}}], budgets={budget: value})
            before = r.snapshot(); self.assertEqual(self.go(r)["error"]["code"], "BudgetExceeded"); self.assertEqual(r.snapshot(), before)
        # Exact microstep limit succeeds, +1 fails without silently deferring work.
        r = self.runtime(program(), budgets={"microsteps": 2}); self.assertEqual(self.go(r)["status"], "COMMITTED")
        r = self.runtime(program(), budgets={"microsteps": 1}); self.assertEqual(self.go(r)["status"], "ABORTED")

    def test_A07_finite_loops(self):
        spec = program(handlers=[handler("a", "Go", [{"op": "repeat", "name": "i", "count": lit(3), "max": 3, "body": [add("x", lit(1))]}])])
        r = self.runtime(spec); self.go(r); self.assertEqual(self.value(r), 3)
        spec["handlers"][0]["body"][0]["count"] = lit(4)
        r = self.runtime(spec); self.assertEqual(self.go(r)["error"]["code"], "BudgetExceeded")

    def test_A08_door_lowering_core_plan_and_source(self):
        core = lower_semantic(door_semantic()); plan = make_plan(compile_program(core))
        entities = [{"id": P, "values": {"key": 1}}, {"id": E, "values": {"open": False}}]
        inputs = [{"name": "Open", "payload": {"player": P, "door": E}}] * 2
        a = Runtime(compile_program(core), entities); b = Runtime(plan, entities)
        ar, br = a.step(inputs=inputs), b.step(inputs=inputs)
        self.assertEqual(ar, br); self.assertEqual(a.snapshot(), b.snapshot())
        self.assertEqual(self.value(b, "key"), 0); self.assertTrue(self.value(b, "open", E))
        self.assertEqual(sum(e["name"] == "Opened" for e in br["events"]), 1)
        self.assertTrue(any("Door@1" in e["source"]["originChain"] for e in br["effects"]))
        bad = door_semantic(); bad["nodes"][0]["coreOverride"] = []
        with self.assertRaises(Fault): lower_semantic(bad)

    def service_spec(self):
        args = {"record": {"target": "EntityID"}}
        spec = program(fields={"x": field(), "move": field("string")}, services={
            "Nav@1": {"kind": "command", "input": args, "determinism": "BackendSemantic", "cost": 1,
                      "delivery": "idempotent", "resource_field": "target"},
            "Sight@1": {"kind": "query", "input": args, "output": "bool", "determinism": "RecordedObservation", "cost": 1}},
            events={"Ack": {"record": {"target": "EntityID", "ok": "bool"}}}, handlers=[
                handler("a", "Go", [{"op": "let", "name": "visible", "value": expr("service_query", service="Sight@1", args=lit({"target": P}, args))},
                    write("x", lit(1)), {"op": "command", "service": "Nav@1", "args": lit({"target": P}, args)}]),
                handler("ack", "Ack", [{"op": "if", "condition": var("ok"), "then": [write("move", lit("Accepted", "string"), var("target"))],
                    "else": [write("move", lit("Blocked", "string"), var("target"))]}])])
        return spec

    def test_A09_delayed_failure_response_snapshot_restore_replay(self):
        core = compile_program(self.service_spec()); entities = initial(x=0, move="Idle")
        with self.assertRaises(Fault) as caught: Runtime(core, entities, capabilities=[])
        self.assertEqual(caught.exception.code, "CapabilityMismatch")
        caps = ["Nav@1", "Sight@1"]
        r = Runtime(core, entities, capabilities=caps)
        result = self.go(r, query_responses=[{"service": "Sight@1", "args": {"target": P}, "epoch": 0, "value": True}])
        self.assertEqual(result["status"], "COMMITTED"); self.assertEqual(len(result["commands"]), 1)
        self.assertEqual(self.value(r, "move"), "Idle")
        restored = Runtime.restore(core, json.loads(canonical_json(r.snapshot())), capabilities=caps)
        replies = [{"name": "Ack", "payload": {"target": P, "ok": False}}]
        self.assertEqual(r.step(replies=replies), restored.step(replies=replies))
        self.assertEqual(self.value(r), 1); self.assertEqual(self.value(r, "move"), "Blocked")
        replayed = replay(core, entities, r.history, capabilities=caps)
        self.assertEqual(replayed.snapshot(), r.snapshot())
        # There is no live backend callable in replay; pure responses come only from the journal.
        changed = copy.deepcopy(r.snapshot()); changed["program_digest"] = "wrong"
        with self.assertRaises(Fault) as caught: Runtime.restore(core, changed, capabilities=caps)
        self.assertEqual(caught.exception.code, "SnapshotSchemaMismatch")

    def test_A09_query_missing_epoch_and_command_conflict(self):
        spec = self.service_spec(); r = self.runtime(spec, initial(x=0, move="Idle"), capabilities=["Nav@1", "Sight@1"])
        self.assertEqual(self.go(r)["error"]["code"], "ServiceResponseMissing")
        r = self.runtime(spec, initial(x=0, move="Idle"), capabilities=["Nav@1", "Sight@1"])
        self.assertEqual(self.go(r, query_responses=[{"service": "Sight@1", "args": {"target": P}, "epoch": 1, "value": True}])["error"]["code"], "ServiceResponseMissing")
        spec["handlers"][0]["body"].pop(0)
        spec["handlers"].append(handler("b", "Go", [copy.deepcopy(spec["handlers"][0]["body"][-1])]))
        r = self.runtime(spec, initial(x=0, move="Idle"), capabilities=["Nav@1", "Sight@1"])
        self.assertEqual(self.go(r)["error"]["code"], "WriteConflict")

    def test_A09_pure_extension_no_hidden_world(self):
        spec = program(extensions={"Twice@1": {"input": "int", "output": "int", "cost": 2,
                                               "body": binary("add", var("input"), var("input"))}},
                       handlers=[handler("a", "Go", [write("x", expr("extension", extension="Twice@1", input=lit(3)))])])
        r = self.runtime(spec); self.go(r); self.assertEqual(self.value(r), 6)
        spec["extensions"]["Twice@1"]["body"] = read("x")
        with self.assertRaises(Fault) as caught: compile_program(spec)
        self.assertEqual(caught.exception.code, "EffectViolation")


if __name__ == "__main__": unittest.main()
