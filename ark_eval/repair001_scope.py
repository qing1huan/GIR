"""User-approved exact repair001 scope, independent of historical pending gates."""
import copy
import hashlib
from pathlib import Path
import time
from .adapter import EvaluationError
from .repair001_review import load_reviewed_bundle,review_bindings,artifact_hashes,BUNDLE,HASHES,PROTOCOL_DIGEST,ROOT
from .oracle import final_cases
from headless_v01.compiler import digest

FILES=('ark_eval/repair001_scope.py','ark_eval/scoped_candidate_backend.py',
       'ark_eval/scoped_candidate_runner.py','ark_eval/repair001_eval.py','ark_eval/tests/test_repair001_scope.py')


def bindings():
    result=review_bindings()
    for name in FILES:result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def approved_manifest():
    _,_,files=load_reviewed_bundle()
    return {'schema':'gir-exact-candidate-execution-scope@1','scope_id':'repair001-both-original26',
        'protocol_digest':PROTOCOL_DIGEST,'candidate_sha256':dict(HASHES),'bundle15_sha256':files,
        'bindings':bindings(),'human_approval_received':True,
        'approval_basis':'Parent relayed explicit human 同意 to both fixed repaired IR/JS in disconnected no-personal-mount containers, 26 original tests each; 2026-10-11.',
        'representations':['gameir_text','js_callback'],'case_count_per_arm':26,'seeds':[1,123],
        'oracle_digest':digest(final_cases('P-C02-create')),'source_modified':False,'oracle_modified':False,
        'old_pending_IR_only_manifest_reused':False,'JS_static_contract_failure_blocks_gameplay':False,
        'JS_requires_actual_parse_and_exports':True,'fresh_isolation_required':True,
        'credentials_or_API_requests':0,'reset_cards':0,'push':False}


class ExactScope:
    def __init__(self,manifest):
        if manifest!=approved_manifest():raise EvaluationError('EXACT_SCOPE_DRIFT_OR_UNAUTHORIZED')
        self.manifest=copy.deepcopy(manifest)
        self._validated=copy.deepcopy(manifest)

    def require(self):
        # Validate the full protocol once, then hash ALL bound code and bundle
        # files at every admission. No metadata-only cache or stale bytes bypass.
        if (self.manifest!=self._validated or artifact_hashes()!=self._validated['bundle15_sha256']
                or any(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha
                       for name,sha in self._validated['bindings'].items())):
            raise EvaluationError('EXACT_SCOPE_CHANGED')

    def source(self,representation):
        self.require()
        if representation not in self.manifest['representations']:raise EvaluationError('REPRESENTATION_NOT_APPROVED')
        raw=(BUNDLE/representation/'candidate.txt').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=self.manifest['candidate_sha256'][representation]:
            raise EvaluationError('EXACT_SOURCE_CHANGED')
        return raw.decode('utf-8',errors='strict')


class FreshScopeIsolation:
    """Fresh in-process proof bound to backend identity and exact approved scope."""
    def __init__(self,scope,backend,profile,faults,kind):
        from .node_backend import NodeBackend
        from .scoped_candidate_backend import ScopedIRBackend
        expected=NodeBackend if kind=='js_callback' else ScopedIRBackend
        names=({'throw_after_write','spin_after_write','oom_after_write','disconnect','unknown_op',
            'injected_key','oversized_line','stdout_flood','stderr_flood','duplicate_key','swallow_host_error','tree_timeout'}
            if kind=='js_callback' else {'spin','oom','tree'})
        checks={'rootless','network_denied','no_user_mounts','environment_scrubbed','cpu_limits','memory_limits','process_tree_termination'}
        if (type(scope)is not ExactScope or type(backend)is not expected or backend.disabled
                or set(profile['checks'])!=checks or any(v!='PASS' for v in profile['checks'].values())
                or len(faults)!=len(names) or {f['fixture'] for f in faults}!=names
                or any(f['status']!='PASS' for f in faults)):raise EvaluationError('FRESH_SCOPE_ISOLATION_REJECTED')
        scope.require();self.scope=scope;self.backend=backend;self.kind=kind
        self.image=backend.worker_image;self.worker_hash=backend.worker_hash;self.created=time.monotonic()

    def require(self,backend):
        self.scope.require()
        if (backend is not self.backend or backend.disabled or backend.worker_image!=self.image
                or backend.worker_hash!=self.worker_hash or time.monotonic()-self.created>600):
            raise EvaluationError('FRESH_SCOPE_ISOLATION_STALE')


def require_JS_parse(scope,evidence):
    scope.require()
    from .container_profile import image_lock
    fingerprint=hashlib.sha256((ROOT/'ark_eval/candidate_parser.mjs').read_bytes()+
        (ROOT/'ark_eval/node_supervisor.sh').read_bytes()+image_lock()['repository_digest'].encode()).hexdigest()
    result=evidence.get('result',{});audit=evidence.get('worker_audit',{})
    if (result!={'status':'PARSED','exports':['onOpen'],'static_imports':[],
                 'source_sha256':HASHES['js_callback'],'top_level_evaluated':False,'callback_invoked':False}
            or evidence.get('candidate_executions')!=0 or audit.get('worker_source_sha256')!=fingerprint
            or audit.get('cleanup_errors')!=[] or audit.get('surviving_owned_processes')!=0
            or audit.get('network_mode')!='none' or audit.get('readonly_rootfs') is not True
            or audit.get('user_mounts')!=[] or audit.get('generated_source_executions')!=0
            or audit.get('kernel_security')!={'CapEff':'0000000000000000','NoNewPrivs':'1','Seccomp':'2'}
            or audit.get('kernel_limits')!={'cpu.max':'25000 100000','memory.max':'67108864','memory.swap.max':'0','pids.max':'32'}):
        raise EvaluationError('EXACT_JS_PARSE_REJECTED')
    return True
