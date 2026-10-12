"""Trusted IR controls run locally. Native JS evidence uses the isolated CLI."""
import unittest
from ark_eval.pilot24_authored import TASKS,ROOT
from ark_eval.pilot24_materials import read
from ark_eval.pilot24_trusted import check_control

class TrustedIRTests(unittest.TestCase):
    def test_all_v0_IR_controls_48_cases(self):
        for task in TASKS:
            for case in read(ROOT/'private'/task/'v0_cases.json'):
                with self.subTest(task=task,case=case['id']):
                    self.assertEqual(check_control(task,0,'gameir_text',case)['status'],'PASS')

    def test_all_v1_IR_controls_72_cases(self):
        for task in TASKS:
            for case in read(ROOT/'private'/task/'cases.json'):
                with self.subTest(task=task,case=case['id']):
                    self.assertEqual(check_control(task,1,'gameir_text',case)['status'],'PASS')

    def test_all_18_IR_negative_controls_detected(self):
        for task in TASKS:
            cases={c['id']:c for c in read(ROOT/'private'/task/'cases.json')}
            for defect in read(ROOT/'private'/task/'mutants/index.json'):
                with self.subTest(task=task,defect=defect['intent']):
                    row=check_control(task,1,'gameir_text',cases[defect['case_id']],mutant=defect['index'])
                    self.assertFalse(row['oracle_match']);self.assertTrue(row['replay_match'])

if __name__=='__main__':unittest.main()
