"""Real Node execution of a fixed reviewed catalog only. Arbitrary candidates stay blocked."""
import time

from headless_v01.types import Fault
from .adapter import EvaluationError, bounded_json
from .host import FAULTS, MockClient
from .node_backend import NodeBackend
from .reviewed_js import FAULT_FIXTURES, source


class IsolatedJSRunner:
    def __init__(self, name, backend, timeout=10):
        if type(backend) is not NodeBackend: raise EvaluationError('INVALID_ISOLATED_BACKEND')
        if not isinstance(name, str): raise EvaluationError('UNREVIEWED_SOURCE_BLOCKED')
        if name not in FAULT_FIXTURES: source(name)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 10:
            raise EvaluationError('INVALID_WALL_TIMEOUT')
        self.name = name; self.backend = backend; self.timeout = timeout; self.audits = []
        self.first_infrastructure_exception = None

    def infrastructure_error(self, error, stage):
        # Fixed metadata only: exception messages can contain source or secrets.
        if self.first_infrastructure_exception is None:
            kinds = {'EvaluationError', 'OSError', 'TimeoutExpired', 'ValueError', 'KeyError',
                     'TypeError', 'AssertionError', 'IndexError', 'FileNotFoundError'}
            kind = type(error).__name__
            self.first_infrastructure_exception = {'stage':stage,
                'exception_type':kind if kind in kinds else 'Exception', 'raw_message_saved':False}
        self.backend.disabled = True

    def run(self, session, payload):
        worker = None; started = time.monotonic()
        fault = None
        try:
            worker = self.backend.invocation(self.timeout)
            worker.open()
            boot = {'v':'gir-node-bootstrap@1', 'invocation':session.invocation, 'payload':payload}
            if self.name in FAULT_FIXTURES:
                boot.update(purpose='fault_fixture', fixture=self.name)
            else:
                boot.update(purpose='callback', source=source(self.name))
                worker.audit['reviewed_source_executions'] = 1
            worker.write(boot, bootstrap=True)
            while True:
                raw = worker.read_line()
                try: frame = bounded_json(raw)
                except EvaluationError:
                    # Reuse the same host parser/fault latch, not a permissive JSON decoder.
                    session.receive(raw); raise AssertionError('Invalid frame accepted')
                if isinstance(frame, dict) and frame.get('transport') == 'gir-node-exit@1':
                    worker.audit['node_exit_code'] = frame.get('code')
                    worker.audit['final_memory_events'] = worker.memory_events()
                    if not session.closed or frame.get('code') != 0:
                        raise Fault('JS_EXITED', phase='callback')
                    worker.release(frame); break
                response = session.receive(raw)
                if self.name == 'disconnect':
                    # Genuine transport fault: terminate our own attach client after a staged write.
                    worker.audit['trusted_test_disconnect_injected'] = True
                    worker.disconnect_transport()
                    continue
                worker.write(response)
            worker.audit['terminal'] = 'COMPLETED'
        except Fault as error:
            fault = error
            if worker is not None: worker.audit['terminal'] = error.code
        except Exception as error:
            self.infrastructure_error(error, 'invocation_or_transport')
            fault = Fault('JS_EXITED', phase='callback')
            if worker is not None: worker.audit['terminal'] = 'ISOLATION_OR_TRANSPORT_REJECTED'
        finally:
            try:
                if worker is not None: worker.close()
            except Exception as error:
                self.infrastructure_error(error, 'cleanup')
                fault = fault or Fault('JS_EXITED', phase='callback')
                worker.audit['terminal'] = 'ISOLATION_CLEANUP_FAILED'
                worker.audit.setdefault('cleanup_errors', []).append('TRUSTED_FIXTURE_CLEANUP_EXCEPTION')
                worker.audit['surviving_owned_processes'] = 'UNKNOWN'
            audit = worker.audit if worker is not None else {
                'terminal':'INVOCATION_NOT_CONFIRMED', 'cleanup_errors':['WORKER_AUDIT_UNAVAILABLE'],
                'surviving_owned_processes':'UNKNOWN', 'reviewed_source_executions':'UNKNOWN'}
            audit['elapsed_seconds'] = time.monotonic() - started
            if self.first_infrastructure_exception is not None:
                audit['first_infrastructure_exception'] = dict(self.first_infrastructure_exception)
            self.audits.append(audit)
        if fault is not None:
            if session.failed is not None: raise session.failed
            # Supervisor terminal failures become replayable, host-latched messages.
            code = fault.code if fault.code in FAULTS else 'JS_EXITED'
            MockClient(session).fault(code)


def execute_candidate_source(*args, **kwargs):
    raise EvaluationError('UNREVIEWED_SOURCE_BLOCKED')
