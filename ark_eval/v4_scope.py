"""Separate exact v4 scope. Old authorizations are never accepted or changed."""
import copy
import hashlib
from pathlib import Path
import time
from .adapter import EvaluationError,bounded_json
from .manual_door_v4 import ROOT,PROTOCOL_DIGEST,ORDER,load_requests
from headless_v01.compiler import digest

PROPOSAL_DIGEST='7b017a647137f3deb3adb6f5ee5f07cde9b58eba199cebfb69f256eb48bad360'
PROPOSAL=ROOT/'results/v4-batch001-static-review-20261011/execution-approval-proposal.json'
BUNDLE=ROOT/'results/user-local-v4-modify-debug-001'
FREEZE=ROOT/'ark_eval/v4_execution.freeze.json'
APPROVAL_FILE=ROOT/'results/v4-batch001-execution-approval.json'
FILES=('ark_eval/v4_scope.py','ark_eval/v4_backend.py','ark_eval/v4_runner.py','ark_eval/v4_eval.py',
       'ark_eval/tests/test_v4_execution.py','docs/ark-v4-exact-execution.md',
       'ark_eval/candidate_eval.py','ark_eval/callback_verify.py','ark_eval/candidate_runner.py',
       'ark_eval/js_verify.py','ark_eval/pair003_eval.py')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    if (path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve())
            or path.name.lower()=='env.txt'):raise EvaluationError('V4_SCOPE_PATH_REJECTED')
    return bounded_json(path.read_text(encoding='utf-8'))

def proposal():
    p=read(PROPOSAL)
    if digest(p)!=PROPOSAL_DIGEST or p['human_approval_received'] is not False:
        raise EvaluationError('V4_PROPOSAL_DRIFT')
    return p

def current_bindings():
    from .pair003_ir_backend import ENGINE_FILES
    contract,_,entry=load_requests()
    names=set(contract['artifact_sha256'])|set(entry['artifact_sha256'])|set(FILES)|set(ENGINE_FILES)
    return {n:sha(ROOT/n) for n in sorted(names)}

def bundle_hashes():
    p=proposal();result={}
    for name in p['bundle31_sha256']:
        path=BUNDLE/name
        if path.is_symlink() or not path.resolve().is_relative_to(BUNDLE.resolve()):
            raise EvaluationError('V4_BUNDLE_PATH_REJECTED')
        result[name]=sha(path)
    return result

def case_sets():
    contract,_,_=load_requests();p=proposal()
    legacy=read(ROOT/contract['private_oracle']['legacy_file'])['cases']
    diagnostics=read(ROOT/contract['private_oracle']['diagnostic_file'])['cases']
    result={}
    for arm in p['arms']:
        task,rep=arm['task_id'],arm['representation']
        old=[c for c in legacy if c['task_id']==task];new=[c for c in diagnostics if c['task_id']==task]
        if (len(old)!=26 or len(new)!=8 or digest(old)!=arm['legacy26_digest']
                or digest(new)!=arm['diagnostic8_digest']
                or [c['id'] for c in old]!=arm['legacy26_case_ids']
                or [c['id'] for c in new]!=arm['diagnostic8_case_ids']):
            raise EvaluationError('V4_CASE_SCOPE_DRIFT')
        result[task,rep]=[('legacy26',c) for c in old]+[('diagnostic8',c) for c in new]
    return result

def make_manifest():
    contract,requests,_=load_requests();p=proposal()
    if (p['protocol_digest']!=PROTOCOL_DIGEST or tuple((a['task_id'],a['representation']) for a in p['arms'])!=ORDER
            or bundle_hashes()!=p['bundle31_sha256']):raise EvaluationError('V4_BUNDLE_OR_ORDER_DRIFT')
    cases=case_sets()
    return {'schema':'gir-v4-exact136-execution@1','protocol_digest':PROTOCOL_DIGEST,
        'proposal_digest':PROPOSAL_DIGEST,'proposal_sha256':sha(PROPOSAL),
        'bundle31_sha256':p['bundle31_sha256'],'bindings':current_bindings(),
        'order':[list(k) for k in ORDER],
        'source_sha256':{a['task_id']+'/'+a['representation']:a['source_sha256'] for a in p['arms']},
        'case_digests':{t+'/'+r:digest(cases[t,r]) for t,r in ORDER},
        'case_count':136,'legacy26_per_arm':26,'diagnostic8_per_arm':8,'seeds':[1,123],
        'seeds_already_in_cases':True,'IR_replays_per_case':1,'JS_replay':'TRANSCRIPT_NO_NEW_CALLBACK',
        'requires_explicit_human_approval':True,'old_authorizations_accepted':False,
        'single_use_execution':{'claim_root':'results/v4-batch001-execution-evidence',
            'key':'scope_digest','atomic_claim':'exclusive_directory_creation',
            'before_any_backend':True,'claim_retained_on_failure':True,'retry_allowed':False},
        'isolation':p['isolation'],'model_calls':0,'repairs':0,'reset_cards':0,'push':False}

def load_manifest():
    frozen=read(FREEZE);actual=make_manifest()
    if digest(frozen)!=digest(actual):raise EvaluationError('V4_EXECUTION_BINDING_DRIFT')
    return frozen

def approval_template(manifest):
    return {'schema':'gir-v4-explicit-human-approval@1','decision':'PENDING',
        'scope_digest':digest(manifest),'protocol_digest':PROTOCOL_DIGEST,'proposal_digest':PROPOSAL_DIGEST,
        'source_sha256':copy.deepcopy(manifest['source_sha256']),
        'human_explicit_approval':False,'human_statement':''}

