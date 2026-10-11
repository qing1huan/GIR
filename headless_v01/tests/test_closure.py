import copy
import unittest

from headless_v01.compiler import compile_program, lower_semantic, make_plan
from headless_v01.runtime import Runtime, replay
from headless_v01.types import Fault
from .fixtures import *


class LoweringClosure(unittest.TestCase):
    def test_A08_cost_preserving_constant_fold_and_budget_fault(self):
        body = [write("x", binary("add", lit(2), binary("mul", lit(3), lit(4))))]
        core = compile_program(program(handlers=[handler("a", "Go", body)]))
        plan = make_plan(core, optimize=True)
        self.assertTrue(plan["folds"])
        for limit in [20000, 5]:
            a = Runtime(core, initial(), budgets={"units": limit}); b = Runtime(plan, initial(), budgets={"units": limit})
            ar = a.step(inputs=[{"name": "Go", "payload": {}}]); br = b.step(inputs=[{"name": "Go", "payload": {}}])
            self.assertEqual(ar, br); self.assertEqual(a.snapshot(), b.snapshot())
        bad = clone(plan); next(iter(bad["folds"].values()))["value"] = 99
        with self.assertRaises(Fault): Runtime(bad, initial())
        self.evidence = {"core": core, "optimized_plan": plan, "last_budget_result": ar, "snapshot": a.snapshot()}

    def test_A08_reorder_mutations_detect_semantic_difference_and_origin(self):
        from headless_v01.plan_checks import compare_execution
        core = compile_program(program(fields={"x": field("number", policy="ordered_add")}, handlers=[
            handler(str(i), "Go", [add("x", lit(v, "number"))], i) for i, v in enumerate([1e16, -1e16, 1.])]))
        mutant = program(fields={"x": field("number", policy="ordered_add")}, handlers=[
            handler(str(i), "Go", [add("x", lit(v, "number"))], i) for i, v in enumerate([1e16, 1., -1e16])])
        diff = compare_execution(core, compile_program(mutant), initial(x=0.), [{"inputs": [{"name": "Go", "payload": {}}]}])
        self.assertEqual(diff["status"], "DIFFERENT"); self.assertTrue(diff["originChains"])
        self.assertEqual(diff["reference_world"][0]["values"]["x"], 1.)
        self.assertEqual(diff["candidate_world"][0]["values"]["x"], 0.)
        float_difference = copy.deepcopy(diff)
        # Draw reordering: same RNG final state, different values assigned to x/y.
        spec = program(fields={"x": field(), "y": field()}, handlers=[handler("a", "Go", [
            {"op": "draw", "name": "a", "bound": lit(2**32)}, {"op": "draw", "name": "b", "bound": lit(2**32)},
            write("x", var("a")), write("y", var("b"))])])
        mutant = clone(spec); mutant["handlers"][0]["body"][0]["name"] = "b"; mutant["handlers"][0]["body"][1]["name"] = "a"
        diff = compare_execution(compile_program(spec), compile_program(mutant), initial(x=0, y=0), [{"inputs": [{"name": "Go", "payload": {}}]}])
        self.assertEqual(diff["status"], "DIFFERENT")
        self.assertNotEqual(diff["reference_world"], diff["candidate_world"])
        self.evidence = {"float": float_difference, "rng": diff, "observation_mutation": "forged Plan rejected; no independent physical backend claim"}
        # Replacing explicit observation with desired IR state is a forbidden Plan mutation.
        fields = {"x": field("vec2"), "pose": field("vec2", authority="Backend")}
        spec = program(fields, [handler("a", "Go", [write("x", expr("observe", entity=lit(P, "EntityID"), field="pose"))])])
        plan = make_plan(compile_program(spec), optimize=True); plan["core"]["handlers"][0]["body"][0]["value"] = lit([9., 9.], "vec2")
        with self.assertRaises(Fault): Runtime(plan, initial(x=[0., 0.]))
        spec["fields"]["otherPose"] = field("vec2", authority="Backend")
        candidate = clone(spec); candidate["handlers"][0]["body"][0]["value"]["field"] = "otherPose"
        observation_diff = compare_execution(compile_program(spec), compile_program(candidate), initial(x=[0., 0.]), [{
            "inputs": [{"name": "Go", "payload": {}}], "observations": [{"id": P, "values": {"pose": [1., 2.], "otherPose": [9., 9.]}}]}])
        self.assertEqual(observation_diff["status"], "DIFFERENT")
        self.assertNotEqual(observation_diff["reference_world"], observation_diff["candidate_world"])
        self.evidence["observation"] = observation_diff
        equivalent = clone(spec); equivalent["handlers"][0]["body"][0]["_origin"] = ["DifferentSourceIdentity"]
        self.assertEqual(compare_execution(compile_program(spec), compile_program(equivalent), initial(x=[0., 0.]), [{
            "inputs": [{"name": "Go", "payload": {}}], "observations": [{"id": P, "values": {"pose": [1., 2.]}}]}])["status"], "EQUAL")

    def test_A08_override_reexpand_conflict_preserves_manual(self):
        from headless_v01.compiler import reexpand_semantic, digest
        semantic = door_semantic(); base = lower_semantic(semantic); manual = clone(base["handlers"][-1])
        manual["body"] = []
        overrides = {"door.open": {"base_digest": digest(base["handlers"][-1]), "handler": manual}}
        core = reexpand_semantic(semantic, overrides)
        self.assertEqual(core["handlers"][-1]["body"], [])
        changed = clone(semantic); changed["nodes"][0]["key_field"] = "other"
        with self.assertRaises(Fault) as caught: reexpand_semantic(changed, overrides)
        self.assertEqual(caught.exception.code, "CoreOverrideConflict"); self.assertEqual(overrides["door.open"]["handler"]["body"], [])
        self.evidence = {"semantic": semantic, "overrides": overrides, "core": core, "reexpansion_error": {"code": caught.exception.code, "details": caught.exception.details}}


