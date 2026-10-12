"""Exact Pilot24 admission. Collection/v4 approvals never grant execution."""
import copy
import hashlib
import time
from pathlib import Path

from headless_v01.compiler import digest
from .adapter import EvaluationError, bounded_json

ROOT = Path(__file__).resolve().parents[1]
STATIC = 'docs/experiment-v3/pilot24-static-manifest.json'
STATIC_SHA = '403c429a6475c4459b3bc5757d2503d80b5b9957ba35dd4455e326b1cf610a28'
BUNDLE = 'results/pilot24-user-local-001'
FREEZE = ROOT / 'ark_eval/pilot24_execution.freeze.json'
APPROVAL = ROOT / 'results/pilot24-execution-human-approval.json'
PUBLIC_RUNTIME = 'ark_eval/pilot24_public_runtime.freeze.json'
FILES = ('ark_eval/pilot24_execution_scope.py', 'ark_eval/pilot24_execution_backend.py',
         'ark_eval/pilot24_execution_worker.py', 'ark_eval/pilot24_execution_eval.py',
         'ark_eval/pilot24_execution_controls.py', 'ark_eval/tests/test_pilot24_execution.py',
         'docs/experiment-v3/PILOT24_EXECUTION.md', PUBLIC_RUNTIME)
PROFILE = {'rootless', 'network_denied', 'no_user_mounts', 'environment_scrubbed',
           'cpu_limits', 'memory_limits', 'process_tree_termination'}
JS_FAULTS = {'throw_after_write', 'spin_after_write', 'oom_after_write', 'disconnect',
             'unknown_op', 'injected_key', 'oversized_line', 'stdout_flood', 'stderr_flood',
             'duplicate_key', 'swallow_host_error', 'tree_timeout'}


def require(ok, code):
    if not ok:
        raise EvaluationError(code)


def read_bytes(relative):
    p = ROOT / relative
    require(not Path(relative).is_absolute() and '..' not in Path(relative).parts and
            p.resolve() == p and p.is_file() and p.name.lower() not in {'env.txt', '.env'} and
            p.suffix.lower() not in {'.key', '.pem'}, 'P24_EXACT_PATH_REJECTED')
    return p.read_bytes()


def sha(relative):
    return hashlib.sha256(read_bytes(relative)).hexdigest()


def read(relative):
    return bounded_json(read_bytes(relative).decode('utf-8'))


def static_manifest():
    require(sha(STATIC) == STATIC_SHA, 'P24_STATIC_MANIFEST_DRIFT')
    m = read(STATIC)
    require(m['counts']['generation_slots'] == 24 and m['counts']['unique_sources'] == 14 and
            m['counts']['planned_primary_case_evaluations'] == 288, 'P24_STATIC_COUNTS')
    for key in ('anchor_sha256', 'material_bindings', 'runtime_bindings',
                'material_validation_evidence', 'sender_artifact_bindings',
                'historical_engineering_evidence', 'additional_maintained_source_bindings'):
        for n, h in m[key].items():
            require(sha(n) == h, 'P24_FROZEN_FILE_DRIFT')
    names = {p.relative_to(ROOT / BUNDLE).as_posix()
             for p in (ROOT / BUNDLE).rglob('*') if p.is_file()}
    require(names == set(m['bundle_files_sha256']) and len(names) == 172, 'P24_BUNDLE_FILE_SET')
    actual = {n: sha(BUNDLE + '/' + n) for n in sorted(names)}
    require(actual == m['bundle_files_sha256'] and digest(actual) == m['bundle_posix_manifest_digest'],
            'P24_RAW_COLLECTION_DRIFT')
    return m


def case_sets(m):
    result = {}
    for task, entry in m['task_cases'].items():
        cases = read(entry['path'])
        require(sha(entry['path']) == entry['sha256'] and len(cases) == 12 and
                [c['id'] for c in cases] == entry['case_ids'] and
                all(type(c['seed']) is int and c['seed'] == 1 and c['task_id'] == task for c in cases),
                'P24_FIXED_CASES_DRIFT')
        result[task] = cases
    require(sum(map(len, result.values())) == 72, 'P24_CASE_COUNT')
    return result


