import copy
import json
import unittest
from unittest.mock import patch

from headless_v01.types import Fault
from ark_eval.adapter import EvaluationError
from ark_eval.callback_verify import callback_manifest
from ark_eval.container_profile import flags, image_lock, clean_environment, verify_inspection
from ark_eval.host import DoorMockRunner, HostRuntime, replay_host
from ark_eval.js_runner import IsolatedJSRunner, execute_candidate_source
from ark_eval.node_backend import NodeBackend
from ark_eval.reviewed_js import source
from ark_eval.tasks import PLAYER, DOOR


class IsolatedRunnerGateTests(unittest.TestCase):
    def test_unreviewed_catalog_entry_blocked_before_engine_work(self):
        backend = object.__new__(NodeBackend)
        for unknown in ['export function onOpen(ctx, {player, door}) {}', 'model-output', '../file', None, {}, []]:
            with self.assertRaisesRegex(EvaluationError, 'UNREVIEWED_SOURCE_BLOCKED'):
                IsolatedJSRunner(unknown, backend)

    def test_arbitrary_source_public_entry_cannot_be_enabled(self):
        with self.assertRaisesRegex(EvaluationError, 'UNREVIEWED_SOURCE_BLOCKED'):
            execute_candidate_source(source('create'), enable=True)

    def test_impostor_backend_and_extended_timeout_rejected(self):
        with self.assertRaises(EvaluationError): IsolatedJSRunner('create', object())
        backend = object.__new__(NodeBackend)
        for timeout in [0,11,True,float('inf')]:
            with self.assertRaises(EvaluationError): IsolatedJSRunner('create',backend,timeout)

    def test_container_name_shell_injection_rejected(self):
        for name in ['worker;echo x','../../x','--privileged','gir-node-'+('x'*32)]:
            with self.assertRaises(EvaluationError): flags(name)

    def test_rootful_launch_rejected(self):
        with patch('ark_eval.container_profile.sys.platform','linux'), patch('ark_eval.container_profile.os.getuid',return_value=0,create=True):
            with self.assertRaisesRegex(EvaluationError,'ROOTLESS_LINUX_REQUIRED'): clean_environment()

    def test_official_digest_cannot_be_replaced_by_other_registry(self):
        lock=image_lock(); lock['repository_digest']='example.invalid/node@sha256:'+'a'*64
        with patch('ark_eval.container_profile.Path.read_text',return_value=json.dumps(lock)):
            with self.assertRaisesRegex(EvaluationError,'INVALID_NODE_IMAGE_LOCK'):image_lock()

    def test_actual_profile_drift_network_memory_mount_and_pid_rejected(self):
        baseline={'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,
                               'Binds':None,'Memory':67108864,'PidsLimit':32,'LogConfig':{'Type':'none'},
                               'PidMode':'private','IpcMode':'private','SecurityOpt':['no-new-privileges']},
                  'Config':{'User':'1000:1000','Labels':{'gir.reviewed-worker':'phase3'}},'Mounts':[]}
        with patch('ark_eval.container_profile.os.getuid',return_value=1000,create=True), patch('ark_eval.container_profile.os.getgid',return_value=1000,create=True):
            verify_inspection(baseline)
            for field,value in [('NetworkMode','host'),('ReadonlyRootfs',False),('Memory',0),('PidMode','host'),('PidsLimit',0),('Binds',['/host:/data']),('LogConfig',{'Type':'k8s-file'})]:
                bad=copy.deepcopy(baseline);bad['HostConfig'][field]=value
                with self.assertRaises(EvaluationError):verify_inspection(bad)

    def test_replay_excludes_physical_counter_but_not_logical_cost_or_messages(self):
        initial=[{'id':PLAYER,'values':{'keys':2}},{'id':DOOR,'values':{'open':False}}]
        runtime=HostRuntime(callback_manifest(),initial,runners={'open':DoorMockRunner()})
        runtime.step(inputs=[{'name':'Open','payload':{'player':PLAYER,'door':DOOR}}])
        audits=copy.deepcopy(runtime.execution_history)
        audits[0].update(mode='ISOLATED_REVIEWED_JS_CALLBACK_HOST',source_executions=1)
        played=replay_host(callback_manifest(),initial,runtime.history,audits)
        self.assertEqual(played.snapshot(),runtime.snapshot())
        self.assertEqual(played.execution_history[0]['source_executions'],0)
        audits[0]['host_operations_total']+=1
        with self.assertRaises(Fault):replay_host(callback_manifest(),initial,runtime.history,audits)