class QueryClosure(unittest.TestCase):
    def spec(self):
        fields = {"hp": field(), "selected": field({"option": "EntityID"}), "pose": field("vec2", authority="Backend")}
        ids = expr("query", fields=["hp"], bind="candidate", where=binary("gt", read("hp", var("candidate")), lit(0)))
        points = expr("map", items=var("ids"), bind="candidate", body=expr("observe", entity=var("candidate"), field="pose"))
        select = [
            {"op": "let", "name": "ids", "value": ids},
            write("selected", expr("nearest", items=var("ids"), points=points,
                                  origin=expr("observe", entity=lit(P, "EntityID"), field="pose"))),
        ]
        return program(fields, [handler("select", "Go", select),
                               handler("delete", "Delete", [{"op": "despawn", "entity": lit(E, "EntityID")}])], events={"Delete": {"record": {}}})

    def test_A05_nearest_predicate_tie_stale_and_replay(self):
        core = compile_program(self.spec())
        entities = [{"id": P, "values": {"selected": {"none": True}}},
                    {"id": E, "values": {"hp": 1}}, {"id": N, "values": {"hp": 1}}]
        r = Runtime(make_plan(core), entities)
        observations = [{"id": P, "values": {"pose": [0., 0.]}}, {"id": E, "values": {"pose": [2., 0.]}}, {"id": N, "values": {"pose": [-2., 0.]}}]
        result = r.step(inputs=[{"name": "Go", "payload": {}}], observations=observations)
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(r.snapshot()["entities"][0]["values"]["selected"], {"some": E})
        self.assertTrue(any(x["op"] == "read" and x["entity"] == E and x["field"] == "hp" for x in result["reads"]))
        result = r.step(inputs=[{"name": "Delete", "payload": {}}, {"name": "Go", "payload": {}}], observations=observations)
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(r.snapshot()["entities"][0]["values"]["selected"], {"some": N})
        self.assertEqual(replay(core, entities, r.history).snapshot(), r.snapshot())
        self.evidence = {"core": core, "initial": entities, "journal": r.history, "snapshot": r.snapshot()}

    def test_A05_predicate_reads_snapshot_not_own_overlay(self):
        q = expr("query", fields=["hp"], bind="id", where=binary("gt", read("hp", var("id")), lit(0)))
        spec = program(fields={"hp": field()}, handlers=[handler("a", "Go", [write("hp", lit(0)),
            {"op": "assert", "condition": binary("eq", expr("count", value=q), lit(1))}])])
        r = Runtime(compile_program(spec), initial(hp=1)); self.assertEqual(r.step(inputs=[{"name": "Go", "payload": {}}])["status"], "COMMITTED")

    def test_A05_query_and_nearest_error_rollback(self):
        for value in [lit([[0., 0.]], {"list": "vec2"}), lit([[1e308, 0.], [0., 0.]], {"list": "vec2"})]:
            spec = program(handlers=[handler("a", "Go", [write("x", lit(3)),
                {"op": "let", "name": "n", "value": expr("nearest", items=lit([P, E], {"list": "EntityID"}), points=value, origin=lit([0., 0.], "vec2"))}])])
            r = Runtime(compile_program(spec), initial()); before = r.snapshot(); result = r.step(inputs=[{"name": "Go", "payload": {}}])
            self.assertEqual(result["status"], "ABORTED"); self.assertEqual(r.snapshot(), before)
            self.assertIn(result["error"]["code"], ["TypeError", "NumericOverflow"])
        spec = self.spec(); spec["handlers"][0]["body"][0]["value"]["where"] = lit(1)
        with self.assertRaises(Fault): compile_program(spec)




