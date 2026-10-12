import copy
import json
import unittest

from headless_v01.compiler import compile_program, digest
from headless_v01.runtime import Job, Transaction
from headless_v01.types import Fault, canonical_json
from ark_eval.adapter import EvaluationError
from ark_eval.callback_contract import (callback_pair, js_starter, model_records, static_js_preflight,
                                        validate_callback_pair)
from ark_eval.callback_verify import callback_manifest, control_check
from ark_eval.host import (ContextSession, DoorMockRunner, HostRuntime, MockClient,
                           ScriptedMockRunner, VERSION, execute_js_source, replay_host)
from ark_eval.oracle import final_cases, observations
from ark_eval.reference import control_program
from ark_eval.tasks import DOOR, PLAYER, base_program


def initial(): return [{"id": list(PLAYER), "values": {"keys": 2}}, {"id": list(DOOR), "values": {"open": False}}]
def open_input(): return {"name": "Open", "payload": {"player": list(PLAYER), "door": list(DOOR)}}
def frame(op, **args): return {"op": op, "args": args}


def runtime(frames=(), terminal="done", program=None, **options):
    return HostRuntime(program or callback_manifest(), initial(),
                       runners={"open": ScriptedMockRunner(tuple(frames), terminal)}, **options)


def session():
    r = runtime(); txn = Transaction(r)
    txn.job = "job"; txn.current_event = {"id": "root:0", "name": "Open", "payload": {}}
    return r, ContextSession(Job(txn, r.core["handlers"][0], txn.current_event, copy.deepcopy(r.world)))


class CallbackContractTests(unittest.TestCase):
    def test_three_paired_starters_budget_feedback_and_examples(self):
        for tid in ["P-C02-create", "P-C02-modify", "P-C02-debug"]:
            arms = callback_pair(tid); self.assertTrue(validate_callback_pair(arms))
            self.assertEqual(arms[0]["shared"], arms[1]["shared"])
            self.assertEqual(arms[1]["representation"], "js_callback")
            self.assertEqual(static_js_preflight(arms[1]["starter"])["execution_allowed"], False)
            self.assertNotIn("defineGame(", arms[1]["starter"])
            self.assertNotIn("final_tests", json.dumps([a["messages"] for a in arms]))
            self.assertTrue(all(c["id"] not in json.dumps(arms) for c in final_cases(tid)))
        self.assertIn("TODO", js_starter("P-C02-create"))
        self.assertIn("keys >= cost", js_starter("P-C02-modify"))
        self.assertIn("keys <= cost", js_starter("P-C02-debug"))

    def test_pair_rejects_changed_feedback_or_extra_information(self):
        arms = callback_pair("P-C02-create"); arms[1]["shared"]["feedback"] = "oracle"
        with self.assertRaises(EvaluationError): validate_callback_pair(arms)
        arms = callback_pair("P-C02-create"); arms[1]["messages"][0]["content"] += "extra"
        with self.assertRaises(EvaluationError): validate_callback_pair(arms)

    def test_static_preflight_is_not_syntax_or_sandbox_proof(self):
        data = static_js_preflight(js_starter("P-C02-create"))
        self.assertFalse(data["syntax_validated"]); self.assertFalse(data["source_executed"])
        for suffix in ['process.env;', 'require("fs");', 'fetch("https://example.invalid");', 'eval("x");']:
            with self.assertRaises(EvaluationError): static_js_preflight(js_starter("P-C02-create")+suffix)
        with self.assertRaisesRegex(EvaluationError, "ISOLATION_UNVERIFIED"): execute_js_source(js_starter("P-C02-create"))

    def test_independent_oracle_for_both_arms_and_known_bug(self):
        for tid in ["P-C02-create", "P-C02-modify", "P-C02-debug"]:
            ir = control_check(tid, "gameir_text"); js = control_check(tid, "js_callback")
            self.assertEqual(len(ir), 26); self.assertEqual(len(js), 26)
            self.assertTrue(all(c["status"] == "PASS" for c in ir+js))
            for a, b in zip(ir, js):
                self.assertEqual([e["host_operations"] for e in a["execution_audits"]],
                                 [e["host_operations"] for e in b["execution_audits"]])
        buggy = control_check("P-C02-debug", "js_callback", inverted=True)
        self.assertTrue(any(c["status"] == "FAIL" for c in buggy))

    def test_model_results_remain_unrun_and_ts_not_added(self):
        records = model_records()
        self.assertEqual(len(records), 6)
        self.assertEqual({r["representation"] for r in records}, {"gameir_text", "js_callback"})
        self.assertTrue(all(r["status"] == "NOT_RUN" and r["tokens_in"] is None for r in records))


