"""Only temporary public fixture files; HTTP is always mocked."""
import io
import json
import stat
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ark_eval.adapter import EvaluationError
from ark_eval.contract_v3 import ROOT
from ark_eval.local_key_file import MAX_FILE_BYTES,read_local_key
from ark_eval.manual_pair_v3_1 import main
from ark_eval.tests.test_create_pair import body

FIXTURE='public-fixture-file-not-a-key'


class LocalKeyFileTests(unittest.TestCase):
    def test_plain_utf8_optional_bom_and_one_terminal_newline(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            path=Path(root)/'fixture.key'
            for prefix in (b'',b'\xef\xbb\xbf'):
                for suffix in (b'',b'\n',b'\r\n'):
                    path.write_bytes(prefix+FIXTURE.encode()+suffix)
                    self.assertEqual(read_local_key(path,project_root=ROOT),FIXTURE)

    def test_multiple_lines_whitespace_quotes_assignment_and_invalid_utf8_rejected(self):
        payloads=[b'',b'short',FIXTURE.encode()+b'\n\n',FIXTURE.encode()+b'\nsecond-line',
                  b' '+FIXTURE.encode(),FIXTURE.encode()+b' ',FIXTURE.encode()+b'\r',
                  ('"'+FIXTURE+'"').encode(),('ARK_API_KEY='+FIXTURE).encode(),b'\xff',
                  ('\u4e0d\u662f\u5bc6\u94a5'+FIXTURE).encode(),b'x'*(MAX_FILE_BYTES+1)]
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            path=Path(root)/'fixture.key'
            for payload in payloads:
                path.write_bytes(payload)
                with self.assertRaises(EvaluationError) as caught:read_local_key(path,project_root=ROOT)
                self.assertNotIn(FIXTURE,str(caught.exception))
                self.assertTrue(str(caught.exception).startswith('KEY_FILE_'))

    def test_exact_length_limit_and_missing_file_fixed_error(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            path=Path(root)/'fixture.key'
            path.write_bytes(b'\xef\xbb\xbf'+b'x'*4096+b'\r\n')
            self.assertEqual(len(read_local_key(path,project_root=ROOT)),4096)
            path.write_bytes(b'x'*4097)
            with self.assertRaisesRegex(EvaluationError,'INVALID_FORMAT'):read_local_key(path,project_root=ROOT)
            with self.assertRaisesRegex(EvaluationError,'^KEY_FILE_NOT_FOUND$'):
                read_local_key(Path(root)/'missing.key',project_root=ROOT)

    def test_project_env_and_git_storage_rejected_before_content_open(self):
        with patch.object(Path,'open') as opened:
            for path in (ROOT/'fixture-only.key',ROOT/'env.txt'):
                with self.assertRaisesRegex(EvaluationError,'UNSAFE_LOCATION'):read_local_key(path,project_root=ROOT)
            opened.assert_not_called()
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            checkout=Path(root)/'checkout';checkout.mkdir();(checkout/'.git').mkdir()
            path=checkout/'fixture.key';path.write_text(FIXTURE)
            with patch.object(Path,'open') as opened:
                with self.assertRaisesRegex(EvaluationError,'GIT_LOCATION_REJECTED'):read_local_key(path,project_root=ROOT)
                opened.assert_not_called()

    def test_git_worktree_marker_and_symlink_rejected_before_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            checkout=Path(root)/'worktree';checkout.mkdir();(checkout/'.git').write_text('public fixture marker')
            path=checkout/'fixture.key';path.write_text(FIXTURE)
            with patch.object(Path,'open') as opened:
                with self.assertRaisesRegex(EvaluationError,'GIT_LOCATION_REJECTED'):read_local_key(path,project_root=ROOT)
                opened.assert_not_called()
            with patch.object(Path,'is_symlink',return_value=True),patch.object(Path,'open') as opened:
                with self.assertRaisesRegex(EvaluationError,'UNSAFE_LOCATION'):read_local_key(path,project_root=ROOT)
                opened.assert_not_called()

    def test_ancestor_windows_reparse_point_rejected_before_content_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            path=Path(root)/'fixture.key';path.write_text(FIXTURE)
            original=Path.lstat
            def metadata(component,*args,**kwargs):
                info=original(component,*args,**kwargs)
                if component==path.parent:
                    return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                return info
            with patch.object(Path,'lstat',metadata),patch.object(Path,'open') as opened:
                with self.assertRaisesRegex(EvaluationError,'UNSAFE_LOCATION'):read_local_key(path,project_root=ROOT)
                opened.assert_not_called()

    def test_offline_mode_with_key_file_never_reads_it_or_sends(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root,patch('ark_eval.manual_pair_v3_1.read_local_key') as read,patch('ark_eval.manual_pair._https_once') as net,patch('sys.stdout',new=io.StringIO()):
            output=Path(root)/'prepared'
            self.assertEqual(main(['--key-file',str(Path(root)/'not-created.key'),'--output',str(output)]),2)
            read.assert_not_called();net.assert_not_called();self.assertFalse(output.exists())

    def test_missing_plan_terminal_or_cancelled_confirmation_never_reads_file(self):
        for plan,interactive,confirm in [(False,True,'SEND_PAIR003'),(True,False,'SEND_PAIR003'),(True,True,'cancel')]:
            with tempfile.TemporaryDirectory(dir=ROOT.parent) as root,patch('sys.stdin.isatty',return_value=interactive),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=interactive),patch('builtins.input',return_value=confirm),patch('ark_eval.manual_pair_v3_1.read_local_key') as read,patch('ark_eval.manual_pair._https_once') as net:
                args=['--user-local-send-pair','--key-file',str(Path(root)/'not-created.key'),'--output',str(Path(root)/'pair')]
                if plan:args.append('--plan-status-confirmed')
                self.assertEqual(main(args),2);read.assert_not_called();net.assert_not_called()

    def test_confirmed_fixture_file_exact_requests_and_no_secret_in_output_or_artifacts(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            path=Path(root)/'fixture.key';path.write_bytes(b'\xef\xbb\xbf'+FIXTURE.encode()+b'\r\n')
            output=Path(root)/'pair'
            order=[]
            from ark_eval.manual_pair_v3_1 import load_review_requests
            _,requests=load_review_requests()
            def confirmation(_):order.append('confirmed');return 'SEND_PAIR003'
            def file_read(*args,**kwargs):
                self.assertEqual(order,['confirmed']);order.append('file-read')
                return read_local_key(*args,**kwargs)
            with patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('builtins.input',side_effect=confirmation),patch('ark_eval.manual_pair_v3_1.read_local_key',side_effect=file_read),patch('getpass.getpass') as hidden,patch('ark_eval.manual_pair._https_once',side_effect=[(200,body()),(200,body())]) as net:
                self.assertEqual(main(['--user-local-send-pair','--plan-status-confirmed','--key-file',str(path),'--output',str(output)]),0)
                self.assertEqual(order,['confirmed','file-read']);hidden.assert_not_called();self.assertEqual(net.call_count,2)
                self.assertNotIn(FIXTURE,stdout.getvalue())
                for call,(_,request) in zip(net.call_args_list,requests):self.assertEqual(call.args[0],request)
            for p in output.rglob('*'):
                if p.is_file():self.assertNotIn(FIXTURE.encode(),p.read_bytes())
            summary=json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['generated_source_executions'],0)

    def test_confirmed_missing_file_reports_only_fixed_code_and_never_sends(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root,patch('sys.stdin.isatty',return_value=True),patch('sys.stdout',new=io.StringIO()) as stdout,patch.object(stdout,'isatty',return_value=True),patch('builtins.input',return_value='SEND_PAIR003'),patch('ark_eval.manual_pair._https_once') as net:
            self.assertEqual(main(['--user-local-send-pair','--plan-status-confirmed','--key-file',str(Path(root)/'missing.key'),'--output',str(Path(root)/'pair')]),2)
            net.assert_not_called();self.assertIn('KEY_FILE_NOT_FOUND',stdout.getvalue())
            self.assertNotIn('missing.key',stdout.getvalue())

    def test_permission_error_never_exposes_os_exception_contents(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as root:
            path=Path(root)/'fixture.key';path.write_text(FIXTURE)
            with patch.object(Path,'open',side_effect=PermissionError(FIXTURE)):
                with self.assertRaisesRegex(EvaluationError,'^KEY_FILE_UNREADABLE_OR_INVALID$') as caught:
                    read_local_key(path,project_root=ROOT)
                self.assertNotIn(FIXTURE,str(caught.exception))


if __name__=='__main__':unittest.main()