def definition(id_, state_type, handlers=None, required=0):
    return {"id": id_, "state_type": state_type, "handlers": handlers or [],
            "parameter_type": {"record": {"required": "int"}}, "parameters": {"required": required}}


def quest_definition(id_, required):
    state_type = {"record": {"count": "int", "seen": {"list": "string"}, "completed": "bool"}}
    state = read("state:"+id_, var("instance"))
    member = lambda name: expr("member", value=var("state"), name=name)
    body = [{"op": "if", "condition": expr("is_some", value=expr("read", entity=var("instance"), field="state:"+id_)), "then": [
        {"op": "let", "name": "state", "value": state},
        {"op": "if", "condition": binary("and", expr("not", value=member("completed")), expr("not", value=binary("contains", member("seen"), var("killID")))), "then": [
            {"op": "let", "name": "count", "value": binary("add", member("count"), lit(1))},
            {"op": "let", "name": "complete", "value": binary("ge", var("count"), lit(required))},
            write("state:"+id_, expr("record", items={"count": var("count"), "seen": expr("append", value=member("seen"), item=var("killID")), "completed": var("complete")}), var("instance")),
            {"op": "if", "condition": var("complete"), "then": [add("gold", lit(10))], "else": []},
        ], "else": []},
    ], "else": []}]
    return definition(id_, state_type, [handler("counter", "Kill", body)], required)


