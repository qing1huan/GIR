"""Append-only hash-chain audit with reservation before each offline attempt."""
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
import copy

from headless_v01.compiler import digest
from .adapter import EvaluationError, ImportedResponse, MODEL, PlanConfig, PreparedRequest, integer, validate_prepared


@dataclass(frozen=True)
class Budget:
    prompt_units: int = 24000
    output_tokens: int = 4096
    attempts: int = 3
    repairs: int = 2
    tool_calls: int = 3

    def __post_init__(self):
        for value in asdict(self).values():
            integer(value)
        if self.attempts < 1 or self.repairs >= self.attempts:
            raise EvaluationError("INVALID_BUDGET")


class Ledger:
    def __init__(self, budget=None, requested_model=MODEL):
        self.budget = budget or Budget()
        PlanConfig(model=requested_model)
        self.requested_model = requested_model
        self._events = []
        self._pending = None
        self._used = {k: 0 for k in asdict(self.budget)}
        self.halted = False

    def _append(self, event):
        record = {"sequence": len(self._events), "previous": self._events[-1]["hash"] if self._events else None,
                  "timestamp_utc": datetime.now(UTC).isoformat(),
                  **event}
        record["hash"] = digest(record)
        self._events.append(record)

    def reserve(self, request, repair=False):
        if self.halted or self._pending is not None:
            raise EvaluationError("LEDGER_HALTED_OR_PENDING")
        if not isinstance(request, PreparedRequest) or type(repair) is not bool:
            raise EvaluationError("INVALID_RESERVATION")
        validate_prepared(request)
        if request.body['model']!=self.requested_model:
            raise EvaluationError('RESERVATION_MODEL_MISMATCH')
        if repair != (self._used["attempts"] > 0):
            raise EvaluationError("REPAIR_CLASSIFICATION_REQUIRED")
        charges = {"prompt_units": request.prompt_units, "output_tokens": request.max_output_tokens,
                   "attempts": 1, "repairs": int(repair), "tool_calls": 0}
        for key, amount in charges.items():
            integer(amount)
            if self._used[key] + amount > getattr(self.budget, key):
                self._append({"event": "REJECTED", "resource": key, "status": "NOT_RUN"})
                raise EvaluationError("BUDGET_EXHAUSTED")
        self._used = {k: self._used[k] + v for k, v in charges.items()}
        call_id = self._used["attempts"]
        self._pending = (call_id, request)
        self._append({"event": "RESERVED", "call_id": call_id, "request_digest": request.request_digest,
                      "repair": repair, "charges": charges})
        return call_id

    def tool_call(self):
        if self.halted or self._pending is not None:
            raise EvaluationError("LEDGER_HALTED_OR_PENDING")
        if self._used["tool_calls"] >= self.budget.tool_calls:
            self._append({"event": "REJECTED", "resource": "tool_calls", "status": "NOT_RUN"})
            raise EvaluationError("BUDGET_EXHAUSTED")
        self._used["tool_calls"] += 1
        self._append({"event": "TOOL_CALL", "count": self._used["tool_calls"]})

    def finish(self, call_id, response=None, failure=None):
        integer(call_id, 1)
        if self._pending is None or self._pending[0] != call_id:
            raise EvaluationError("UNKNOWN_OR_DUPLICATE_CALL")
        if (response is None) == (failure is None):
            raise EvaluationError("ONE_OUTCOME_REQUIRED")
        if response is not None and not isinstance(response, ImportedResponse):
            raise EvaluationError("INVALID_OUTCOME")
        if response is not None:
            integer(response.tokens_in)
            integer(response.tokens_out)
            if (not isinstance(response.candidate, str)
                    or response.candidate_digest != digest(response.candidate)
                    or not isinstance(response.model_identity_digest, str)
                    or len(response.model_identity_digest) != 64
                    or any(c not in "0123456789abcdef" for c in response.model_identity_digest)
                    or type(response.model_matches_requested) is not bool
                    or response.model_matches_requested != (response.model_identity_digest == digest(self.requested_model))
                    or response.finish_reason not in {"stop", "length"}):
                raise EvaluationError("INVALID_OUTCOME")
        if failure is not None and (not isinstance(failure, str) or failure not in {"TIMEOUT", "QUOTA", "AUTH", "INVALID_RESPONSE", "TRANSPORT"}):
            raise EvaluationError("INVALID_FAILURE_CODE")
        _, request = self._pending
        self._pending = None
        event = {"event": "FINISHED", "call_id": call_id, "status": "FAIL"}
        if response is not None:
            event.update({"tokens_in": response.tokens_in, "tokens_out": response.tokens_out,
                          "candidate_digest": response.candidate_digest,
                          "model_identity_digest": response.model_identity_digest,
                          "finish_reason": response.finish_reason})
            if response.tokens_in > request.prompt_units or response.tokens_out > request.max_output_tokens:
                event["failure"] = "USAGE_EXCEEDS_RESERVATION"
                self.halted = True
                self._used["prompt_units"] += max(0, response.tokens_in - request.prompt_units)
                self._used["output_tokens"] += max(0, response.tokens_out - request.max_output_tokens)
            elif not response.model_matches_requested:
                event["failure"] = "MODEL_MISMATCH"
                self.halted = True
            else:
                event["status"] = "RESPONSE_IMPORTED"  # Never gameplay PASS.
                # Only definitive valid usage can release a reservation.
                self._used["prompt_units"] -= request.prompt_units - response.tokens_in
                self._used["output_tokens"] -= request.max_output_tokens - response.tokens_out
        else:
            event.update({"failure": failure, "tokens_in": None, "tokens_out": None})
            # Unknown usage retains the full reservation. Quota/auth require human action.
            if failure in {"QUOTA", "AUTH"}:
                self.halted = True
        self._append(event)

    def export(self):
        return copy.deepcopy({"mode": "OFFLINE", "token_accounting": "UNVERIFIED_BYTE_UNITS_PLUS_REPORTED_USAGE",
                              "budget": asdict(self.budget), "charged": self._used, "halted": self.halted,
                              "pending_call": self._pending[0] if self._pending else None,
                              "events": self._events, "real_model_calls": 0})


def verify_chain(events):
    previous = None
    for index, record in enumerate(events):
        body = {k: v for k, v in record.items() if k != "hash"}
        if record.get("sequence") != index or record.get("previous") != previous or digest(body) != record.get("hash"):
            return False
        previous = record["hash"]
    return True  # Integrity check, not a digital signature or proof of completeness.