def make_manifest():
    m = static_manifest()
    cases = case_sets(m)
    slots = [{k: s[k] for k in ('index', 'slot_id', 'task_id', 'repetition', 'representation',
                               'source_path', 'source_sha256', 'source_bytes', 'primary_case_ids')}
             for s in m['slots']]
    for slot in slots:
        slot['static_rejection'] = ('FAIL_SYNTAX' if slot['slot_id'] == 'R1-r1-gameir_text' else None)
    batch_identity = {'bundle_digest': m['bundle_posix_manifest_digest'], 'slots': slots,
                      'case_digests': {t: digest(c) for t, c in cases.items()}}
    return {'schema': 'gir-pilot24-exact-execution@1', 'status': 'SEALED_PENDING_REVIEW_AND_APPROVAL',
            'static_manifest_sha256': STATIC_SHA, 'batch_key': digest(batch_identity),
            'bundle_digest': m['bundle_posix_manifest_digest'], 'slots': slots,
            'case_digests': batch_identity['case_digests'],
            'bindings': {n: sha(n) for n in FILES}, 'fixed_case_definitions': 72,
            'public_runtime_package_digest': digest(read(PUBLIC_RUNTIME)['files_sha256']),
            'planned_primary_rows': 288, 'maximum_primary_runs': 276,
            'static_rejected_slots': ['R1-r1-gameir_text'], 'potentially_runnable_slots': 23,
            'old_authorizations_accepted': False, 'explicit_execution_approval_required': True,
            'claim': {'root': 'results/pilot24-execution-claims', 'key': 'batch_key',
                      'atomic_directory': True, 'retained_on_failure': True, 'resume_or_retry': False},
            'limits': {'seconds': 10, 'memory': 67108864, 'cpu': '25000 100000', 'pids': 32,
                       'host_ops_tick': 128, 'wire_handler': 32, 'wire_tick': 256,
                       'line_bytes': 8192, 'bootstrap_bytes': 32768},
            'overhead': {'JS_profile_and_12_faults': 'per_JS_slot_and_when_stale',
                         'IR_profile_and_3_faults': 'per_IR_slot_and_when_stale',
                         'base_JS_precheck_worker_runs': 156, 'base_IR_precheck_worker_runs': 44,
                         'JS_parse_workers_maximum': 12, 'IR_replays_maximum': 132,
                         'JS_transcript_replays_maximum': 144,
                         'IR_admission_workers_maximum': 11,
                         'IR_image_count_maximum': 6,
                         'callback_invocations': 'all_events; not equal to primary case runs'},
            'model_calls': 0, 'repairs': 0, 'reset_cards': 0, 'push': False}


def load_manifest():
    frozen = read(FREEZE.relative_to(ROOT).as_posix())
    require(digest(frozen) == digest(make_manifest()), 'P24_EXECUTION_FREEZE_DRIFT')
    return frozen


def approval_fields(m):
    """In-memory contract only. No approval/template is written by this module."""
    return {'schema': 'gir-pilot24-explicit-execution-approval@1',
            'decision': 'APPROVE_EXACT_PILOT24', 'human_explicit_approval': True,
            'scope_digest': digest(m), 'batch_key': m['batch_key'],
            'static_manifest_sha256': STATIC_SHA, 'planned_primary_rows': 288,
            'maximum_primary_runs': 276, 'static_rejected_slots': ['R1-r1-gameir_text']}