class LifecycleClosure(unittest.TestCase):
    def base(self):
        return program(fields={"gold": field(policy="ordered_add")}, events={
            "Kill": {"record": {"instance": "EntityID", "definition": "string", "killID": "string"}},
            "Pulse": {"record": {"instance": "EntityID", "definition": "string"}}})

    def controller(self, defs=None):
        from headless_v01.lifecycle import Lifecycle
        return Lifecycle(self.base(), initial(gold=0), defs or [quest_definition("quest@1", 2)], default=defs[0]["id"] if defs else "quest@1")

    def test_A10_preserve_quest_versions_dedup_save_and_replay(self):
        from headless_v01.lifecycle import Lifecycle
        c = self.controller(); q1 = c.create_instance({"count": 0, "seen": [], "completed": False})
        c.step(inputs=[c.target_event("Kill", q1, killID="K1")])
        c.activate(quest_definition("quest@2", 3), mode="preserve")
        q2 = c.create_instance({"count": 0, "seen": [], "completed": False})
        c.step(inputs=[c.target_event("Kill", q1, killID="K1"), c.target_event("Kill", q1, killID="K2")])
        self.assertEqual(c.instance_state(q1)["count"], 2); self.assertTrue(c.instance_state(q1)["completed"])
        self.assertEqual(c.instance_state(q2)["count"], 0); self.assertEqual(c.binding(q1), "quest@1"); self.assertEqual(c.binding(q2), "quest@2")
        self.assertEqual(c.runtime.snapshot()["entities"][0]["values"]["gold"], 10)
        restored = Lifecycle.restore(c.snapshot()); self.assertEqual(restored.snapshot(), c.snapshot())
        self.assertEqual(Lifecycle.replay(c.initial_config, c.journal).snapshot(), c.snapshot())
        bad = clone(c.snapshot()); del bad["packages"]["quest@1"]
        with self.assertRaises(Fault): Lifecycle.restore(bad)
        self.evidence = {"initial_config": c.initial_config, "journal": c.journal, "snapshot": c.snapshot(), "missing_package_rejected": True}

    def timer(self, id_):
        return definition(id_, {"record": {"duration": "int", "elapsed": "int", "remaining": "int", "nextPulse": "int"}})

    def test_A10_rebase_explicit_timing_pending_and_failure_atomic(self):
        c = self.controller([self.timer("burn@1")]); old = c.create_instance({"duration": 5, "elapsed": 2, "remaining": 3, "nextPulse": 3}, "burn@1")
        # Pending event is produced by an actual Core schedule, not a forged save value.
        schedule = handler("schedule", "Go", [{"op": "schedule", "delay": lit(3), "event": "Pulse", "payload": lit(c.target_event("Pulse", old)["payload"], c.base["events"]["Pulse"])}])
        c.install_global_handlers([schedule]); c.step(inputs=[{"name": "Go", "payload": {}}])
        state = var("input"); member = lambda key: expr("member", value=state, name=key)
        migration = expr("record", items={"duration": lit(8), "elapsed": member("elapsed"), "remaining": binary("sub", lit(8), member("elapsed")), "nextPulse": member("nextPulse")})
        c.activate(self.timer("burn@2"), mode="rebase", migration=migration, queue_policy="rebind_preserve_time")
        self.assertEqual(c.instance_state(old)["remaining"], 6); self.assertEqual(c.instance_state(old)["nextPulse"], 3)
        future = c.runtime.snapshot()["future"][0]
        self.assertEqual(future["tick"], 3); self.assertEqual(future["event"]["payload"]["definition"], "burn@2")
        before = c.snapshot(); bad = clone(migration); bad["items"]["remaining"] = binary("add", lit(2**53-1), member("elapsed"))
        with self.assertRaises(Fault) as caught: c.activate(self.timer("burn@3"), mode="rebase", migration=bad, queue_policy="rebind_preserve_time")
        self.assertEqual(caught.exception.code, "NumericOverflow"); self.assertEqual(c.snapshot(), before)
        self.assertEqual(type(c).replay(c.initial_config, c.journal).snapshot(), c.snapshot())
        self.evidence = {"initial_config": c.initial_config, "journal": c.journal, "snapshot": c.snapshot()}
    def test_A10_partial_migration_err_hidden_read_and_budget_rollback(self):
        c = self.controller([self.timer("burn@1")])
        for elapsed in [1, 2]: c.create_instance({"duration": 5, "elapsed": elapsed, "remaining": 5-elapsed, "nextPulse": 3}, "burn@1")
        before = c.snapshot(); t = self.timer("burn@2")["state_type"]; result_t = {"result": {"ok": t, "err": "string"}}
        migration = expr("select", condition=binary("eq", expr("member", value=var("input"), name="elapsed"), lit(1)),
            then=expr("result", tag="ok", value=var("input"), ok_type=t, err_type="string"),
            **{"else": lit({"err": "blocked"}, result_t)})
        with self.assertRaises(Fault) as caught: c.activate(self.timer("burn@2"), "rebase", migration, "rebind_preserve_time")
        self.assertEqual(caught.exception.code, "MigrationFailed"); self.assertEqual(c.snapshot(), before)
        with self.assertRaises(Fault) as caught: c.activate(self.timer("burn@2"), "rebase", read("gold"), "rebind_preserve_time")
        self.assertEqual(caught.exception.code, "EffectViolation"); self.assertEqual(c.snapshot(), before)
        self.assertEqual(type(c).replay(c.initial_config, c.journal).snapshot(), c.snapshot())
        self.evidence = {"initial_config": c.initial_config, "journal": c.journal, "snapshot": c.snapshot()}

    def test_A10_cumulative_migration_budget_and_namespace_abort_restore(self):
        from headless_v01.lifecycle import Lifecycle
        c = Lifecycle(self.base(), initial(gold=0), [self.timer("burn@1")], "burn@1", budgets={"units": 4})
        for elapsed in [1, 2]: c.create_instance({"duration": 5, "elapsed": elapsed, "remaining": 5-elapsed, "nextPulse": 3})
        before = c.snapshot()
        with self.assertRaises(Fault) as caught: c.activate(self.timer("burn@2"), "rebase", var("input"), "rebind_preserve_time")
        self.assertEqual(caught.exception.code, "BudgetExceeded"); self.assertEqual(c.snapshot(), before)
        self.assertEqual(Lifecycle.replay(c.initial_config, c.journal).snapshot(), before)
        budget_evidence = {"initial_config": c.initial_config, "journal": c.journal, "snapshot": c.snapshot()}
        c = self.controller(); state = {"count": 0, "seen": [], "completed": False}
        q = c.create_instance(state); c.activate(quest_definition("quest@2", 3), "preserve")
        c.install_global_handlers([handler("bad", "Go", [add("gold", lit(9)), write("state:quest@2", lit(state, quest_definition("quest@2", 3)["state_type"]), lit(q, "EntityID"))])])
        before_world = c.runtime.snapshot(); before_registry = clone(c.registry)
        result = c.step(inputs=[{"name": "Go", "payload": {}}])
        self.assertEqual(result["error"]["code"], "NamespaceViolation"); self.assertEqual(result["commands"], [])
        self.assertEqual(c.runtime.snapshot(), before_world); self.assertEqual(c.registry, before_registry)
        self.assertTrue(c.runtime.halted); self.assertTrue(Lifecycle.restore(c.snapshot()).runtime.halted)
        self.assertEqual(Lifecycle.replay(c.initial_config, c.journal).snapshot(), c.snapshot())
        self.evidence = {"budget": budget_evidence, "namespace": {"initial_config": c.initial_config, "journal": c.journal, "snapshot": c.snapshot()}}

    def test_A10_restart_cancels_timer_no_reward_and_bad_abi_rejected(self):
        c = self.controller(); q = c.create_instance({"count": 0, "seen": [], "completed": False}, reservations=["nav-slot:7"])
        c.install_global_handlers([handler("schedule", "Go", [{"op": "schedule", "delay": lit(2), "event": "Pulse", "payload": lit(c.target_event("Pulse", q)["payload"], c.base["events"]["Pulse"])}])])
        c.step(inputs=[{"name": "Go", "payload": {}}]); result = c.activate(quest_definition("quest@2", 3), mode="restart")
        self.assertEqual(c.runtime.snapshot()["future"], []); self.assertEqual(result["cancelledEvents"][0]["code"], "TargetCancelled")
        self.assertEqual(result["releasedReservations"], ["nav-slot:7"]); self.assertEqual(c.registry, {})
        self.assertEqual(c.runtime.snapshot()["entities"][0]["values"]["gold"], 0)
        before = c.snapshot(); bad = quest_definition("quest@3", 4); bad["abi_digest"] = "different"
        with self.assertRaises(Fault): c.activate(bad)
        self.assertEqual(c.snapshot(), before)
        self.assertEqual(type(c).replay(c.initial_config, c.journal).snapshot(), c.snapshot())
        self.evidence = {"initial_config": c.initial_config, "journal": c.journal, "snapshot": c.snapshot()}





