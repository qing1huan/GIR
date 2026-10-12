"""One explicitly simulated attempt, with reservation and fixed failure codes."""
from .adapter import EvaluationError, SimulatedTransport, import_response


def simulate_attempt(ledger, request, transport, repair=False):
    if type(transport) is not SimulatedTransport:
        raise EvaluationError("SIMULATED_TRANSPORT_REQUIRED")
    call_id = ledger.reserve(request, repair=repair)
    try:
        raw = transport.respond(request)
        response = import_response(raw)
    except EvaluationError as error:
        code = str(error)
        ledger.finish(call_id, failure=code if code in {"QUOTA", "AUTH"} else "INVALID_RESPONSE")
        return None
    ledger.finish(call_id, response=response)
    return None if ledger.halted else response
