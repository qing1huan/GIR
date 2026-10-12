import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from headless_v01.types import canonical_json
from ark_eval.adapter import EvaluationError
from ark_eval.bridge import decode_data_program, execute_candidate
from ark_eval.build_corpus import corpus
from ark_eval.oracle import SUITE, final_cases, score_authored_control
from ark_eval.protocol import not_run_records, pair, protocol, validate_pair
from ark_eval.reference import control_program, starter
from ark_eval.tasks import TASKS, public_input


class ProtocolTests(unittest.TestCase):
    def test_pairs_match_and_detect_budget_or_info_drift(self):
        arms = pair("P-C02-create"); self.assertTrue(validate_pair(arms))
        for path in ["budget", "model", "api_digest", "runtime_and_oracle"]:
            changed = copy.deepcopy(arms); changed[1]["shared"][path] = "changed"
            with self.assertRaises(EvaluationError): validate_pair(changed)
        changed = copy.deepcopy(arms); changed[1]["messages"][0]["content"] += "extra info"
        with self.assertRaises(EvaluationError): validate_pair(changed)

    def test_both_arms_cannot_silently_change_protocol(self):
        arms = pair("P-C02-create")
        for a in arms: a["shared"]["budget"]["attempts"] = 99
        with self.assertRaises(EvaluationError): validate_pair(arms)
        for malformed in [[None, None], [{"shared": {}}, {"shared": {}}], None]:
            with self.assertRaises(EvaluationError): validate_pair(malformed)

    def test_prompt_construction_does_not_import_oracle_or_reference(self):
        prompts = json.dumps(public_input("P-C02-create", "gameir_text"))
        self.assertNotIn('"expected"', prompts)
        self.assertNotIn('"seed"', prompts)
        self.assertNotIn('"case_id"', prompts)
        self.assertNotIn("final_tests.json", prompts)
        self.assertNotIn('\\"op\\":\\"write\\"', prompts)
        self.assertTrue(all(c["id"] not in prompts for c in final_cases("P-C02-create")))

    def test_modify_debug_starter_required_and_same_semantic_information(self):
        for tid in ["P-C02-modify", "P-C02-debug"]:
            with self.assertRaises(EvaluationError): public_input(tid, "gameir_text")
            arms = pair(tid, starter(tid)); self.assertTrue(validate_pair(arms))
            self.assertEqual(arms[0]["shared"]["starter_digest"], arms[1]["shared"]["starter_digest"])

    def test_data_bridge_shares_exact_compiler_semantics_and_ts_envelope(self):
        text = canonical_json(control_program("P-C02-create"))
        core = decode_data_program(text, "gameir_text")
        for ending in [");", ") satisfies CoreProgram;"]:
            self.assertEqual(core, decode_data_program("export default defineGame(" + text + ending, "wrapper_format"))

    def test_executable_js_and_generated_execution_rejected(self):
        for source in ['require("fs").readFileSync("env.txt")', 'export default defineGame(process.env);',
                       'export default defineGame({}); fetch("https://example.invalid");']:
            with self.assertRaises(EvaluationError): decode_data_program(source, "wrapper_format")
        with self.assertRaisesRegex(EvaluationError, "ISOLATION_UNVERIFIED"): execute_candidate("anything")

    def test_fixed_corpus_reproducible_and_not_holdout(self):
        frozen = json.loads(SUITE.read_text(encoding="utf-8"))
        self.assertEqual(frozen, corpus())
        self.assertFalse(frozen["holdout"])
        self.assertEqual(len(frozen["cases"]), 78)
        self.assertEqual(len({c["id"] for c in frozen["cases"]}), 78)

    def test_authored_controls_and_independent_bug_detection(self):
        for tid in TASKS:
            text = canonical_json(control_program(tid))
            for rep, candidate in [("gameir_text", text), ("wrapper_format", "export default defineGame(" + text + ");")]:
                reports = score_authored_control(tid, candidate, rep)
                self.assertEqual(len(reports), 26)
                self.assertTrue(all(r["status"] == "PASS" for r in reports))
        mutant = canonical_json(control_program("P-C02-debug", inverted=True))
        reports = score_authored_control("P-C02-debug", mutant, "gameir_text")
        self.assertTrue(any(r["status"] == "FAIL" for r in reports))
        empty = canonical_json(__import__("ark_eval.tasks", fromlist=["base_program"]).base_program())
        self.assertTrue(any(r["status"] == "FAIL" for r in score_authored_control("P-C02-create", empty, "gameir_text")))

    def test_unrun_denominator_and_metrics_are_honest(self):
        records = not_run_records(); self.assertEqual(len(records), 6)
        self.assertTrue(all(r["status"] == "NOT_RUN" and r["real_model_calls"] == 0
                            and r["pass_at_1"] is None and r["tokens_in"] is None for r in records))
        p = protocol(); self.assertFalse(p["holdout"])
        self.assertIsNone(p["exact_model_revision"])
        self.assertIn("NOT_GENERAL_JS", p["baseline"])

    def test_cli_preserves_existing_evidence(self):
        from ark_eval.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "keep.txt"; marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(["--output", directory])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_unreviewed_program_is_not_executed_by_control_scorer(self):
        altered = control_program("P-C02-create")
        altered["handlers"][0]["id"] = "unreviewed"
        reports = score_authored_control("P-C02-create", canonical_json(altered), "gameir_text")
        self.assertEqual(len(reports), 26)
        self.assertTrue(all(r["status"] == "NOT_RUN" for r in reports))
