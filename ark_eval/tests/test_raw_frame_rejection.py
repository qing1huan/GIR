import copy
import unittest
from unittest.mock import patch

from headless_v01.types import Fault
from ark_eval.host import MockClient, replay_host
from ark_eval.tests.test_callback_host import session


class InvalidRawReplayTests(unittest.TestCase):
    def test_duplicate_key_is_latched_and_diagnostic_does_not_store_raw(self):
        _, s = session()
        raw = '{"v":"trusted-ctx@1","v":"other"}'
        with self.assertRaises(Fault) as caught: s.receive(raw)
        self.assertEqual(caught.exception.code, 'ProtocolViolation')
        diagnostic = s.trace['messages'][-1]
        self.assertIsNone(diagnostic['request'])
        self.assertEqual(diagnostic['rejection'], {'kind':'INVALID_RAW_FRAME','parser_code':'DUPLICATE_JSON_KEY'})
        self.assertNotIn(raw, str(diagnostic))
        with self.assertRaises(Fault) as latched: MockClient(s).done()
        self.assertIs(latched.exception, s.failed)
        self.assertFalse(s.closed)

    def test_uncanonicalized_record_replay_is_rejected_before_runtime_construction(self):
        records = [{'transcripts':[{'messages':[{'request':None,'response':{'kind':'error','code':'ProtocolViolation'}}]}]}]
        unchanged = copy.deepcopy(records)
        with patch('ark_eval.host.HostRuntime', side_effect=AssertionError('must not construct runtime')):
            with self.assertRaises(Fault) as caught: replay_host({}, [], [{}], records)
        self.assertEqual(caught.exception.code, 'ReplayUnsupported')
        self.assertEqual(caught.exception.details, {'reason':'uncanonicalized_raw_frame'})
        self.assertEqual(records, unchanged)

    def test_length_gate_still_precedes_replay_inspection(self):
        for value in (None, {}, 'invalid'):
            with self.assertRaises(Fault) as caught: replay_host({}, [], [], value)
            self.assertEqual(caught.exception.code, 'ReplayMismatch')
