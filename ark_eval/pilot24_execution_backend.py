"""Thin exact-task adapters over unchanged NodeBackend/HostRuntime transport."""
import hashlib
import io
import json
import subprocess
import tarfile
import time

from headless_v01.compiler import digest
from headless_v01.types import Fault
from .adapter import EvaluationError, bounded_json
from .host import FAULTS, HostRuntime, MockClient
from .node_backend import NodeBackend
from .pilot24_public_payload import frozen_public_payload
from .pilot24_execution_scope import (ROOT, ApprovedPilot24Scope, TrustedPilotControl,
    FreshPilotIsolation, current_isolation, require_scope, require, read_bytes, parse_status)
from .pilot24_execution_scope import read, PUBLIC_RUNTIME


def ir_config(scope, slot_id):
    require_scope(scope)
    slot = scope.slot(slot_id)
    require(slot['representation'] == 'gameir_text' and slot['static_rejection'] is None,
            'P24_IR_SLOT_REJECTED')
    return {'source_sha256': slot['source_sha256'], 'abi': scope.abi(slot_id),
            'cases': {c['id']: digest({k: c[k] for k in ('id', 'initial', 'seed', 'steps')})
                      for c in scope.cases[slot['task_id']]}}


def require_public_package(files):
    actual = {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())}
    require(actual == read(PUBLIC_RUNTIME)['files_sha256'], 'P24_PUBLIC_RUNTIME_PACKAGE_DRIFT')


def pilot_payload(scope, slot_id, environment):
    config = ir_config(scope, slot_id)
    files = frozen_public_payload()
    require_public_package(files)
    files['gir-pilot24-config.json'] = json.dumps(config, sort_keys=True, separators=(',', ':')).encode()
    return files


class Pilot24IRBackend(NodeBackend):
    def __init__(self, scope, slot_id):
        require_scope(scope)
        scope.source(slot_id)
        self.scope, self.slot_id = scope, slot_id
        self.config = ir_config(scope, slot_id)
        super().__init__()

    def _build(self):
        # Same content-addressed packaging as v4; transport/profile is inherited.
        files = pilot_payload(self.scope, self.slot_id, self.environment)
        self.payload_hashes = {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())}
        fp = hashlib.sha256((json.dumps(self.payload_hashes, sort_keys=True) +
                             self.lock['repository_digest']).encode()).hexdigest()
        self.worker_hash = fp
        tag = 'localhost/gir-pilot24-ir:' + fp[:24]
        found = subprocess.run(['/usr/bin/podman', 'image', 'exists', tag], capture_output=True,
                               env=self.environment, cwd='/', timeout=20)
        require(found.returncode in (0, 1), 'P24_IMAGE_QUERY_FAILED')
        if found.returncode == 1:
            files['Containerfile'] = ('FROM ' + self.lock['repository_digest'] +
                '\nCOPY . /\nLABEL gir.worker.source="' + fp +
                '"\nENTRYPOINT ["/bin/sh", "/gir-supervisor.sh"]\n').encode()
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as tar:
                for n, b in files.items():
                    item = tarfile.TarInfo(n)
                    item.size = len(b)
                    item.mode = 0o555 if n in {'gir-python/bin/python3.12',
                                              'gir-python/lib/ld-linux-x86-64.so.2'} else 0o444
                    tar.addfile(item, io.BytesIO(b))
            self._ctl('build', '--pull=never', '--network=none', '-t', tag, '-',
                      data=archive.getvalue(), timeout=90)
        image = json.loads(self._ctl('image', 'inspect', tag))[0]
        require(image['Labels'].get('gir.worker.source') == fp, 'P24_IMAGE_DRIFT')
        return 'sha256:' + image['Id'].removeprefix('sha256:')


