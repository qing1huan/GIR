"""Trusted transaction ctx and bounded, in-memory callback protocol.

World mutation remains in headless_v01.Job. Fixed Python controls, recorded
protocols, and the reviewed-only isolated Node runner share the same ctx boundary.
"""
import copy
import json
from dataclasses import dataclass

from headless_v01.compiler import digest
from headless_v01.runtime import Runtime
from headless_v01.types import Fault, canonical_json, validate_value
from .adapter import EvaluationError, bounded_json
from .callback_contract import HOST_LIMIT, MESSAGE_BYTES, REQUEST_LIMIT

VERSION = "trusted-ctx@1"
FAULTS = {"JS_EXCEPTION", "JS_TIMEOUT", "JS_DISCONNECTED", "JS_EXITED"}
ARGS = {"resolve": {"entity"}, "read": {"entity", "field"}, "require": {"option"},
        "int_sub": {"a", "b"}, "write": {"entity", "field", "value"},
        "add": {"entity", "field", "value"}, "emit": {"event", "payload"},
        "schedule": {"event", "payload", "delay"}, "draw": {"bound"}}


class TrustedContext:
    def __init__(self, job): self.job = job

    def call(self, op, args, sequence):
        if not isinstance(op, str) or op not in ARGS or not isinstance(args, dict) or set(args) != ARGS[op]:
            raise Fault("ProtocolViolation", phase="callback")
        j = self.job
        source = {"nodeID": "callback:" + j.handler["id"] + ":" + str(sequence),
                  "originChain": ["JSCallback", j.handler["id"], "ctx:" + op]}
        j.txn.source = source
        node = {"op": op, "_source": source}
        if "entity" in args: validate_value("EntityID", args["entity"])
        if op == "resolve": return j.host_resolve(args["entity"])
        if op in {"read", "write", "add"}:
            field = args["field"]
            if not isinstance(field, str) or field not in j.txn.core["fields"]:
                raise Fault("TypeError", phase="callback")
            schema = j.txn.core["fields"][field]
            if schema["authority"] != "IR": raise Fault("AuthorityViolation", phase="callback")
            if op == "read": return j.host_read(args["entity"], field)
            value = validate_value(schema["type"], args["value"])
            policy = schema.get("policy", "exclusive")
            if (op == "write" and policy == "ordered_add"
                    or op == "add" and (schema["type"] not in ("int", "number") or policy == "priority_set")):
                raise Fault("EffectViolation", phase="callback")
            j.host_write(node, args["entity"], field, value)
            return None
        if op == "require":
            option = args["option"]
            if not isinstance(option, dict) or (set(option) != {"some"} and
                    not (set(option) == {"none"} and option["none"] is True)):
                raise Fault("TypeError", phase="callback")
            if "none" in option: raise Fault("InvalidEntity", phase="callback")
            return copy.deepcopy(option["some"])
        if op == "int_sub":
            a = validate_value("int", args["a"]); b = validate_value("int", args["b"])
            return j.host_int_sub(a, b)
        if op in {"emit", "schedule"}:
            name = args["event"]
            if not isinstance(name, str): raise Fault("TypeError", phase="callback")
            payload = j.txn.runtime.check_event({"name": name, "payload": args["payload"]})
            delay = validate_value("int", args["delay"]) if op == "schedule" else None
            j.host_emit(node, name, payload, delay)
            return None
        if op == "draw": return j.host_draw(node, validate_value("int", args["bound"]))
        raise Fault("ProtocolViolation", phase="callback")


