import copy
import unittest

from headless_v01.compiler import compile_program, digest, make_plan
from headless_v01.runtime import Runtime, replay
from headless_v01.types import Fault, validate_value
from .fixtures import *


class AdditionalBoundaries(unittest.TestCase):
    def test_value_nodes_bytes_map_len_and_structural_eq(self):
        cases = [({"map": "int"}, {str(i): i for i in range(1025)}, {"length": 1024}),
                 ({"list": "int"}, [1, 2, 3], {"value_nodes": 3}),
                 ("string", "aaaa", {"value_bytes": 5})]
        for type_, value, limits in cases:
            with self.assertRaises(Fault) as caught: validate_value(type_, value, limits)
            self.assertEqual(caught.exception.code, "BudgetExceeded")
        validate_value({"list": "int"}, [1, 2, 3], {"value_nodes": 4})
        validate_value("string", "aaaa", {"value_bytes": 6})
        self.assertNotEqual(validate_value({"list": "int"}, [1, 2]), [2, 1])
        validate_value({"result": {"ok": "int", "err": "string"}}, {"err": "NoPath"})
        special = {"record": {"_digest": "int", "_compiled": "bool", "op": "string", "_source": "int"}}
        spec = program(fields={"bag": field(special)})
        value = {"_digest": 3, "_compiled": False, "op": "read", "_source": 4}
        r = Runtime(make_plan(compile_program(spec)), initial(bag=value))
        self.assertEqual(r.snapshot()["entities"][0]["values"]["bag"], value)

    def test_ast_depth_malformed_and_forged_annotations(self):
        node = lit(True, "bool")
        for _ in range(65): node = expr("not", value=node)
        spec = program(handlers=[handler("a", "Go", [{"op": "assert", "condition": node}])])
        with self.assertRaises(Fault) as caught: compile_program(spec)
        self.assertEqual(caught.exception.code, "BudgetExceeded")
        for node in [{"op": "write"}, {"op": "emit", "event": "Go"}]:
            with self.assertRaises(Fault): compile_program(program(handlers=[handler("a", "Go", [node])]))
        spec = program(handlers=[handler("a", "Go", [write("x", lit(1))])])
        compiled = compile_program(spec)
        compiled["handlers"][0]["body"][0]["field"] = "unknown"
        compiled["_digest"] = digest({k: v for k, v in compiled.items() if k != "_digest"})
        with self.assertRaises(Fault) as caught: compile_program(compiled)
        self.assertEqual(caught.exception.code, "TypeError")
        plan = make_plan(compile_program(spec)); plan["dispatch"]["Go"] = []
        with self.assertRaises(Fault): Runtime(plan, initial())

    def test_default_event_cycle_has_bounded_uids(self):
        r = Runtime(compile_program(program(handlers=[handler("a", "Go", [emit("Go")])])), initial())
        result = r.step(inputs=[{"name": "Go", "payload": {}}])
        self.assertEqual(result["error"]["code"], "BudgetExceeded")
        self.assertEqual(result["budget_attempted"]["microsteps"], 33)
        self.assertTrue(all(len(e["id"]) < 100 for e in result["events"]))

    def test_new_max_clamp_and_scoped_loop_accumulator(self):
        spec = program(fields={"hp": field(policy="ordered_add", clamp={"min": 0, "max_field": "max"}), "max": field()},
                       handlers=[handler("a", "Go", [add("hp", lit(4)), write("max", lit(5))])])
        r = Runtime(compile_program(spec), initial(hp=1, max=2)); result = r.step(inputs=[{"name": "Go", "payload": {}}])
        self.assertEqual(result["status"], "COMMITTED"); self.assertEqual(r.snapshot()["entities"][0]["values"]["hp"], 5)
        spec = program(handlers=[handler("a", "Go", [
            {"op": "let", "name": "ids", "value": lit([], {"list": "int"})},
            {"op": "repeat", "name": "i", "count": lit(3), "max": 3, "body": [
                {"op": "let", "name": "ids", "value": expr("append", value=var("ids"), item=var("i"))}]},
            {"op": "assert", "condition": binary("eq", var("ids"), lit([0, 1, 2], {"list": "int"}))},
        ])])
        r = Runtime(compile_program(spec), initial()); self.assertEqual(r.step(inputs=[{"name": "Go", "payload": {}}])["status"], "COMMITTED")

    def test_failed_intermediate_write_and_two_deletes(self):
        for body in [[write("x", lit(2)), write("x", lit(0))], [add("x", lit(2**53-1)), add("x", lit(-1))]]:
            spec = program(fields={"x": field(range=[0, 1])}, handlers=[handler("a", "Go", body)])
            r = Runtime(compile_program(spec), initial(x=1)); before = r.snapshot()
            result = r.step(inputs=[{"name": "Go", "payload": {}}]); self.assertEqual(result["status"], "ABORTED"); self.assertEqual(r.snapshot(), before)
        spec = program(handlers=[handler("a", "Go", [{"op": "despawn", "entity": lit(P, "EntityID")}]),
                                  handler("b", "Go", [{"op": "despawn", "entity": lit(P, "EntityID")}])])
        r = Runtime(compile_program(spec), initial()); self.assertEqual(r.step(inputs=[{"name": "Go", "payload": {}}])["error"]["code"], "WriteConflict")

    def test_budget_units_effects_access_commands_and_live(self):
        for key, body, limit in [("units", [write("x", lit(1))], 2), ("effects", [write("x", lit(1)), write("x", lit(2))], 1),
                                  ("accesses", [write("x", read("x"))], 1)]:
            r = Runtime(compile_program(program(handlers=[handler("a", "Go", body)])), initial(), budgets={key: limit})
            before = r.snapshot(); result = r.step(inputs=[{"name": "Go", "payload": {}}])
            self.assertEqual(result["error"]["code"], "BudgetExceeded"); self.assertEqual(r.snapshot(), before)
        service = {"Visual@1": {"kind": "command", "input": {"record": {"target": "EntityID"}}, "determinism": "BackendSemantic",
                                "cost": 1, "delivery": "at-most-once", "resource_field": "target"}}
        spec = program(services=service, handlers=[handler("a", "Go", [{"op": "command", "service": "Visual@1", "args": lit({"target": P}, service["Visual@1"]["input"])}])])
        r = Runtime(compile_program(spec), initial(), budgets={"commands": 0}, capabilities=["Visual@1"])
        self.assertEqual(r.step(inputs=[{"name": "Go", "payload": {}}])["error"]["code"], "BudgetExceeded")
        spec = program(handlers=[handler("a", "Go", [{"op": "spawn", "name": "n", "values": {"x": lit(1)}}])])
        r = Runtime(compile_program(spec), initial(), budgets={"live": 1}); self.assertEqual(r.step(inputs=[{"name": "Go", "payload": {}}])["error"]["code"], "BudgetExceeded")

    def test_rng_known_vector_and_replay_tamper(self):
        spec = compile_program(program(handlers=[handler("a", "Go", [
            {"op": "draw", "name": "n", "bound": lit(2**32)}, write("x", var("n"))])]))
        r = Runtime(spec, initial(), seed=1); r.step(inputs=[{"name": "Go", "payload": {}}])
        self.assertEqual(r.snapshot()["entities"][0]["values"]["x"], 270369)
        r.step(inputs=[{"name": "Go", "payload": {}}])
        self.assertEqual(r.snapshot()["entities"][0]["values"]["x"], 67634689)
        self.assertEqual(replay(spec, initial(), r.history).snapshot(), r.snapshot())
        tampered = copy.deepcopy(r.history); tampered[0]["result"]["state_digest"] = "wrong"
        with self.assertRaises(Fault) as caught: replay(spec, initial(), tampered)
        self.assertEqual(caught.exception.code, "ReplayMismatch")

    def test_entity_stale_service_reply_resolve_none(self):
        spec = program(events={"Ack": {"record": {"target": "EntityID"}}}, handlers=[
            handler("delete", "Go", [{"op": "despawn", "entity": lit(E, "EntityID")}]),
            handler("ack", "Ack", [{"op": "assert", "condition": expr("not", value=expr("is_some", value=expr("resolve", entity=var("target"))))}])])
        r = Runtime(compile_program(spec), [{"id": P, "values": {"x": 0}}, {"id": E, "values": {"x": 0}}])
        r.step(inputs=[{"name": "Go", "payload": {}}]); result = r.step(replies=[{"name": "Ack", "payload": {"target": E}}])
        self.assertEqual(result["status"], "COMMITTED")

    def test_retry_cannot_replace_failed_admitted_input(self):
        spec = program(handlers=[handler("a", "Go", [emit("Go")])])
        r = Runtime(compile_program(spec), initial(), budgets={"microsteps": 2})
        r.step(inputs=[{"name": "Go", "payload": {}}])
        result = r.step(inputs=[], control="retry")
        self.assertEqual(result["error"]["code"], "RetryInputMismatch")
        result = r.step(inputs=[{"name": "Go", "payload": {}}], control="retry")
        self.assertEqual(result["error"]["code"], "BudgetExceeded")

    def test_map_structural_eq_list_order_and_queued_copy(self):
        t = {"map": {"list": "int"}}
        original = {"__proto__": [1], "$f64": [2]}
        payload_t = {"record": {"bag": t}}
        spec = program(fields={"bag": field(t)}, events={"Copied": payload_t}, handlers=[handler("a", "Go", [
            {"op": "let", "name": "copy", "value": read("bag")},
            {"op": "emit", "event": "Copied", "payload": expr("record", items={"bag": var("copy")})},
            {"op": "let", "name": "copy", "value": expr("map_put", value=var("copy"), key=lit("__proto__", "string"), item=lit([9], {"list": "int"}))},
            write("bag", var("copy")),
            {"op": "assert", "condition": binary("eq", lit(original, t), lit(dict(reversed(list(original.items()))), t))},
            {"op": "assert", "condition": expr("not", value=binary("eq", lit([1, 2], {"list": "int"}), lit([2, 1], {"list": "int"})))},
        ])])
        r = Runtime(compile_program(spec), initial(bag=original)); original["__proto__"].append(8)
        result = r.step(inputs=[{"name": "Go", "payload": {}}]); self.assertEqual(result["status"], "COMMITTED")
        copied = next(e for e in result["events"] if e["name"] == "Copied")
        self.assertEqual(copied["payload"]["bag"]["__proto__"], [1])
        self.assertEqual(r.snapshot()["entities"][0]["values"]["bag"]["__proto__"], [9])

    def test_dynamic_alias_and_container_cell_conflict(self):
        t = {"map": "int"}
        spec = program(fields={"bag": field(t)}, events={"Alias": {"record": {"target": "EntityID"}}}, handlers=[
            handler("a", "Alias", [write("bag", expr("map_put", value=read("bag", var("target")), key=lit("A", "string"), item=lit(1)), var("target"))]),
            handler("b", "Alias", [write("bag", expr("map_put", value=read("bag"), key=lit("B", "string"), item=lit(2)))])])
        r = Runtime(compile_program(spec), initial(bag={})); before = r.snapshot()
        result = r.step(inputs=[{"name": "Alias", "payload": {"target": P}}])
        self.assertEqual(result["error"]["code"], "WriteConflict"); self.assertEqual(r.snapshot(), before)

    def test_observation_epoch_same_for_later_microstep(self):
        pose = expr("observe", entity=lit(P, "EntityID"), field="pose")
        spec = program(fields={"pose": field("vec2", authority="Backend"), "desired": field("vec2")},
                       events={"Check": {"record": {}}}, handlers=[
            handler("a", "Go", [write("desired", lit([9., 9.], "vec2")), emit("Check")]),
            handler("b", "Check", [{"op": "assert", "condition": binary("eq", pose, lit([3., 4.], "vec2"))}])])
        r = Runtime(compile_program(spec), initial(desired=[0., 0.])); result = r.step(inputs=[{"name": "Go", "payload": {}}], observations=[{"id": P, "values": {"pose": [3., 4.]}}])
        self.assertEqual(result["status"], "COMMITTED")

    def test_clamp_cannot_hide_base_overflow_and_snapshot_config_mismatch(self):
        fields = {"hp": field(policy="ordered_add", clamp={"min": 0, "max_field": "max"}), "max": field()}
        core = compile_program(program(fields, [handler("a", "Go", [add("hp", lit(1))])]))
        r = Runtime(core, initial(hp=2**53-1, max=2**53-1)); before = r.snapshot()
        self.assertEqual(r.step(inputs=[{"name": "Go", "payload": {}}])["error"]["code"], "NumericOverflow")
        self.assertEqual(r.snapshot(), before)
        for key, value in [("numeric", "different-platform"), ("program_digest", "different-schema"), ("format", "unknown")]:
            bad = copy.deepcopy(before); bad[key] = value
            with self.assertRaises(Fault) as caught: Runtime.restore(core, bad)
            self.assertEqual(caught.exception.code, "SnapshotSchemaMismatch")


if __name__ == "__main__": unittest.main()
