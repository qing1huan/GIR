"""Independent hash-approved candidate runner; existing catalog gates stay closed."""
import copy
import time

from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .candidate_review import validate_approval,version_bindings,HASHES
from .host import FAULTS,HostRuntime,MockClient
from .node_backend import NodeBackend


class FreshIsolation:
    """In-process proof tied to the exact active backend, never loaded from JSON."""
    def __init__(self,backend,profile,faults):
        if (type(backend)is not NodeBackend or backend.disabled or not profile or
                any(v!='PASS' for v in profile['checks'].values()) or
                set(profile['checks'])!={'rootless','network_denied','no_user_mounts','environment_scrubbed','cpu_limits','memory_limits','process_tree_termination'} or
                len(faults)!=12 or {f['fixture'] for f in faults}!={'throw_after_write','spin_after_write','oom_after_write','disconnect','unknown_op',
                    'injected_key','oversized_line','stdout_flood','stderr_flood','duplicate_key','swallow_host_error','tree_timeout'} or
                any(f['status']!='PASS' for f in faults)):
            raise EvaluationError('FRESH_CANDIDATE_ISOLATION_REJECTED')
        self.backend=backend;self.image=backend.worker_image;self.worker_hash=backend.worker_hash
        self.bindings=version_bindings()
        self.created=time.monotonic()

    def require(self,backend):
        if (backend is not self.backend or backend.disabled or backend.worker_image!=self.image or
                backend.worker_hash!=self.worker_hash or self.bindings!=version_bindings() or
                time.monotonic()-self.created>600):
            raise EvaluationError('FRESH_CANDIDATE_ISOLATION_STALE_OR_CHANGED')


class ApprovedCandidateJSRunner:
    def __init__(self,manifest,backend,isolation):
        if type(backend)is not NodeBackend or type(isolation)is not FreshIsolation:
            raise EvaluationError('CANDIDATE_BACKEND_OR_ISOLATION_REJECTED')
        isolation.require(backend)
        self.source=validate_approval(manifest);self.manifest=copy.deepcopy(manifest)
        self.backend=backend;self.isolation=isolation;self.timeout=10;self.audits=[]

    def run(self,session,payload):
        # Recheck bytes, approval and current runtime/runner before every invocation.
        self.isolation.require(self.backend)
        if validate_approval(self.manifest)!=self.source:
            raise EvaluationError('CANDIDATE_SOURCE_CHANGED_OR_UNKNOWN')
        worker=self.backend.invocation(self.timeout);started=time.monotonic();fault=None
        worker.audit.update(candidate_sha256=HASHES['js_callback'],approval_schema=self.manifest['schema'])
        try:
            worker.open()
            worker.write({'v':'gir-node-bootstrap@1','invocation':session.invocation,'payload':payload,
                          'purpose':'callback','source':self.source},bootstrap=True)
            worker.audit['generated_source_executions']=1
            while True:
                raw=worker.read_line()
                try:frame=bounded_json(raw)
                except EvaluationError:
                    session.receive(raw);raise AssertionError('Invalid frame accepted')
                if isinstance(frame,dict) and frame.get('transport')=='gir-node-exit@1':
                    worker.audit['node_exit_code']=frame.get('code')
                    worker.audit['final_memory_events']=worker.memory_events()
                    if not session.closed or frame.get('code')!=0:raise Fault('JS_EXITED',phase='callback')
                    worker.release(frame);break
                worker.write(session.receive(raw))
            worker.audit['terminal']='COMPLETED'
        except Fault as error:fault=error;worker.audit['terminal']=error.code
        except (EvaluationError,OSError,ValueError,KeyError,TypeError):
            fault=Fault('JS_EXITED',phase='callback');worker.audit['terminal']='CANDIDATE_ISOLATION_OR_TRANSPORT_REJECTED'
        finally:
            try:worker.close()
            except EvaluationError:
                fault=fault or Fault('JS_EXITED',phase='callback');worker.audit['terminal']='ISOLATION_CLEANUP_FAILED'
            worker.audit['elapsed_seconds']=time.monotonic()-started;self.audits.append(worker.audit)
        if fault is not None:
            if session.failed is not None:raise session.failed
            MockClient(session).fault(fault.code if fault.code in FAULTS else 'JS_EXITED')


class CandidateHostRuntime(HostRuntime):
    def __init__(self,program,entities,runner,seed=1):
        if type(runner)is not ApprovedCandidateJSRunner:raise EvaluationError('CANDIDATE_RUNNER_REJECTED')
        runner.isolation.require(runner.backend);validate_approval(runner.manifest)
        super().__init__(program,entities,seed=seed)
        if any(h['body'] for h in self.core['handlers']) or [h['id'] for h in self.core['handlers']]!=['open']:
            raise EvaluationError('CANDIDATE_HOST_MANIFEST_REJECTED')
        self.runners={'open':runner};self._isolated=True;self._candidate_runner=runner;self._audit_start=0

    def step(self,**kwargs):
        self._audit_start=len(self._candidate_runner.audits)
        return super().step(**kwargs)

    def capture_execution(self,txn,result):
        super().capture_execution(txn,result)
        record=self.execution_history[-1]
        record['mode']='ISOLATED_APPROVED_ORIGINAL_CANDIDATE_JS_HOST'
        record['source_executions']=sum(a['generated_source_executions'] for a in self._candidate_runner.audits[self._audit_start:])


def replay_candidate(program,case,history,executions):
    """Shared transcript replay without candidate execution or changed oracle."""
    runtime=HostRuntime(program,case['initial'],seed=case['seed'],recorded=[e['transcripts'] for e in executions])
    for index,record in enumerate(history):
        actual=runtime.step(**record['request'])
        expected={k:v for k,v in executions[index].items() if k not in ('mode','source_executions')}
        replayed={k:v for k,v in runtime.execution_history[index].items() if k not in ('mode','source_executions')}
        if actual!=record['result'] or expected!=replayed:raise Fault('ReplayMismatch',phase='callback')
    return runtime