def ir_call(scope, slot_id, backend, proof, case=None, refresh=None):
    require(type(backend) is Pilot24IRBackend and backend.scope is scope and
            backend.slot_id == slot_id and type(proof) is FreshPilotIsolation and
            proof.scope is scope and proof.rep == 'gameir_text', 'P24_IR_ADMISSION_REJECTED')
    if case is not None:
        scope.case(slot_id, case)
    source = scope.source(slot_id)
    source_sha256 = scope.slot(slot_id)['source_sha256']
    abi_digest = digest(scope.abi(slot_id))
    proof = current_isolation(proof, backend, refresh)
    worker = backend.invocation(10)
    result = None
    try:
        worker.open()
        worker.write({'v': 'gir-pair003-ir@1', 'purpose': 'ir_case', 'source': source,
                      'case': None if case is None else {k: case[k] for k in ('id', 'initial', 'seed', 'steps')}},
                     bootstrap=True)
        frame = bounded_json(worker.read_line())
        require(set(frame) == {'transport', 'result'} and
                frame['transport'] == 'gir-pilot24-ir-result@1', 'P24_IR_PROTOCOL_REJECTED')
        result = frame['result']
        # Finish the bounded worker transport before doing host result validation.
        worker.release(bounded_json(worker.read_line()))
        require(result['source_sha256'] == source_sha256 and
                result['abi_digest'] == abi_digest and
                result['case_id'] == (None if case is None else case['id']) and
                result['source_modified'] is False and result['admission'] in {'PARSED', 'REJECTED'},
                'P24_IR_RESULT_BINDING_REJECTED')
        executed = int(case is not None and result['admission'] == 'PARSED')
        require(type(result['candidate_case_executions']) is int and
                result['candidate_case_executions'] == executed and
                type(result['IR_case_replays']) is int and result['IR_case_replays'] == executed,
                'P24_IR_RESULT_COUNT_REJECTED')
        worker.audit['generated_source_executions'] = executed if scope.generated else 0
        worker.audit['reviewed_source_executions'] = executed if not scope.generated else 0
    finally:
        worker.close()
    require(worker.audit['cleanup_errors'] == [] and worker.audit['surviving_owned_processes'] == 0 and
            not backend.disabled, 'P24_IR_CLEANUP_FAILED')
    if result['admission'] == 'REJECTED':
        require(result.get('admission_code') in {'FAIL_SYNTAX', 'FAIL_ABI', 'FAIL_COMPILE'},
                'P24_UNKNOWN_IR_REJECTION')
    if case is not None and result['admission'] == 'PARSED':
        require(all(type(n) is int and 0 <= n <= 128 for n in result['host_operations']) and
                len(result['observed']) == len(case['steps']) and
                result['snapshot_digest'] == result['replay_snapshot_digest'] and result['replay_match'] is True,
                'P24_IR_REPLAY_OR_BUDGET_UNVERIFIED')
    return {'result': result, 'worker_audit': worker.audit, 'cleanup_verified': True}