class ApprovedPilot24Scope:
    def __init__(self, manifest, approval, *, forward_manifest=None):
        self._forward_manifest = copy.deepcopy(forward_manifest)
        if forward_manifest is not None:
            from . import pilot24_recovery_scope as recovery
            recovery = recovery.forward_provider(forward_manifest)
            recovery.validate_forward_permit(forward_manifest, approval)
            require(digest(manifest) == digest(recovery.forward_execution_manifest(forward_manifest)),
                    'P24_FORWARD_EXECUTION_VIEW_DRIFT')
            self.cases = case_sets(recovery.forward_static_manifest())
        else:
            self._approve_original(manifest, approval)
            self.cases = case_sets(static_manifest())
        self.manifest = copy.deepcopy(manifest)
        self.approval_digest = digest(approval)
        self._snapshot = digest(manifest)
        self.slots = {s['slot_id']: s for s in self.manifest['slots']}
        self.generated = True

    @staticmethod
    def _approve_original(manifest, approval):
        expected = approval_fields(manifest)
        statement = approval.get('human_statement') if isinstance(approval, dict) else None
        require(isinstance(statement, str) and 0 < len(statement.strip()) <= 1000,
                'P24_EXPLICIT_EXECUTION_APPROVAL_REQUIRED')
        expected['human_statement'] = statement
        require(digest(approval) == digest(expected) and digest(manifest) == digest(load_manifest()),
                'P24_OLD_OR_INEXACT_APPROVAL_REJECTED')

    def require(self):
        if getattr(self, '_forward_manifest', None) is None:
            current = load_manifest()
        else:
            from . import pilot24_recovery_scope as recovery
            recovery = recovery.forward_provider(self._forward_manifest)
            require(digest(self._forward_manifest) == digest(recovery.load_forward_manifest()),
                    'P24_FORWARD_SCOPE_DRIFT')
            current = recovery.forward_execution_manifest(self._forward_manifest)
        require(digest(self.manifest) == self._snapshot == digest(current) and
                all(digest(self.cases[t]) == self.manifest['case_digests'][t] for t in self.cases),
                'P24_LIVE_SCOPE_DRIFT')

    def slot(self, slot_id):
        self.require()
        if getattr(self, '_forward_manifest', None) is not None:
            require(slot_id in {r['slot_id'] for r in self._forward_manifest['allowed_rows']},
                    'P24_FORWARD_EXCLUDED_SLOT')
        require(slot_id in self.slots, 'P24_UNKNOWN_SLOT')
        require(digest(self.slots[slot_id]) == digest(next(s for s in self.manifest['slots']
                if s['slot_id'] == slot_id)), 'P24_SLOT_OBJECT_DRIFT')
        return copy.deepcopy(self.slots[slot_id])

    def source(self, slot_id):
        slot = self.slot(slot_id)
        require(slot['static_rejection'] is None, 'P24_STATICALLY_REJECTED_SOURCE')
        raw = read_bytes(slot['source_path'])
        require(hashlib.sha256(raw).hexdigest() == slot['source_sha256'] and
                len(raw) == slot['source_bytes'], 'P24_EXACT_SOURCE_DRIFT')
        return raw.decode('utf-8')

    def abi(self, slot_id):
        return read('ark_eval/pilot24/public/' + self.slot(slot_id)['task_id'] + '/abi.json')

    def case(self, slot_id, case):
        slot = self.slot(slot_id)
        if getattr(self, '_forward_manifest', None) is not None:
            require(any(r['slot_id'] == slot_id and r['case_id'] == case['id'] and
                        r['case_digest'] == digest(case) for r in self._forward_manifest['allowed_rows']),
                    'P24_FORWARD_EXCLUDED_CASE')
        require(digest(case) in {digest(c) for c in self.cases[slot['task_id']]}, 'P24_UNKNOWN_CASE')


class TrustedPilotControl:
    """Only pre-existing authored catalog controls; no candidate/path parameter."""
    def __init__(self, task, rep, mutant=0):
        from .pilot24_authored import TASKS
        from .pilot24_trusted import authored_source, authored_path
        require(task in TASKS and rep in {'gameir_text', 'js_callback'} and
                type(mutant) is int and mutant in (0, 1, 2, 3), 'P24_TRUSTED_CATALOG_ONLY')
        self.generated = False
        self._source = authored_source(task, 1, rep, mutant)
        self._path = authored_path(task, 1, rep, mutant).relative_to(ROOT).as_posix()
        self._sha = sha(self._path)
        self._slot = {'slot_id': task + '-' + rep + '-control-' + str(mutant), 'task_id': task,
                      'representation': rep, 'source_sha256': self._sha, 'static_rejection': None}
        self.cases = {task: read('ark_eval/pilot24/private/' + task + '/cases.json')}
        self._cases_digest = digest(self.cases)
        self._bindings = {n: sha(n) for n in FILES}

    def require(self):
        from .pilot24_trusted import authored_source
        s = self._slot
        require(sha(self._path) == self._sha and digest(self.cases) == self._cases_digest and
                authored_source(s['task_id'], 1, s['representation'], int(s['slot_id'][-1])) == self._source
                and all(sha(n) == h for n, h in self._bindings.items()), 'P24_TRUSTED_CONTROL_DRIFT')

    def slot(self, slot_id):
        self.require()
        require(slot_id == self._slot['slot_id'], 'P24_TRUSTED_SLOT_ONLY')
        return copy.deepcopy(self._slot)

    def source(self, slot_id):
        self.slot(slot_id)
        return read_bytes(self._path).decode('utf-8')

    def abi(self, slot_id):
        return read('ark_eval/pilot24/public/' + self.slot(slot_id)['task_id'] + '/abi.json')

    def case(self, slot_id, case):
        task = self.slot(slot_id)['task_id']
        require(digest(case) in {digest(c) for c in self.cases[task]}, 'P24_TRUSTED_CASE_ONLY')