class ApprovedV4Scope:
    def __init__(self,manifest,approval):
        expected=approval_template(manifest)
        expected.update(decision='APPROVE_EXACT_V4_136',human_explicit_approval=True)
        statement=approval.get('human_statement') if isinstance(approval,dict) else None
        if (not isinstance(statement,str) or not statement.strip() or len(statement)>1000):
            raise EvaluationError('V4_EXPLICIT_HUMAN_APPROVAL_REQUIRED')
        expected['human_statement']=statement
        if digest(approval)!=digest(expected) or digest(manifest)!=digest(load_manifest()):
            raise EvaluationError('V4_EXPLICIT_APPROVAL_OR_SCOPE_REJECTED')
        self.manifest=copy.deepcopy(manifest);self._validated=copy.deepcopy(manifest)
        self.approval=copy.deepcopy(approval);self._approval=copy.deepcopy(approval)
        self.cases=case_sets()

    def require(self):
        # Full content hashes on every admission; no stale metadata cache.
        if (digest(self.manifest)!=digest(self._validated) or digest(self.approval)!=digest(self._approval)
                or sha(PROPOSAL)!=self.manifest['proposal_sha256']
                or digest(read(FREEZE))!=digest(self._validated) or bundle_hashes()!=self.manifest['bundle31_sha256']
                or any(sha(ROOT/n)!=h for n,h in self.manifest['bindings'].items())
                or any(digest(self.cases[t,r])!=self.manifest['case_digests'][t+'/'+r] for t,r in ORDER)):
            raise EvaluationError('V4_EXACT_SCOPE_CHANGED')

    def source(self,task,rep):
        self.require()
        if (task,rep) not in ORDER:raise EvaluationError('V4_UNKNOWN_ARM')
        raw=(BUNDLE/task/rep/'candidate.txt').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=self.manifest['source_sha256'][task+'/'+rep]:
            raise EvaluationError('V4_EXACT_SOURCE_CHANGED')
        return raw.decode('utf-8')

    def case(self,task,rep,group,case):
        self.require()
        if (group,digest(case)) not in [(g,digest(c)) for g,c in self.cases.get((task,rep),[])]:
            raise EvaluationError('V4_UNKNOWN_CASE')

class FreshV4Isolation:
    def __init__(self,scope,backend,profile,faults,rep):
        from .node_backend import NodeBackend
        from .v4_backend import V4IRBackend
        required={'rootless','network_denied','no_user_mounts','environment_scrubbed','cpu_limits','memory_limits','process_tree_termination'}
        names=({'throw_after_write','spin_after_write','oom_after_write','disconnect','unknown_op','injected_key','oversized_line',
          'stdout_flood','stderr_flood','duplicate_key','swallow_host_error','tree_timeout'} if rep=='js_callback' else {'spin','oom','tree'})
        expected=NodeBackend if rep=='js_callback' else V4IRBackend
        if (rep not in ('gameir_text','js_callback') or type(scope)is not ApprovedV4Scope or type(backend)is not expected or backend.disabled
                or set(profile.get('checks',{}))!=required or any(v!='PASS' for v in profile['checks'].values())
                or len(faults)!=len(names) or {f['fixture'] for f in faults}!=names or any(f['status']!='PASS' for f in faults)):
            raise EvaluationError('V4_FRESH_ISOLATION_REJECTED')
        scope.require();self.scope=scope;self.backend=backend;self.rep=rep
        self.image=backend.worker_image;self.worker_hash=backend.worker_hash;self.created=time.monotonic()

    def require(self,backend):
        self.scope.require()
        if (backend is not self.backend or backend.disabled or backend.worker_image!=self.image
                or backend.worker_hash!=self.worker_hash or time.monotonic()-self.created>600):
            raise EvaluationError('V4_ISOLATION_STALE_OR_CHANGED')

def require_parse(scope,task,evidence):
    scope.require()
    from .container_profile import image_lock
    fingerprint=hashlib.sha256((ROOT/'ark_eval/candidate_parser.mjs').read_bytes()+
        (ROOT/'ark_eval/node_supervisor.sh').read_bytes()+image_lock()['repository_digest'].encode()).hexdigest()
    audit=evidence.get('worker_audit',{});source_sha=scope.manifest['source_sha256'][task+'/js_callback']
    if (evidence.get('result')!={'status':'PARSED','exports':['onOpen'],'static_imports':[],
            'source_sha256':source_sha,'top_level_evaluated':False,'callback_invoked':False}
            or evidence.get('candidate_executions')!=0 or audit.get('worker_source_sha256')!=fingerprint
            or audit.get('cleanup_errors')!=[] or audit.get('surviving_owned_processes')!=0
            or audit.get('network_mode')!='none' or audit.get('readonly_rootfs') is not True or audit.get('user_mounts')!=[]
            or audit.get('generated_source_executions')!=0
            or audit.get('rootless_host',{}).get('rootless') is not True
            or audit.get('kernel_security')!={'CapEff':'0000000000000000','NoNewPrivs':'1','Seccomp':'2'}
            or audit.get('kernel_limits')!={'cpu.max':'25000 100000','memory.max':'67108864','memory.swap.max':'0','pids.max':'32'}):
        raise EvaluationError('V4_EXACT_JS_PARSE_REJECTED')