class ContextSession:
    def __init__(self, job):
        self.ctx = TrustedContext(job)
        self.invocation = digest([job.txn.tick, job.event["id"], job.handler["id"]])
        self.sequence = 0; self.wire_count = 0; self.cache = {}; self.closed = False; self.failed = None
        self.trace = {"invocation": self.invocation, "handler": job.handler["id"], "messages": []}
        job.txn.callback_transcripts.append(self.trace)

    def receive(self, raw):
        if self.failed is not None: raise self.failed
        request = None; response = None; rejection = None
        try:
            self.wire_count += 1
            txn = self.ctx.job.txn
            txn.callback_wire_count += 1
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MESSAGE_BYTES:
                raise Fault("BudgetExceeded", {"resource": "callback_message_bytes"}, "callback")
            request = bounded_json(raw)
            if self.wire_count > REQUEST_LIMIT or txn.callback_wire_count > 256:
                raise Fault("BudgetExceeded", {"resource": "callback_requests"}, "callback")
            if (not isinstance(request, dict) or request.get("v") != VERSION
                    or request.get("invocation") != self.invocation or type(request.get("seq")) is not int
                    or request["seq"] < 0):
                raise Fault("ProtocolViolation", phase="callback")
            seq = request["seq"]; fingerprint = digest(request)
            if seq in self.cache:
                previous, response = self.cache[seq]
                if fingerprint != previous: raise Fault("ProtocolViolation", {"reason": "duplicate_changed"}, "callback")
                self.trace["messages"].append({"request": request, "response": copy.deepcopy(response), "duplicate": True})
                return copy.deepcopy(response)
            if self.closed or seq != self.sequence: raise Fault("ProtocolViolation", phase="callback")
            base = {"v", "invocation", "seq", "kind"}
            response = {"v": VERSION, "invocation": self.invocation, "seq": seq, "kind": "return"}
            if request["kind"] == "call":
                if set(request) != base | {"op", "args"}: raise Fault("ProtocolViolation", phase="callback")
                response["value"] = self.ctx.call(request["op"], request["args"], seq)
            elif request["kind"] == "done":
                if set(request) != base: raise Fault("ProtocolViolation", phase="callback")
                self.closed = True; response["kind"] = "done"
            elif request["kind"] == "fault":
                if set(request) != base | {"code"} or not isinstance(request["code"], str) or request["code"] not in FAULTS:
                    raise Fault("ProtocolViolation", phase="callback")
                raise Fault(request["code"], phase="callback")
            else: raise Fault("ProtocolViolation", phase="callback")
            if len(canonical_json(response).encode("utf-8")) > MESSAGE_BYTES:
                raise Fault("BudgetExceeded", {"resource": "callback_response_bytes"}, "callback")
            self.sequence += 1; self.cache[seq] = (fingerprint, copy.deepcopy(response))
            self.trace["messages"].append({"request": request, "response": copy.deepcopy(response), "duplicate": False})
            return copy.deepcopy(response)
        except EvaluationError as error:
            # Preserve a fixed parser diagnostic, never the invalid raw bytes.
            rejection = {"kind": "INVALID_RAW_FRAME", "parser_code": str(error)}
            fault = Fault("ProtocolViolation", phase="callback")
        except (UnicodeError, ValueError, TypeError, KeyError, RecursionError):
            fault = Fault("ProtocolViolation", phase="callback")
        except Fault as error:
            fault = error
        # Latch faults: a worker cannot swallow a host error and then finish.
        self.failed = fault
        diagnostic = {"request": request if request is not None and isinstance(raw, str) and len(raw) <= MESSAGE_BYTES else None,
                      "response": {"kind": "error", "code": fault.code}, "duplicate": False}
        if rejection is not None: diagnostic["rejection"] = rejection
        self.trace["messages"].append(diagnostic)
        raise fault


class MockClient:
    def __init__(self, session): self.session = session; self.last = None

    def send(self, kind, **values):
        request = {"v": VERSION, "invocation": self.session.invocation, "seq": self.session.sequence,
                   "kind": kind, **values}
        self.last = canonical_json(request)
        return self.session.receive(self.last)

    def call(self, op, **args): return self.send("call", op=op, args=args)["value"]
    def done(self): return self.send("done")
    def fault(self, code): return self.send("fault", code=code)
    def repeat_previous(self): return self.session.receive(self.last)


