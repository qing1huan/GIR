"""Scripted service boundary for headless tests, not a physics or navigation engine."""
import copy

from .runtime import Runtime, replay
from .types import Fault


class ScriptedBackend:
    def __init__(self, query_values, acknowledgements, observations=None):
        self.query_values = copy.deepcopy(query_values); self.acknowledgements = copy.deepcopy(acknowledgements)
        self.observations = copy.deepcopy(observations or []); self.pending = []; self.delivered = []
        self.captures = 0; self.queries = 0; self.sends = 0

    def capture(self, epoch, requests):
        self.captures += 1; replies = self.pending; self.pending = []; responses = []
        for request in requests:
            self.queries += 1
            if request["service"] not in self.query_values: raise Fault("CapabilityMismatch", phase="service")
            responses.append({**copy.deepcopy(request), "epoch": epoch, "value": copy.deepcopy(self.query_values[request["service"]])})
        return copy.deepcopy(self.observations), copy.deepcopy(replies), responses

    def send(self, command):
        if command["id"] in self.delivered: return
        self.delivered.append(command["id"]); self.sends += 1
        if command["service"] not in self.acknowledgements: raise Fault("CapabilityMismatch", phase="service")
        template = copy.deepcopy(self.acknowledgements[command["service"]])
        template["payload"]["target"] = command["args"]["target"]; self.pending.append(template)

    def snapshot(self):
        return copy.deepcopy(self.__dict__)

    @classmethod
    def restore(cls, snapshot):
        b = cls(snapshot["query_values"], snapshot["acknowledgements"], snapshot["observations"])
        b.__dict__.update(copy.deepcopy(snapshot)); return b


class ServiceTapeAdapter:
    def __init__(self, runtime, backend): self.runtime, self.backend = runtime, backend

    def step(self, inputs=None, query_requests=None):
        observations, replies, responses = self.backend.capture(self.runtime.state["tick"], query_requests or [])
        result = self.runtime.step(inputs=inputs, observations=observations, replies=replies, query_responses=responses)
        if result["status"] == "COMMITTED":
            for command in result["commands"]: self.backend.send(command)
        return result

    def snapshot(self):
        return {"runtime": self.runtime.snapshot(), "driver": {"halted": self.runtime.halted, "failed_request": self.runtime.failed_request}, "mock_backend": self.backend.snapshot()}

    @classmethod
    def restore(cls, program, snapshot, capabilities):
        runtime = Runtime.restore(program, snapshot["runtime"], capabilities); runtime.restore_driver(snapshot["driver"])
        return cls(runtime, ScriptedBackend.restore(snapshot["mock_backend"]))

    @staticmethod
    def replay_frozen(program, initial, journal, **options):
        # No backend object enters this API; sampling and sending are excluded by construction.
        return replay(program, initial, journal, **options)
