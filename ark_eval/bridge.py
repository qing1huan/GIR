"""Non-executing JSON envelope bridge, NOT a JavaScript interpreter/baseline."""
from headless_v01.compiler import compile_program
from headless_v01.types import Fault
from .adapter import EvaluationError, bounded_json


def decode_data_program(text, representation):
    if representation == "wrapper_format":
        prefix = "export default defineGame("
        endings = (");", ") satisfies CoreProgram;")
        text = text.strip()
        ending = next((e for e in endings if text.endswith(e)), None)
        if not text.startswith(prefix) or ending is None:
            raise EvaluationError("NON_DATA_JS_REJECTED")
        text = text[len(prefix):-len(ending)]
    elif representation != "gameir_text":
        raise EvaluationError("UNKNOWN_REPRESENTATION")
    spec = bounded_json(text)
    if not isinstance(spec, dict):
        raise EvaluationError("INVALID_PROGRAM")
    try:
        return compile_program(spec)
    except (Fault, RecursionError):
        raise EvaluationError("PROGRAM_VALIDATION_FAILED") from None


def execute_candidate(*args, **kwargs):
    raise EvaluationError("GENERATED_CODE_ISOLATION_UNVERIFIED")