@dataclass(frozen=True)
class DoorMockRunner:
    cost: int = 1
    inverted: bool = False

    def __post_init__(self):
        if type(self.cost) is not int or self.cost not in {1, 2} or type(self.inverted) is not bool:
            raise Fault("TypeError", phase="callback")

    def run(self, session, payload):
        """Independent hand-written control through actual bounded ctx messages."""
        c = MockClient(session); p, d = payload["player"], payload["door"]
        if "none" in c.call("resolve", entity=p): c.done(); return
        if "none" in c.call("resolve", entity=d): c.done(); return
        opened = c.call("require", option=c.call("read", entity=d, field="open"))
        if opened: c.done(); return
        keys = c.call("require", option=c.call("read", entity=p, field="keys"))
        eligible = keys <= self.cost if self.inverted else keys >= self.cost
        if eligible:
            remaining = c.call("int_sub", a=keys, b=self.cost)
            c.call("write", entity=p, field="keys", value=remaining)
            c.call("write", entity=d, field="open", value=True)
            c.call("emit", event="Opened", payload={"door": d})
        else: c.call("emit", event="MissingKey", payload={"player": p})
        c.done()


@dataclass(frozen=True)
class ScriptedMockRunner:
    frames: tuple
    terminal: str = "done"

    def __post_init__(self):
        if (not isinstance(self.frames, (tuple, list)) or len(self.frames) > REQUEST_LIMIT
                or self.terminal not in FAULTS | {"done"}): raise Fault("TypeError", phase="callback")
        object.__setattr__(self, "frames", tuple(copy.deepcopy(self.frames)))

    def run(self, session, payload):
        c = MockClient(session)
        for frame in self.frames:
            if frame == {"repeat_previous": True}: c.repeat_previous()
            else: c.call(frame["op"], **frame["args"])
        c.done() if self.terminal == "done" else c.fault(self.terminal)


@dataclass(frozen=True)
class RecordedMockRunner:
    messages: tuple

    def run(self, session, payload):
        for item in self.messages:
            if item["request"] is None: raise Fault("ReplayUnsupported", phase="callback")
            try:
                response = session.receive(canonical_json(item["request"]))
            except Fault as error:
                if item["response"] != {"kind": "error", "code": error.code}:
                    raise Fault("ReplayMismatch", phase="callback") from None
                raise
            if response != item["response"]: raise Fault("ReplayMismatch", phase="callback")


