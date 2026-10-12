import dataclasses
import json
import unittest

from ark_eval.adapter import (EvaluationError, MODEL, PLAN_URL, OfflinePlanAdapter,
                              PlanConfig, SimulatedTransport, bounded_json, import_response, prepare)
from ark_eval.budget import Budget, Ledger, verify_chain
from ark_eval.offline import simulate_attempt


def response(content="untrusted", model=MODEL, tin=12, tout=3, finish="stop"):
    return json.dumps({"model": model, "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": finish}], "usage": {"prompt_tokens": tin, "completion_tokens": tout,
                     "total_tokens": tin + tout}})


class AdapterTests(unittest.TestCase):
    def test_plan_only_route_and_model(self):
        for route in ["https://ark.cn-beijing.volces.com/api/v3", PLAN_URL + "/", PLAN_URL + "?fallback=1"]:
            with self.assertRaises(EvaluationError): PlanConfig(route)
        with self.assertRaises(EvaluationError): PlanConfig(model="ark-code-latest")

    def test_live_send_unconditionally_disabled(self):
        with self.assertRaisesRegex(EvaluationError, "LIVE_TRANSPORT_DISABLED"):
            OfflinePlanAdapter().send(prepare([{"role": "user", "content": "x"}]))

    def test_request_is_immutable_and_has_no_auth(self):
        msgs = [{"role": "user", "content": "测试"}]
        req = prepare(msgs)
        msgs[0]["content"] = "changed"
        body = req.body
        body["messages"][0]["content"] = "also changed"
        self.assertEqual(req.body["messages"][0]["content"], "测试")
        self.assertEqual(set(req.body), {"model", "messages", "max_tokens", "temperature", "stream"})

    def test_extra_prompt_fields_and_tools_rejected(self):
        for messages in [[], [{"role": ["user"], "content": "x"}], [{"role": "tool", "content": "x"}], [{"role": "user", "content": "x", "api_key": "synthetic"}]]:
            with self.assertRaises(EvaluationError): prepare(messages)

    def test_oversize_and_invalid_unicode_rejected(self):
        for raw in ["x" * 1048577, "\ud800"]:
            with self.assertRaises(EvaluationError): bounded_json(raw)
        with self.assertRaises(EvaluationError): prepare([{"role": "user", "content": "\ud800"}])

    def test_duplicate_keys_nonfinite_and_deep_json_rejected(self):
        for raw in ['{"usage":1,"usage":2}', '{"x":NaN}', '[' * 1100 + '0' + ']' * 1100]:
            with self.assertRaises(EvaluationError): bounded_json(raw)

    def test_usage_required_exact_integer_and_consistent(self):
        data = json.loads(response())
        del data["usage"]
        with self.assertRaises(EvaluationError): import_response(json.dumps(data))
        for value in [True, -1, 1.5, "12"]:
            data = json.loads(response()); data["usage"]["prompt_tokens"] = value
            with self.assertRaises(EvaluationError): import_response(json.dumps(data))
        data = json.loads(response()); data["usage"]["total_tokens"] += 1
        with self.assertRaisesRegex(EvaluationError, "INCONSISTENT_USAGE"): import_response(json.dumps(data))

    def test_errors_and_tool_requests_do_not_leak_text(self):
        marker = "SYNTHETIC_SECRET_MARKER_NOT_A_CREDENTIAL"
        for data in [{"error": {"message": marker}}, {"model": MODEL, "choices": [{"message": {
            "role": "assistant", "content": marker, "tool_calls": [{"type": "function"}]}}]}]:
            with self.assertRaises(EvaluationError) as caught: import_response(json.dumps(data))
            self.assertNotIn(marker, str(caught.exception))

    def test_model_identity_and_truncation_retained(self):
        result = import_response(response(model="unexpected-model", finish="length"))
        self.assertFalse(result.model_matches_requested)
        self.assertEqual(result.finish_reason, "length")


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.request = prepare([{"role": "user", "content": "x"}], 10)

    def test_reservation_precedes_transport_and_exhaustion_prevents_call(self):
        ledger = Ledger(Budget(prompt_units=10))
        transport = SimulatedTransport([response()])
        with self.assertRaises(EvaluationError): simulate_attempt(ledger, self.request, transport)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(ledger.export()["events"][0]["status"], "NOT_RUN")

    def test_success_usage_release_and_no_candidate_logging(self):
        ledger = Ledger()
        marker = "SYNTHETIC_SECRET_MARKER_NOT_A_CREDENTIAL"
        simulate_attempt(ledger, self.request, SimulatedTransport([response(marker)]))
        audit = ledger.export()
        self.assertEqual(audit["charged"]["prompt_units"], 12)
        self.assertEqual(audit["charged"]["output_tokens"], 3)
        self.assertEqual(audit["events"][-1]["status"], "RESPONSE_IMPORTED")
        self.assertNotIn(marker, json.dumps(audit))
        self.assertTrue(verify_chain(audit["events"]))

    def test_unknown_usage_charged_fully_and_repair_counted(self):
        ledger = Ledger()
        first = ledger.reserve(self.request)
        ledger.finish(first, failure="TIMEOUT")
        self.assertEqual(ledger.export()["charged"]["prompt_units"], self.request.prompt_units)
        with self.assertRaises(EvaluationError): ledger.reserve(self.request)
        second = ledger.reserve(self.request, repair=True)
        ledger.finish(second, failure="TRANSPORT")
        self.assertEqual(ledger.export()["charged"]["repairs"], 1)
        self.assertEqual(ledger.export()["charged"]["attempts"], 2)

    def test_attempt_and_repair_budgets_have_no_automatic_retries(self):
        ledger = Ledger(Budget(attempts=1, repairs=0))
        simulate_attempt(ledger, self.request, SimulatedTransport(['{"error":{}}']))
        with self.assertRaises(EvaluationError): ledger.reserve(self.request, repair=True)
        self.assertEqual(ledger.export()["charged"]["attempts"], 1)

    def test_pending_and_duplicate_outcomes_rejected(self):
        ledger = Ledger(); call = ledger.reserve(self.request)
        with self.assertRaises(EvaluationError): ledger.reserve(self.request, repair=True)
        with self.assertRaises(EvaluationError): ledger.finish(call + 1, failure="TIMEOUT")
        with self.assertRaises(EvaluationError): ledger.finish(True, failure="TIMEOUT")
        ledger.finish(call, failure="TIMEOUT")
        with self.assertRaises(EvaluationError): ledger.finish(call, failure="TIMEOUT")

    def test_quota_and_auth_stop_all_actions(self):
        for code in ["QUOTA", "AUTH"]:
            ledger = Ledger(); call = ledger.reserve(self.request)
            ledger.finish(call, failure=code)
            self.assertTrue(ledger.export()["halted"])
            with self.assertRaises(EvaluationError): ledger.reserve(self.request, repair=True)
            with self.assertRaises(EvaluationError): ledger.tool_call()

    def test_excess_usage_and_model_drift_halt_without_refund(self):
        for raw in [response(tin=99999), response(tout=11), response(model="drift")]:
            ledger = Ledger(); call = ledger.reserve(self.request)
            ledger.finish(call, response=import_response(raw))
            audit = ledger.export()
            self.assertTrue(audit["halted"])
            self.assertEqual(audit["charged"]["output_tokens"], max(10, json.loads(raw)["usage"]["completion_tokens"]))
            self.assertEqual(audit["events"][-1]["status"], "FAIL")

    def test_tampered_request_cannot_underreserve(self):
        ledger = Ledger()
        for req in [dataclasses.replace(self.request, prompt_units=0),
                    dataclasses.replace(self.request, max_output_tokens=1),
                    dataclasses.replace(self.request, request_digest="forged")]:
            with self.assertRaises(EvaluationError): ledger.reserve(req)
        self.assertEqual(ledger.export()["charged"]["attempts"], 0)

    def test_tool_budget_and_export_mutation(self):
        ledger = Ledger(Budget(tool_calls=1)); ledger.tool_call()
        with self.assertRaises(EvaluationError): ledger.tool_call()
        audit = ledger.export(); audit["charged"]["tool_calls"] = 0
        self.assertEqual(ledger.export()["charged"]["tool_calls"], 1)

    def test_audit_tamper_detection(self):
        ledger = Ledger(); call = ledger.reserve(self.request); ledger.finish(call, failure="TIMEOUT")
        audit = ledger.export(); audit["events"][0]["charges"]["attempts"] = 0
        self.assertFalse(verify_chain(audit["events"]))

    def test_arbitrary_transport_is_not_invoked(self):
        invoked = []
        def network_like(*args): invoked.append(1)
        with self.assertRaises(EvaluationError): simulate_attempt(Ledger(), self.request, network_like)
        self.assertEqual(invoked, [])

    def test_invalid_budget_and_outcome_rejected(self):
        with self.assertRaises(EvaluationError): Budget(attempts=True)
        ledger = Ledger(); call = ledger.reserve(self.request)
        with self.assertRaises(EvaluationError): ledger.finish(call, failure="arbitrary provider message")
        with self.assertRaises(EvaluationError): ledger.finish(call)
        self.assertEqual(ledger.export()["pending_call"], call)

    def test_provider_quota_auth_import_halts_without_leaking(self):
        for code in ["QuotaExceeded", "invalid_api_key"]:
            ledger = Ledger()
            raw = json.dumps({"error": {"code": code, "message": "SYNTHETIC_PRIVATE_TEXT"}})
            self.assertIsNone(simulate_attempt(ledger, self.request, SimulatedTransport([raw])))
            self.assertTrue(ledger.export()["halted"])
            self.assertNotIn("SYNTHETIC_PRIVATE_TEXT", json.dumps(ledger.export()))

    def test_forged_response_cannot_write_plaintext_audit_digest(self):
        ledger = Ledger(); call = ledger.reserve(self.request)
        forged = dataclasses.replace(import_response(response()), candidate_digest="SYNTHETIC_PRIVATE_TEXT")
        with self.assertRaises(EvaluationError): ledger.finish(call, response=forged)
        self.assertNotIn("SYNTHETIC_PRIVATE_TEXT", json.dumps(ledger.export()))
        self.assertEqual(ledger.export()["pending_call"], call)