class TrustedHostTests(unittest.TestCase):
    def test_read_own_write_and_commit_order(self):
        r = runtime([frame("write", entity=PLAYER, field="keys", value=1),
                     frame("read", entity=PLAYER, field="keys"),
                     frame("write", entity=PLAYER, field="keys", value=0),
                     frame("emit", event="Opened", payload={"door": DOOR})])
        result = r.step(inputs=[open_input()])
        self.assertEqual(result["status"], "COMMITTED")
        trace = r.execution_history[0]["transcripts"][0]["messages"]
        self.assertEqual(trace[1]["response"]["value"], {"some": 1})
        self.assertEqual(r.snapshot()["entities"][0]["values"]["keys"], 0)
        self.assertEqual([e["op"] for e in result["effects"]], ["write", "write", "emit"])
        self.assertEqual([e["ordinal"] for e in result["effects"]], [0, 1, 2])

    def test_duplicate_request_is_idempotent_and_not_free_wire_traffic(self):
        r = runtime([frame("emit", event="Opened", payload={"door": DOOR}), {"repeat_previous": True}])
        result = r.step(inputs=[open_input()])
        self.assertEqual(sum(e["name"] == "Opened" for e in result["events"]), 1)
        audit = r.execution_history[0]
        self.assertEqual(audit["host_operations_total"], 1)
        self.assertEqual(audit["callback_requests"], 3)
        self.assertTrue(audit["transcripts"][0]["messages"][1]["duplicate"])
        played = replay_host(callback_manifest(), initial(), r.history, r.execution_history)
        self.assertEqual(played.snapshot(), r.snapshot())

    def test_duplicate_changed_out_of_order_cross_invocation_and_bool_sequence(self):
        _, s = session(); c = MockClient(s); c.call("write", entity=PLAYER, field="keys", value=1)
        changed = json.loads(c.last); changed["args"]["value"] = 0
        with self.assertRaises(Fault) as caught: s.receive(canonical_json(changed))
        self.assertEqual(caught.exception.code, "ProtocolViolation")
        self.assertEqual(s.ctx.job.overlay[tuple(PLAYER)]["keys"], 1)
        for update in [{"seq": 1}, {"seq": True}, {"invocation": "other"}, {"extra": "x"}]:
            _, s = session()
            request = {"v": VERSION, "invocation": s.invocation, "seq": 0, "kind": "done", **update}
            with self.assertRaises(Fault): s.receive(canonical_json(request))

    def test_protocol_error_is_latched_and_done_does_not_recover(self):
        _, s = session(); c = MockClient(s)
        with self.assertRaises(Fault): c.call("read", entity=PLAYER, field="unknown")
        with self.assertRaises(Fault): c.done()
        self.assertFalse(s.closed)

    def test_finished_session_rejects_new_operations(self):
        _, s = session(); c = MockClient(s); c.done()
        self.assertEqual(c.repeat_previous()["kind"], "done")
        with self.assertRaises(Fault): c.call("write", entity=PLAYER, field="keys", value=1)

    def test_parameter_type_value_authority_and_policy_validation(self):
        p = callback_manifest()
        p["fields"]["pose"] = {"type": "int", "authority": "Backend"}
        p["fields"]["total"] = {"type": "int", "authority": "IR", "policy": "ordered_add"}
        checks = [(frame("write", entity=PLAYER, field="keys", value=True), "TypeError"),
                  (frame("write", entity=PLAYER, field="keys", value=11), "InvariantViolation"),
                  (frame("write", entity=PLAYER, field="pose", value=1), "AuthorityViolation"),
                  (frame("read", entity=PLAYER, field="pose"), "AuthorityViolation"),
                  (frame("write", entity=PLAYER, field="total", value=1), "EffectViolation"),
                  (frame("require", option={"none": 1}), "TypeError"),
                  (frame("require", option={"none": True}), "InvalidEntity"),
                  (frame("resolve", entity=["world", True, "genesis", 0]), "TypeError"),
                  (frame("int_sub", a=-(2**53-1), b=1), "NumericOverflow")]
        for f, code in checks:
            r = runtime([f], program=p); before = r.snapshot()
            result = r.step(inputs=[open_input()])
            self.assertEqual(result["error"]["code"], code)
            self.assertEqual(r.snapshot(), before)
            self.assertEqual(r.execution_history[0]["published_events"], [])

    def test_reserved_event_extra_payload_unknown_op_fail_closed(self):
        for f in [frame("emit", event="Tick", payload={}),
                  frame("emit", event="Opened", payload={"door": DOOR, "extra": True}),
                  frame("file_read", path="never-accessed.txt")]:
            r = runtime([f]); before = r.snapshot(); result = r.step(inputs=[open_input()])
            self.assertEqual(result["status"], "ABORTED"); self.assertEqual(r.snapshot(), before)

    def test_exception_timeout_disconnect_exit_roll_back_everything(self):
        self.evidence = []
        p = base_program(); p["events"].update({n: {"record": {}} for n in ["Prep", "Bad", "Later"]})
        p["handlers"] = [{"id": "prep", "event": "Prep", "priority": 0, "body": []},
                         {"id": "bad", "event": "Bad", "priority": 0, "body": []}]
        prep = ScriptedMockRunner((frame("write", entity=PLAYER, field="keys", value=1),
                                   frame("draw", bound=10), frame("schedule", event="Later", payload={}, delay=2),
                                   frame("emit", event="Opened", payload={"door": DOOR})))
        for fault in ["JS_EXCEPTION", "JS_TIMEOUT", "JS_DISCONNECTED", "JS_EXITED"]:
            r = HostRuntime(p, initial(), runners={"prep": prep, "bad": ScriptedMockRunner((), fault)})
            before = r.snapshot()
            result = r.step(inputs=[{"name": "Prep", "payload": {}}, {"name": "Bad", "payload": {}}])
            self.assertEqual(result["error"]["code"], fault)
            self.assertEqual(r.snapshot(), before)  # world/RNG/cursor/future/outbox/tick
            self.assertTrue(result["effects"])  # Attempt diagnostics are retained.
            self.assertEqual(r.execution_history[0]["published_events"], [])
            self.assertEqual(r.execution_history[0]["published_commands"], [])
            played = replay_host(p, initial(), r.history, r.execution_history)
            self.assertEqual(played.snapshot(), before)
            self.evidence.append({"fault": fault, "before": before, "after": r.snapshot(),
                                  "result": result, "execution_audit": r.execution_history[0],
                                  "replay_snapshot": played.snapshot(), "source_executions": 0,
                                  "kind": "FIXED_PROTOCOL_FAULT_SIMULATION_NOT_PROCESS_ISOLATION_PROOF"})

    def test_request_and_common_host_operation_limits_abort(self):
        r = runtime([frame("read", entity=PLAYER, field="keys")] * 32)
        before = r.snapshot(); result = r.step(inputs=[open_input()])
        self.assertEqual(result["error"]["code"], "BudgetExceeded")
        self.assertEqual(r.snapshot(), before)
        self.assertEqual(replay_host(callback_manifest(), initial(), r.history, r.execution_history).snapshot(), before)
        for callback in [True, False]:
            p = callback_manifest() if callback else control_program("P-C02-create")
            r = HostRuntime(p, initial(), host_limit=6, runners={"open": DoorMockRunner()} if callback else None)
            before = r.snapshot(); result = r.step(inputs=[open_input()])
            self.assertEqual(result["error"]["code"], "BudgetExceeded")
            self.assertEqual(result["error"]["details"]["resource"], "host_operations")
            self.assertEqual(r.snapshot(), before)

    def test_message_and_response_size_limits(self):
        _, s = session()
        with self.assertRaises(Fault) as caught: s.receive("x" * 8193)
        self.assertEqual(caught.exception.code, "BudgetExceeded")
        _, s = session()
        with self.assertRaises(Fault): s.receive('{"v":1,"v":2}')
        p = callback_manifest(); p["fields"]["text"] = {"type": "string", "authority": "IR"}
        values = initial(); values[0]["values"]["text"] = "x" * 9000
        r = HostRuntime(p, values, runners={"open": ScriptedMockRunner((frame("read", entity=PLAYER, field="text"),))})
        before = r.snapshot(); result = r.step(inputs=[open_input()])
        self.assertEqual(result["error"]["code"], "BudgetExceeded")
        self.assertEqual(r.snapshot(), before)

    def test_other_handler_writes_not_visible_and_conflict_rollback(self):
        p = callback_manifest(); p["handlers"].append({"id": "second", "event": "Open", "priority": 0, "body": []})
        first = ScriptedMockRunner((frame("write", entity=PLAYER, field="keys", value=1),))
        second = ScriptedMockRunner((frame("read", entity=PLAYER, field="keys"),
                                     frame("write", entity=PLAYER, field="keys", value=0)))
        r = HostRuntime(p, initial(), runners={"open": first, "second": second})
        before = r.snapshot(); result = r.step(inputs=[open_input()])
        self.assertEqual(result["error"]["code"], "WriteConflict")
        self.assertEqual(r.execution_history[0]["transcripts"][1]["messages"][0]["response"]["value"], {"some": 2})
        self.assertEqual(r.snapshot(), before)

    def test_recorded_replay_rejects_response_tamper_and_extra_transcript(self):
        r = runtime([frame("read", entity=PLAYER, field="keys")]); r.step(inputs=[open_input()])
        audits = copy.deepcopy(r.execution_history)
        audits[0]["transcripts"][0]["messages"][0]["response"]["value"] = {"some": 99}
        with self.assertRaises(Fault): replay_host(callback_manifest(), initial(), r.history, audits)
        with self.assertRaises(Fault): replay_host(callback_manifest(), initial(), r.history, r.execution_history * 2)
        records = [copy.deepcopy(r.execution_history[0]["transcripts"])]
        records[0].append(copy.deepcopy(records[0][0]))
        played = HostRuntime(callback_manifest(), initial(), recorded=records)
        before = played.snapshot(); result = played.step(inputs=[open_input()])
        self.assertEqual(result["error"]["code"], "ReplayMismatch"); self.assertEqual(played.snapshot(), before)

    def test_ir_ast_and_js_compute_costs_are_separate(self):
        ir = HostRuntime(control_program("P-C02-create"), initial()); ir.step(inputs=[open_input()])
        js = HostRuntime(callback_manifest(), initial(), runners={"open": DoorMockRunner()}); js.step(inputs=[open_input()])
        self.assertGreater(ir.execution_history[0]["ir_ast_units"], 0)
        self.assertEqual(js.execution_history[0]["ir_ast_units"], 0)
        self.assertIsNone(js.execution_history[0]["js_compute_units"])
        self.assertNotEqual(ir.execution_history[0]["legacy_runtime_units"], js.execution_history[0]["legacy_runtime_units"])

    def test_arbitrary_python_or_js_runner_not_accepted(self):
        invoked = []
        def callback(*args): invoked.append(True)
        with self.assertRaises(Fault): HostRuntime(callback_manifest(), initial(), runners={"open": callback})
        self.assertEqual(invoked, [])

    def test_callback_inputs_do_not_alias_caller_or_snapshot(self):
        events = [open_input()]
        r = HostRuntime(callback_manifest(), initial(), runners={"open": DoorMockRunner()})
        result = r.step(inputs=events)
        events[0]["payload"]["door"][0] = "changed"
        result["events"].clear()
        self.assertEqual(r.history[0]["request"]["inputs"][0]["payload"]["door"], DOOR)
        self.assertTrue(r.execution_history[0]["published_events"])
