import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from headless_v01.examples import cases


class CommandLineTests(unittest.TestCase):
    def call(self, *args):
        result = subprocess.run([sys.executable, "-m", "headless_v01", *map(str, args)], capture_output=True, text=True)
        self.assertFalse(result.stderr, result.stderr)
        return result

    def test_run_replay_inspect(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case = root/"case.json"; case.write_text(json.dumps(cases()["PT11"]))
            result = self.call("run", case, "--output", root/"run"); self.assertEqual(result.returncode, 0, result.stdout)
            result = self.call("replay", case, "--journal", root/"run/journal.json", "--output", root/"replay")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual((root/"run/snapshot.json").read_text(), (root/"replay/snapshot.json").read_text())
            result = self.call("inspect", root/"run/snapshot.json"); self.assertEqual(json.loads(result.stdout)["tick"], 1)

    def test_step_delayed_reply_restore_and_segment_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); fixture = cases()["PT13"]; case = root/"case.json"; case.write_text(json.dumps(fixture))
            result = self.call("step", case, "--output", root/"first"); self.assertEqual(result.returncode, 0, result.stdout)
            request = root/"request.json"; request.write_text(json.dumps(fixture["steps"][1]))
            result = self.call("step", case, "--snapshot", root/"first/snapshot.json", "--request", request, "--output", root/"second")
            self.assertEqual(result.returncode, 0, result.stdout)
            snapshot = json.loads((root/"second/snapshot.json").read_text())
            self.assertEqual([e["values"]["move"] for e in snapshot["entities"] if "move" in e["values"]], ["Accepted", "Accepted", "Blocked"])
            result = self.call("replay", case, "--snapshot", root/"first/snapshot.json", "--journal", root/"second/journal.json", "--output", root/"replay")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual((root/"second/snapshot.json").read_text(), (root/"replay/snapshot.json").read_text())

    def test_abort_resume_requires_same_input_retry_and_driver_persists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); fixture = cases()["PT15"]; case = root/"case.json"; case.write_text(json.dumps(fixture))
            result = self.call("step", case, "--output", root/"first"); self.assertEqual(result.returncode, 2)
            result = self.call("step", case, "--snapshot", root/"first/snapshot.json", "--output", root/"second")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads((root/"second/results.json").read_text())[0]["error"]["code"], "RuntimeHalted")
            request = root/"request.json"; request.write_text(json.dumps({**fixture["steps"][0], "control": "retry"}))
            result = self.call("step", case, "--snapshot", root/"first/snapshot.json", "--request", request, "--output", root/"retry")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads((root/"retry/results.json").read_text())[0]["error"]["code"], "BudgetExceeded")

    def test_malformed_is_structured_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root/"bad.json"; path.write_text('{}')
            result = self.call("run", path, "--output", root/"bad"); self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "ERROR")


if __name__ == "__main__": unittest.main()