class Pilot24JSRunner:
    def __init__(self, scope, slot_id, backend, proof, parsed, refresh=None):
        require_scope(scope)
        require(type(backend) is NodeBackend and type(proof) is FreshPilotIsolation and
                proof.scope is scope and proof.rep == 'js_callback' and
                scope.slot(slot_id)['representation'] == 'js_callback', 'P24_JS_ADMISSION_REJECTED')
        require(parse_status(scope, slot_id, parsed) == 'PARSED', 'P24_JS_SOURCE_NOT_PARSED')
        proof = current_isolation(proof, backend, refresh)
        self.scope, self.slot_id, self.backend, self.proof, self.parsed = scope, slot_id, backend, proof, parsed
        self.audits = []
        self.infrastructure_failure = None
        self.refresh = refresh
        self.first_infrastructure_exception = None

    def infrastructure_error(self, error, stage):
        from .pilot24_recovery import safe_failure
        if self.first_infrastructure_exception is None:
            self.first_infrastructure_exception = {**safe_failure(error), 'stage':stage}

    def run(self, session, payload):
        worker = None
        submitted = False
        fault = None
        started = time.monotonic()
        try:
            require(self.infrastructure_failure is None, 'P24_CALLBACK_ALREADY_BLOCKED')
            require(parse_status(self.scope, self.slot_id, self.parsed) == 'PARSED', 'P24_JS_PARSE_CHANGED')
            candidate_sha256 = self.scope.slot(self.slot_id)['source_sha256']
            source = self.scope.source(self.slot_id)
            self.proof = current_isolation(self.proof, self.backend, self.refresh)
            worker = self.backend.invocation(10)
            worker.audit['candidate_sha256'] = candidate_sha256
            worker.open()
            worker.write({'v': 'gir-node-bootstrap@1', 'invocation': session.invocation,
                          'payload': payload, 'purpose': 'callback', 'source': source},
                         bootstrap=True)
            submitted = True
            worker.audit['generated_source_executions'] = int(self.scope.generated)
            worker.audit['reviewed_source_executions'] = int(not self.scope.generated)
            while True:
                raw = worker.read_line()
                try:
                    frame = bounded_json(raw)
                except EvaluationError:
                    session.receive(raw)
                    raise EvaluationError('P24_INVALID_FRAME_NOT_REJECTED')
                if isinstance(frame, dict) and frame.get('transport') == 'gir-node-exit@1':
                    worker.audit['node_exit_code'] = frame.get('code')
                    worker.audit['final_memory_events'] = worker.memory_events()
                    if not session.closed or frame.get('code') != 0:
                        raise Fault('JS_EXITED', phase='callback')
                    worker.release(frame)
                    break
                worker.write(session.receive(raw))
            worker.audit['terminal'] = 'COMPLETED'
        except Fault as exc:
            fault = exc
            if not submitted or exc.code == 'JS_DISCONNECTED':
                self.infrastructure_failure = 'P24_CALLBACK_ADMISSION_BOOT_OR_TRANSPORT_FAILED'
                self.infrastructure_error(exc, 'bootstrap_or_transport')
            if worker is not None:
                worker.audit['terminal'] = exc.code
        except Exception as error:
            self.infrastructure_error(error, 'metadata_or_transport')
            fault = Fault('JS_EXITED', phase='callback')
            self.infrastructure_failure = 'P24_CALLBACK_ADMISSION_OR_INFRA_FAILED'
            if worker is not None:
                worker.audit['terminal'] = 'P24_ISOLATION_OR_TRANSPORT_REJECTED'
        finally:
            if worker is not None:
                try:
                    worker.close()
                except Exception as error:
                    self.infrastructure_error(error, 'cleanup')
                    fault = fault or Fault('JS_EXITED', phase='callback')
                    self.infrastructure_failure = 'P24_CALLBACK_CLEANUP_FAILED'
                    worker.audit['terminal'] = 'ISOLATION_CLEANUP_FAILED'
                finally:
                    worker.audit['elapsed_seconds'] = time.monotonic() - started
                    if self.first_infrastructure_exception is not None:
                        worker.audit['first_infrastructure_exception'] = dict(self.first_infrastructure_exception)
                    self.audits.append(worker.audit)
        if fault is not None:
            if session.failed is not None:
                raise session.failed
            MockClient(session).fault(fault.code if fault.code in FAULTS else 'JS_EXITED')


class Pilot24Host(HostRuntime):
    def __init__(self, scope, slot_id, initial, runner, seed):
        require(type(runner) is Pilot24JSRunner and runner.scope is scope and
                runner.slot_id == slot_id, 'P24_HOST_RUNNER_REJECTED')
        super().__init__(scope.abi(slot_id), initial, seed=seed)
        require([h['id'] for h in self.core['handlers']] == ['open'] and
                all(not h['body'] for h in self.core['handlers']), 'P24_EMPTY_HOST_ABI_REQUIRED')
        self.runners = {'open': runner}
        self._isolated = True
