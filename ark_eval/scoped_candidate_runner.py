"""Thin manifest-scoped adapter; unchanged host protocol and container transport."""
import copy
import time
from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .host import FAULTS,HostRuntime,MockClient
from .node_backend import NodeBackend
from .repair001_scope import ExactScope,FreshScopeIsolation,require_JS_parse


class ScopedJSRunner:
    def __init__(self,scope,backend,isolation,parsed):
        if (type(scope)is not ExactScope or type(backend)is not NodeBackend
                or type(isolation)is not FreshScopeIsolation or isolation.scope is not scope
                or isolation.kind!='js_callback'):raise EvaluationError('SCOPED_JS_BACKEND_REJECTED')
        isolation.require(backend);require_JS_parse(scope,parsed)
        self.scope=scope;self.source=scope.source('js_callback');self.backend=backend
        self.isolation=isolation;self.parsed=copy.deepcopy(parsed);self.audits=[]

    def run(self,session,payload):
        self.isolation.require(self.backend);require_JS_parse(self.scope,self.parsed)
        if self.scope.source('js_callback')!=self.source:raise EvaluationError('SCOPED_JS_SOURCE_DRIFT')
        worker=self.backend.invocation(10);started=time.monotonic();fault=None
        worker.audit.update(candidate_sha256=self.scope.manifest['candidate_sha256']['js_callback'],approval_schema=self.scope.manifest['schema'])
        try:
            worker.open();worker.write({'v':'gir-node-bootstrap@1','invocation':session.invocation,'payload':payload,
                'purpose':'callback','source':self.source},bootstrap=True)
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
            fault=Fault('JS_EXITED',phase='callback');worker.audit['terminal']='SCOPED_ISOLATION_OR_TRANSPORT_REJECTED'
        finally:
            try:worker.close()
            except EvaluationError:fault=fault or Fault('JS_EXITED',phase='callback');worker.audit['terminal']='ISOLATION_CLEANUP_FAILED'
            worker.audit['elapsed_seconds']=time.monotonic()-started;self.audits.append(worker.audit)
        if fault is not None:
            if session.failed is not None:raise session.failed
            MockClient(session).fault(fault.code if fault.code in FAULTS else 'JS_EXITED')


class ScopedHostRuntime(HostRuntime):
    def __init__(self,program,entities,runner,seed=1):
        if type(runner)is not ScopedJSRunner:raise EvaluationError('SCOPED_RUNNER_REQUIRED')
        runner.isolation.require(runner.backend);require_JS_parse(runner.scope,runner.parsed)
        super().__init__(program,entities,seed=seed)
        if any(h['body'] for h in self.core['handlers']) or [h['id'] for h in self.core['handlers']]!=['open']:
            raise EvaluationError('SCOPED_HOST_ABI_REJECTED')
        self.runners={'open':runner};self._isolated=True;self._scope_runner=runner;self._audit_start=0

    def step(self,**kwargs):
        self._audit_start=len(self._scope_runner.audits);return super().step(**kwargs)

    def capture_execution(self,txn,result):
        super().capture_execution(txn,result)
        row=self.execution_history[-1];row['mode']='ISOLATED_EXACT_SCOPE_CANDIDATE_JS_HOST'
        row['source_executions']=sum(a['generated_source_executions'] for a in self._scope_runner.audits[self._audit_start:])