class HostRuntime(Runtime):
    def __init__(self, program, entities, seed=1, budgets=None, capabilities=None,
                 runners=None, host_limit=HOST_LIMIT, recorded=None):
        if type(host_limit) is not int or not 0 <= host_limit <= HOST_LIMIT:
            raise Fault("BudgetExceeded", phase="callback")
        self.host_operation_limit = host_limit
        self.runners = dict(runners) if runners is not None else None
        self.recorded = copy.deepcopy(recorded)
        self.execution_history = []; self._current_transcripts = None
        self._isolated = False; self._step_source_executions = 0
        if self.runners is not None:
            from .js_runner import IsolatedJSRunner
            if any(type(r) not in {DoorMockRunner, ScriptedMockRunner, IsolatedJSRunner} for r in self.runners.values()):
                raise Fault("SOURCE_EXECUTION_DISABLED", phase="callback")
            self._isolated = any(type(r) is IsolatedJSRunner for r in self.runners.values())
        super().__init__(program, entities, seed, budgets, capabilities)
        if self.plan.get("folds"):
            raise Fault("UnsupportedHostPlan", {"reason": "phase2 uses unoptimized Core"}, "callback")
        if self.runners is not None or self.recorded is not None:
            if any(h["body"] for h in self.core["handlers"]): raise Fault("TypeError", phase="callback")
            if self.runners is not None and set(self.runners) != {h["id"] for h in self.core["handlers"]}:
                raise Fault("TypeError", phase="callback")

    def execute_job(self, job, body, payload):
        if self.runners is None and self.recorded is None:
            return super().execute_job(job, body, payload)
        session = ContextSession(job)
        try:
            if self.recorded is not None:
                if not self._current_transcripts: raise Fault("ReplayMismatch", phase="callback")
                expected = self._current_transcripts.pop(0)
                if expected["invocation"] != session.invocation or expected["handler"] != job.handler["id"]:
                    raise Fault("ReplayMismatch", phase="callback")
                runner = RecordedMockRunner(tuple(expected["messages"]))
            else: runner = self.runners[job.handler["id"]]
            try:
                runner.run(session, copy.deepcopy(payload))
            finally:
                if self._isolated and hasattr(runner, 'audits') and runner.audits:
                    self._step_source_executions += runner.audits[-1]['reviewed_source_executions']
            if session.failed is not None: raise session.failed
            if not session.closed: raise Fault("JS_EXITED", phase="callback")
        except Fault: raise
        except Exception:
            # Trusted simulator failures also fail closed; no traceback/provider text.
            if session.failed is not None: raise session.failed
            MockClient(session).fault("JS_EXCEPTION")

    def step(self, **kwargs):
        self._step_source_executions = 0
        if self.recorded is not None:
            index = len(self.history)
            if index >= len(self.recorded): raise Fault("ReplayMismatch", phase="callback")
            self._current_transcripts = copy.deepcopy(self.recorded[index])
        result = super().step(**kwargs)
        return result

    def finish_dispatch(self, txn):
        if self.recorded is not None and self._current_transcripts:
            raise Fault("ReplayMismatch", phase="callback")

    def capture_execution(self, txn, result):
        committed = result["status"] == "COMMITTED"
        self.execution_history.append({"host_operations": dict(txn.host_counts),
            "host_operations_total": sum(txn.host_counts.values()), "host_limit": self.host_operation_limit,
            "ir_ast_units": txn.ir_ast_units, "legacy_runtime_units": txn.counts["units"],
            "js_compute_units": None, "callback_requests": txn.callback_wire_count,
            "published_events": copy.deepcopy([e for e in result["events"] if not e["id"].startswith("root:")]) if committed else [],
            "published_commands": copy.deepcopy(result["commands"]) if committed else [],
            "transcripts": copy.deepcopy(txn.callback_transcripts),
            "source_executions": self._step_source_executions,
            "mode": "ISOLATED_REVIEWED_JS_CALLBACK_HOST" if self._isolated else
                    "IR_HOST" if self.runners is None and self.recorded is None else "SIMULATED_CALLBACK_HOST"})


def replay_host(program, entities, history, executions, seed=1, budgets=None, host_limit=HOST_LIMIT, callback=True):
    if not isinstance(history, list) or not isinstance(executions, list) or len(history) != len(executions):
        raise Fault("ReplayMismatch", {"reason": "history_execution_length"})
    # Invalid raw frames have no canonical request. Reject the replay before any
    # runtime is constructed; do not replace them with synthetic valid messages.
    for execution in executions:
        for transcript in execution.get("transcripts", []):
            if any(message.get("request") is None for message in transcript["messages"]):
                raise Fault("ReplayUnsupported", {"reason": "uncanonicalized_raw_frame"}, "callback")
    recorded = [e["transcripts"] for e in executions] if callback else None
    runtime = HostRuntime(program, entities, seed, budgets, host_limit=host_limit, recorded=recorded)
    for index, record in enumerate(history):
        actual = runtime.step(**record["request"])
        expected_execution = executions[index]
        replay_execution = runtime.execution_history[index]
        if expected_execution.get('mode') == 'ISOLATED_REVIEWED_JS_CALLBACK_HOST':
            # Physical Node launches are not performed during deterministic transcript replay.
            # Every logical host cost, operation, message, event, and result is still checked.
            expected_execution = {k:v for k,v in expected_execution.items() if k not in {'mode','source_executions'}}
            replay_execution = {k:v for k,v in replay_execution.items() if k not in {'mode','source_executions'}}
        if actual != record["result"] or replay_execution != expected_execution:
            raise Fault("ReplayMismatch", {"record": index})
    return runtime


def execute_js_source(*args, **kwargs):
    raise EvaluationError("SOURCE_EXECUTION_DISABLED_ISOLATION_UNVERIFIED")