def require_scope(scope):
    require(type(scope) in (ApprovedPilot24Scope, TrustedPilotControl), 'P24_SCOPE_TYPE_REJECTED')
    scope.require()


class FreshPilotIsolation:
    def __init__(self, scope, backend, profile, faults, rep):
        from .node_backend import NodeBackend
        from .pilot24_execution_backend import Pilot24IRBackend
        require_scope(scope)
        kind = NodeBackend if rep == 'js_callback' else Pilot24IRBackend
        names = JS_FAULTS if rep == 'js_callback' else {'spin', 'oom', 'tree'}
        require(rep in {'js_callback', 'gameir_text'} and type(backend) is kind and not backend.disabled and
                set(profile['checks']) == PROFILE and all(v == 'PASS' for v in profile['checks'].values()) and
                len(faults) == len(names) and {f['fixture'] for f in faults} == names and
                all(f['status'] == 'PASS' for f in faults), 'P24_FRESH_ISOLATION_REJECTED')
        self.scope, self.backend, self.rep = scope, backend, rep
        self.image, self.worker_hash = backend.worker_image, backend.worker_hash
        self.created = time.monotonic()

    def require(self, backend):
        require_scope(self.scope)
        require(backend is self.backend and not backend.disabled and backend.worker_image == self.image and
                backend.worker_hash == self.worker_hash and time.monotonic() - self.created <= 600,
                'P24_FRESH_ISOLATION_STALE')


def current_isolation(proof, backend, refresh=None):
    """Refresh age only, after metadata checks; never refresh a changed binding."""
    try:
        proof.require(backend)
        return proof
    except EvaluationError as error:
        if error.args != ('P24_FRESH_ISOLATION_STALE',) or refresh is None:
            raise
        require(backend is proof.backend and not backend.disabled and
                backend.worker_image == proof.image and backend.worker_hash == proof.worker_hash,
                'P24_FRESH_ISOLATION_BINDING_CHANGED')
        replacement = refresh()
        require(type(replacement) is FreshPilotIsolation and replacement.scope is proof.scope and
                replacement.backend is backend and replacement.rep == proof.rep,
                'P24_FRESH_ISOLATION_REFRESH_REJECTED')
        replacement.require(backend)
        return replacement


def check_parser_audit(evidence):
    from .container_profile import image_lock
    fp = hashlib.sha256(read_bytes('ark_eval/candidate_parser.mjs') +
                        read_bytes('ark_eval/node_supervisor.sh') +
                        image_lock()['repository_digest'].encode()).hexdigest()
    a = evidence.get('worker_audit', {})
    require(type(evidence.get('candidate_executions')) is int and evidence['candidate_executions'] == 0 and
            a.get('worker_source_sha256') == fp and a.get('cleanup_errors') == [] and
            type(a.get('surviving_owned_processes')) is int and a['surviving_owned_processes'] == 0 and
            a.get('network_mode') == 'none' and a.get('readonly_rootfs') is True and a.get('user_mounts') == [] and
            a.get('generated_source_executions') == 0 and a.get('rootless_host', {}).get('rootless') is True and
            a.get('kernel_security') == {'CapEff': '0000000000000000', 'NoNewPrivs': '1', 'Seccomp': '2'} and
            a.get('kernel_limits') == {'cpu.max': '25000 100000', 'memory.max': '67108864',
                                      'memory.swap.max': '0', 'pids.max': '32'}, 'P24_PARSE_ISOLATION_REJECTED')


def parse_status(scope, slot_id, evidence):
    require_scope(scope)
    require(scope.slot(slot_id)['representation'] == 'js_callback', 'P24_JS_PARSER_SLOT_REQUIRED')
    check_parser_audit(evidence)
    r = evidence.get('result', {})
    require(r.get('source_sha256') == scope.slot(slot_id)['source_sha256'] and
            r.get('top_level_evaluated') is False and r.get('callback_invoked') is False and
            set(r) == {'status', 'source_sha256', 'exports', 'static_imports', 'top_level_evaluated', 'callback_invoked'},
            'P24_PARSE_RESULT_BINDING_REJECTED')
    require(r['status'] in {'PARSED', 'SYNTAX_REJECTED', 'IMPORTS_REJECTED', 'EXPORTS_REJECTED'},
            'P24_PARSE_STATUS_UNKNOWN')
    if r['status'] == 'PARSED':
        require(r['exports'] == ['onOpen'] and r['static_imports'] == [], 'P24_PARSE_EXPORTS_REJECTED')
    return r['status']
