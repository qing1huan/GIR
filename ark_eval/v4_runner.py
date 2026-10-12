"""v4 admission adapter; shared host semantics, transport and cleanup unchanged."""
import time
from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .host import FAULTS,HostRuntime,MockClient
from .node_backend import NodeBackend
from .v4_scope import ApprovedV4Scope,FreshV4Isolation,require_parse

class V4JSRunner:
    def __init__(self,scope,task,backend,receipt,parsed):
        if (type(scope)is not ApprovedV4Scope or type(backend)is not NodeBackend
                or type(receipt)is not FreshV4Isolation or receipt.scope is not scope or receipt.rep!='js_callback'):
            raise EvaluationError('V4_JS_ADMISSION_REJECTED')
        receipt.require(backend);require_parse(scope,task,parsed)
        self.scope=scope;self.task=task;self.backend=backend;self.receipt=receipt;self.parsed=parsed;self.audits=[]

    def run(self,session,payload):
        self.receipt.require(self.backend);require_parse(self.scope,self.task,self.parsed)
        source=self.scope.source(self.task,'js_callback')
        worker=self.backend.invocation(10);started=time.monotonic();fault=None
        worker.audit.update(candidate_sha256=self.scope.manifest['source_sha256'][self.task+'/js_callback'])
        try:
            worker.open();worker.write({'v':'gir-node-bootstrap@1','invocation':session.invocation,'payload':payload,
                'purpose':'callback','source':source},bootstrap=True)
            worker.audit['generated_source_executions']=1
            while True:
                raw=worker.read_line()
                try:frame=bounded_json(raw)
                except EvaluationError:session.receive(raw);raise AssertionError('Invalid frame accepted')
                if isinstance(frame,dict) and frame.get('transport')=='gir-node-exit@1':
                    worker.audit['node_exit_code']=frame.get('code');worker.audit['final_memory_events']=worker.memory_events()
                    if not session.closed or frame.get('code')!=0:raise Fault('JS_EXITED',phase='callback')
                    worker.release(frame);break
                worker.write(session.receive(raw))
            worker.audit['terminal']='COMPLETED'
        except Fault as e:fault=e;worker.audit['terminal']=e.code
        except (EvaluationError,OSError,ValueError,KeyError,TypeError):
            fault=Fault('JS_EXITED',phase='callback');worker.audit['terminal']='V4_ISOLATION_OR_TRANSPORT_REJECTED'
        finally:
            try:worker.close()
            except EvaluationError:fault=fault or Fault('JS_EXITED',phase='callback');worker.audit['terminal']='ISOLATION_CLEANUP_FAILED'
            worker.audit['elapsed_seconds']=time.monotonic()-started;self.audits.append(worker.audit)
        if fault is not None:
            if session.failed is not None:raise session.failed
            MockClient(session).fault(fault.code if fault.code in FAULTS else 'JS_EXITED')

class V4HostRuntime(HostRuntime):
    def __init__(self,program,entities,runner,seed=1):
        if type(runner)is not V4JSRunner:raise EvaluationError('V4_RUNNER_REQUIRED')
        runner.receipt.require(runner.backend);require_parse(runner.scope,runner.task,runner.parsed)
        super().__init__(program,entities,seed=seed)
        if any(h['body'] for h in self.core['handlers']) or [h['id'] for h in self.core['handlers']]!=['open']:
            raise EvaluationError('V4_HOST_ABI_REJECTED')
        self.runners={'open':runner};self._isolated=True;self._runner=runner;self._audit_start=0

    def step(self,**kwargs):
        self._audit_start=len(self._runner.audits);return super().step(**kwargs)

    def capture_execution(self,txn,result):
        super().capture_execution(txn,result)
        row=self.execution_history[-1];row['mode']='V4_ISOLATED_EXACT_SOURCE_JS'
        row['source_executions']=sum(a['generated_source_executions'] for a in self._runner.audits[self._audit_start:])
