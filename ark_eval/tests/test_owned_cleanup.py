"""Fail-closed tests for the witnessed inspect/top container exit race."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ark_eval.adapter import EvaluationError
from ark_eval.node_backend import NodeBackend, PodmanControlError, WorkerProcess


class OwnedCleanupTests(unittest.TestCase):
    def setUp(self):
        self.uid = patch('ark_eval.container_profile.os.getuid', return_value=1000, create=True)
        self.gid = patch('ark_eval.container_profile.os.getgid', return_value=1000, create=True)
        self.uid.start(); self.gid.start()
        self.addCleanup(self.uid.stop); self.addCleanup(self.gid.stop)
        self.identity = 'a' * 64
        self.data = {'Id': self.identity, 'Image': 'b' * 64,
                     'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                                    'Memory': 67108864, 'PidsLimit': 32, 'LogConfig': {'Type': 'none'},
                                    'SecurityOpt': ['no-new-privileges']},
                     'Config': {'User': '1000:1000', 'Labels': {'gir.reviewed-worker': 'phase3',
                                                              'gir.worker.source': 'c' * 64}},
                     'State': {'Running': True, 'Pid': 123, 'Status': 'running', 'OOMKilled': False,
                               'Paused': False, 'Restarting': False, 'Dead': False}}
        self.exited = copy.deepcopy(self.data)
        self.exited['State'].update(Running=False, Pid=0, Status='exited')

    def worker(self, after=None, top_error=None, rm_error=None):
        backend = SimpleNamespace(worker_image='sha256:' + 'b' * 64, worker_hash='c' * 64,
                                  lock={'repository_digest': 'fixture', 'node_version': 'fixture'},
                                  host={}, disabled=False, inspect=Mock(side_effect=[self.data, after or self.exited]))
        def ctl(command, *args, **kwargs):
            if command == 'top':
                raise top_error or PodmanControlError('top', 125, 'TOP_NOT_RUNNING')
            if command == 'rm' and rm_error: raise rm_error
            return ''
        backend._ctl = Mock(side_effect=ctl)
        worker = WorkerProcess(backend, 10); worker.created = True; worker.container_id = self.identity
        worker.memory_events = Mock(return_value={})
        worker.verify_exited_tree = Mock()
        self.addCleanup(worker.selector.close)
        return worker

    def assert_blocked(self, worker):
        with self.assertRaisesRegex(EvaluationError, 'ISOLATION_CLEANUP_FAILED'): worker.close()
        self.assertTrue(worker.backend.disabled)
        self.assertIsNone(worker.audit['surviving_owned_processes'])
        self.assertTrue(worker.audit['cleanup_errors'])

    def test_verified_exit_race_removes_same_immutable_id(self):
        worker = self.worker(); worker.close()
        self.assertFalse(worker.backend.disabled)
        worker.verify_exited_tree.assert_called_once_with()
        self.assertEqual(worker.backend.inspect.call_args.args, (self.identity,))
        self.assertEqual(worker.backend._ctl.call_args.args, ('rm', '--force', '--time', '1', self.identity))
        self.assertEqual(worker.audit['cleanup_exit_race'], 'VERIFIED_TOP_NOT_RUNNING')
        self.assertEqual(worker.audit['surviving_owned_processes'], 0)

    def test_running_oom_unknown_or_malformed_exit_states_fail_closed(self):
        for key, value in [('Running', True), ('Running', 0), ('Pid', True), ('Pid', 1),
                           ('Status', 'unknown'), ('OOMKilled', True), ('Paused', True), ('Dead', True)]:
            with self.subTest(key=key, value=value):
                after = copy.deepcopy(self.exited); after['State'][key] = value
                worker = self.worker(after); self.assert_blocked(worker)
                worker.verify_exited_tree.assert_not_called()

    def test_changed_id_image_worker_label_or_profile_fail_closed(self):
        for mutation in ('id', 'image', 'label', 'network'):
            with self.subTest(mutation=mutation):
                after = copy.deepcopy(self.exited)
                if mutation == 'id': after['Id'] = 'd' * 64
                if mutation == 'image': after['Image'] = 'd' * 64
                if mutation == 'label': after['Config']['Labels']['gir.worker.source'] = 'd' * 64
                if mutation == 'network': after['HostConfig']['NetworkMode'] = 'host'
                worker = self.worker(after); self.assert_blocked(worker)
                worker.verify_exited_tree.assert_not_called()

    def test_other_top_error_cannot_enter_recovery(self):
        for error in [PodmanControlError('top', 125, 'CONTROL_FAILED'),
                      PodmanControlError('top', 125, 'OUTPUT_LIMIT'),
                      PodmanControlError('top', 1, 'TOP_NOT_RUNNING'),
                      EvaluationError('secret-shaped-untrusted-error')]:
            with self.subTest(error=type(error).__name__):
                worker = self.worker(top_error=error); self.assert_blocked(worker)
                self.assertEqual(worker.backend.inspect.call_count, 1)
                self.assertNotIn('secret-shaped', repr(worker.audit))

    def test_cgroup_failure_still_blocks_removal(self):
        worker = self.worker(); worker.verify_exited_tree.side_effect = PermissionError('private')
        self.assert_blocked(worker)
        self.assertEqual(worker.audit['cleanup_failure']['stage'], 'verify_exit_race')
        self.assertNotIn('private', repr(worker.audit))
        self.assertEqual(worker.backend._ctl.call_count, 1)

    def test_rm_failure_does_not_publish_zero_survivors(self):
        worker = self.worker(rm_error=PodmanControlError('rm', 125, 'CONTROL_FAILED'))
        self.assert_blocked(worker)
        self.assertEqual(worker.audit['cleanup_failure'], {'stage': 'remove_owned', 'exception': 'PodmanControlError',
                         'command': 'rm', 'returncode': 125, 'reason': 'CONTROL_FAILED'})

    def test_normal_exited_cleanup_unchanged(self):
        worker = self.worker(); worker.backend.inspect.side_effect = [self.exited]; worker.close()
        worker.verify_exited_tree.assert_not_called()
        self.assertEqual(worker.backend._ctl.call_count, 1)
        self.assertEqual(worker.audit['surviving_owned_processes'], 0)

    def test_normal_running_cleanup_checks_process_identities(self):
        worker = self.worker(); worker.backend._ctl.side_effect = lambda *a, **k: 'HPID\n123' if a[0] == 'top' else ''
        with patch('ark_eval.node_backend.process_identity', return_value=None): worker.close()
        self.assertEqual(worker.audit['terminated_tree_processes'], 1)
        self.assertEqual(worker.audit['surviving_owned_processes'], 0)
        self.assertNotIn('cleanup_exit_race', worker.audit)

    def test_exact_cli_text_and_bounded_output_required(self):
        backend = object.__new__(NodeBackend); backend.environment = {}
        for code, stderr, stdout, expected in [
            (125, b'Error: top can only be used on running containers\n', b'', 'TOP_NOT_RUNNING'),
            (125, b'permission denied secret', b'', 'CONTROL_FAILED'),
            (1, b'Error: top can only be used on running containers\n', b'', 'CONTROL_FAILED'),
            (125, b'Error: top can only be used on running containers\n', b'x' * 1048577, 'OUTPUT_LIMIT'),
            (125, b'x' * 65537, b'', 'OUTPUT_LIMIT')]:
            with self.subTest(code=code, expected=expected):
                result = SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
                with patch('ark_eval.node_backend.subprocess.run', return_value=result):
                    with self.assertRaises(PodmanControlError) as caught: backend._ctl('top', self.identity, 'hpid')
                self.assertEqual(caught.exception.reason, expected)
                self.assertNotIn('secret', repr(caught.exception.__dict__))

    def bound_cgroup(self):
        worker = self.worker(); del worker.verify_exited_tree
        worker.cgroup = Path('/sys/fs/cgroup/owned-scope')
        return worker

    def test_populated_zero_verifies_entire_subtree(self):
        worker = self.bound_cgroup()
        with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', return_value='populated 0\nfrozen 0\n'):
            worker.verify_exited_tree()
        self.assertEqual(worker.audit['exit_race_cgroup'], 'SUBTREE_POPULATED_ZERO')

    def test_populated_nonzero_or_missing_value_fail_closed(self):
        for text in ['populated 1\n', 'frozen 0\n', 'populated unknown\n']:
            worker = self.bound_cgroup()
            with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', return_value=text):
                with self.assertRaises(EvaluationError): worker.verify_exited_tree()

    def test_removed_bound_scope_requires_readable_parent_and_absent_entry(self):
        worker = self.bound_cgroup()
        with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', side_effect=FileNotFoundError), \
             patch.object(Path, 'lstat', side_effect=FileNotFoundError), patch.object(Path, 'is_dir', return_value=True), \
             patch.object(Path, 'iterdir', return_value=iter([])):
            worker.verify_exited_tree()
        self.assertEqual(worker.audit['exit_race_cgroup'], 'BOUND_SCOPE_REMOVED')

    def test_removed_container_child_and_systemd_scope_verified_at_existing_ancestor(self):
        worker = self.bound_cgroup(); worker.cgroup = Path('/sys/fs/cgroup/user.slice/libpod-owned.scope/container')
        def lstat(path):
            if path.name in ('container', 'libpod-owned.scope'): raise FileNotFoundError
            return object()
        with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', side_effect=FileNotFoundError), \
             patch.object(Path, 'lstat', lstat), patch.object(Path, 'is_dir', return_value=True), \
             patch.object(Path, 'iterdir', return_value=iter([])):
            worker.verify_exited_tree()
        self.assertEqual(worker.audit['exit_race_cgroup'], 'BOUND_SCOPE_REMOVED')

    def test_events_missing_from_existing_scope_or_parent_unreadable_rejected(self):
        worker = self.bound_cgroup()
        with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', side_effect=FileNotFoundError), \
             patch.object(Path, 'lstat', return_value=object()):
            with self.assertRaises(EvaluationError): worker.verify_exited_tree()
        with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', side_effect=FileNotFoundError), \
             patch.object(Path, 'lstat', side_effect=FileNotFoundError), patch.object(Path, 'is_dir', return_value=True), \
             patch.object(Path, 'iterdir', side_effect=PermissionError):
            with self.assertRaises(PermissionError): worker.verify_exited_tree()

    def test_cgroup_outside_root_or_symlink_drift_rejected(self):
        for path in [None, Path('/sys/fs/cgroup'), Path('/tmp/outside')]:
            worker = self.bound_cgroup(); worker.cgroup = path
            with patch.object(Path, 'resolve', lambda p, **kw: p):
                with self.assertRaises(EvaluationError): worker.verify_exited_tree()
        worker = self.bound_cgroup()
        with patch.object(Path, 'resolve', lambda p, **kw: p if p == Path('/sys/fs/cgroup') else Path('/tmp/drift')):
            with self.assertRaises(EvaluationError): worker.verify_exited_tree()

    def test_removed_branch_reappearing_at_ancestor_is_not_empty(self):
        worker = self.bound_cgroup()
        with patch.object(Path, 'resolve', lambda p, **kw: p), patch.object(Path, 'read_text', side_effect=FileNotFoundError), \
             patch.object(Path, 'lstat', side_effect=FileNotFoundError), patch.object(Path, 'is_dir', return_value=True), \
             patch.object(Path, 'iterdir', return_value=iter([worker.cgroup])):
            with self.assertRaisesRegex(EvaluationError, 'OWNED_CGROUP_UNVERIFIED'): worker.verify_exited_tree()