class ServiceClosure(unittest.TestCase):
    def test_A09_mock_failure_delayed_ack_restore_and_changed_provider_replay(self):
        from headless_v01.services import ScriptedBackend, ServiceTapeAdapter
        from .test_contract import ContractTests
        core = compile_program(ContractTests().service_spec()); entities = initial(x=0, move="Idle"); caps = ["Nav@1", "Sight@1"]
        backend = ScriptedBackend({"Sight@1": True}, {"Nav@1": {"name": "Ack", "payload": {"ok": False}}})
        adapter = ServiceTapeAdapter(Runtime(core, entities, capabilities=caps), backend)
        result = adapter.step(inputs=[{"name": "Go", "payload": {}}], query_requests=[{"service": "Sight@1", "args": {"target": P}}])
        self.assertEqual(result["status"], "COMMITTED"); self.assertEqual(backend.sends, 1); self.assertEqual(len(backend.pending), 1)
        restored = ServiceTapeAdapter.restore(core, adapter.snapshot(), caps)
        self.assertEqual(adapter.step(), restored.step()); self.assertEqual(restored.backend.sends, 1)
        self.assertEqual(adapter.runtime.snapshot()["entities"][0]["values"], {"x": 1, "move": "Blocked"})
        changed_provider = ScriptedBackend({"Sight@1": False}, {"Nav@1": {"name": "Ack", "payload": {"ok": True}}})
        changed_provider.capture = lambda *_: self.fail("replay must not query changed live provider")
        replayed = ServiceTapeAdapter.replay_frozen(core, entities, adapter.runtime.history, capabilities=caps)
        self.assertEqual(replayed.snapshot(), adapter.runtime.snapshot()); self.assertEqual(changed_provider.queries, 0)
        self.evidence = {"core": core, "initial": entities, "journal": adapter.runtime.history, "adapter_snapshot": adapter.snapshot(), "replay_snapshot": replayed.snapshot(), "provider_kind": "SCRIPTED_MOCK"}
        # This is scripted-provider isolation, not a real physical-backend equivalence claim.

    def test_A09_missing_version_pure_extension_budget_and_rollback(self):
        from .test_contract import ContractTests
        with self.assertRaises(Fault) as caught: Runtime(compile_program(ContractTests().service_spec()), initial(x=0, move="Idle"), capabilities=["Nav@2", "Sight@1"])
        self.assertEqual(caught.exception.code, "CapabilityMismatch")
        spec = program(extensions={"Pure@1": {"input": "int", "output": "int", "cost": 100, "body": var("input")}},
            handlers=[handler("a", "Go", [write("x", lit(3)), write("x", expr("extension", extension="Pure@1", input=lit(7)))])])
        r = Runtime(compile_program(spec), initial(), budgets={"units": 20}); before = r.snapshot()
        result = r.step(inputs=[{"name": "Go", "payload": {}}]); self.assertEqual(result["error"]["code"], "BudgetExceeded"); self.assertEqual(r.snapshot(), before)
        self.evidence = {"core": r.core, "initial": initial(), "journal": r.history, "snapshot": r.snapshot()}


class EditingClosure(unittest.TestCase):
    def document(self):
        base = program(events={"Source": {"record": {}}, "Target": {"record": {}}, "Wrong": {"record": {"x": "int"}}})
        return {"version": "semantic-v0.1", "base": base, "nodes": [
            {"id": "sender", "library": "CoreOverride", "handler": handler("sender", "Go", [emit("Source")])},
            {"id": "receiver", "library": "CoreOverride", "handler": handler("receiver", "Target", [write("x", lit(7))])}]}

    def editor(self):
        from headless_v01.editing import SemanticEditor
        return SemanticEditor(self.document(), initial(), test_catalog={"sender": ["public.sender"], "receiver": ["public.receiver"]})

    def test_A11_patch_validate_revision_connect_activate_and_new_version_rollback(self):
        from headless_v01.editing import SemanticEditor
        e = self.editor(); original = e.snapshot(); e.connect("sender", "Source", "receiver", "Target")
        validated = e.validate_patch(); self.assertEqual(validated["status"], "VALID"); self.assertEqual(e.snapshot(), original)
        wrong = e.commit_patch(expectedRevision=3); self.assertEqual(wrong["errors"][0]["code"], "RevisionConflict"); self.assertEqual(e.snapshot(), original)
        committed = e.commit_patch(expectedRevision=0); self.assertEqual(committed["status"], "COMMITTED"); self.assertEqual(e.revision, 1)
        e.step(inputs=[{"name": "Go", "payload": {}}]); self.assertEqual(e.runtime.snapshot()["entities"][0]["values"]["x"], 7)
        rollback = e.restore_published(0, expectedRevision=1)
        self.assertEqual(rollback["status"], "COMMITTED"); self.assertEqual(e.revision, 2)
        self.assertEqual(e.runtime.snapshot()["entities"][0]["values"]["x"], 7)
        self.assertEqual(SemanticEditor.replay(e.initial_config, e.journal).snapshot(), e.snapshot())
        self.evidence = {"initial_config": e.initial_config, "journal": e.journal, "snapshot": e.snapshot()}

    def test_A11_unknown_port_type_control_and_atomic_failure(self):
        e = self.editor(); before = e.snapshot()
        for args in [("unknown", "Source", "receiver", "Target"), ("sender", "missing", "receiver", "Target")]:
            e.connect(*args); self.assertEqual(e.validate_patch()["status"], "REJECTED"); self.assertEqual(e.snapshot(), before); e.rollback_patch()
        node = self.document()["nodes"][0]; node["handler"]["body"] = [{"op": "emit", "event": "Wrong", "payload": lit({"x": 1}, {"record": {"x": "int"}})}]
        e.replace("sender", node); e.connect("sender", "Wrong", "receiver", "Target")
        self.assertEqual(e.validate_patch()["errors"][0]["code"], "PortTypeMismatch"); self.assertEqual(e.snapshot(), before); e.rollback_patch()
        node = self.document()["nodes"][0]; node["handler"]["body"] = [{"op": "while", "body": []}]
        e.replace("sender", node); result = e.commit_patch(expectedRevision=0)
        self.assertEqual(result["status"], "REJECTED"); self.assertEqual(e.snapshot(), before)

    def test_A11_unknown_impact_create_delete_and_uncommitted_rollback(self):
        e = self.editor(); before = e.snapshot()
        node = {"id": "query", "library": "CoreOverride", "handler": handler("query", "Go", [{"op": "let", "name": "ids", "value": expr("query", fields=["x"])}])}
        e.create(node); validated = e.validate_patch()
        self.assertEqual(validated["dependencyPrecision"], "unknown"); self.assertEqual(set(validated["requiredTests"]), {"public.sender", "public.receiver"})
        self.assertEqual(e.rollback_patch()["status"], "ROLLED_BACK_DRAFT"); self.assertEqual(e.snapshot(), before)
        e.create(node); self.assertEqual(e.commit_patch(expectedRevision=0)["status"], "COMMITTED")
        self.assertTrue(e.query_nodes(id="query")); self.assertEqual(e.get_dependencies("query")["precision"], "unknown")
        e.delete("query"); self.assertEqual(e.commit_patch(expectedRevision=1)["status"], "COMMITTED")
        self.assertFalse(e.query_nodes(id="query"))
        self.assertEqual(type(e).replay(e.initial_config, e.journal).snapshot(), e.snapshot())
        self.evidence = {"initial_config": e.initial_config, "journal": e.journal, "snapshot": e.snapshot()}

    def test_A11_shared_field_impact_pending_migration_and_overflow_atomic(self):
        e = self.editor(); node = self.document()["nodes"][0]
        node["handler"]["body"] = [write("x", lit(4))]
        e.replace("sender", node); result = e.validate_patch()
        self.assertEqual(set(result["requiredTests"]), {"public.sender", "public.receiver"})
        e.rollback_patch()
        node["handler"]["body"] = [{"op": "schedule", "delay": lit(2), "event": "Target", "payload": lit({}, {"record": {}})}]
        e.replace("sender", node); self.assertEqual(e.commit_patch(0)["status"], "COMMITTED")
        e.step(inputs=[{"name": "Go", "payload": {}}]); before = e.snapshot()
        node["handler"]["body"] = []; e.replace("sender", node)
        result = e.commit_patch(1); self.assertEqual(result["errors"][0]["code"], "MigrationRequired"); self.assertEqual(e.snapshot(), before)
        self.assertEqual(type(e).replay(e.initial_config, e.journal).snapshot(), before)
        self.evidence = {"initial_config": e.initial_config, "journal": e.journal, "snapshot": e.snapshot()}
        e = self.editor(); e.revision = 2**53-1; before = e.snapshot(); e.replace("sender", self.document()["nodes"][0])
        result = e.commit_patch(2**53-1); self.assertEqual(result["errors"][0]["code"], "NumericOverflow"); self.assertEqual(e.snapshot(), before)


if __name__ == "__main__": unittest.main()
