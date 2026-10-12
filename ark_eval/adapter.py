"""Plan-only request preparation and strict, non-executing response import.

No environment reads, credentials, SDK, HTTP client, retries, or paid fallback.
The chat-completions envelope is a compatibility candidate, not a live probe.
"""
from dataclasses import dataclass
import copy
import json

from headless_v01.compiler import digest

PLAN_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
MODEL = "deepseek-v4.1-flash"
# Public identifier supplied by the user from their existing personal Plan UI.
# Inclusion is not proof of server availability; there is no alias fallback.
ACCOUNT_MODEL = "deepseek-v4-1-flash-260910"
MODEL_IDENTIFIERS = frozenset({MODEL, ACCOUNT_MODEL})
MAX_BYTES = 1048576


class EvaluationError(Exception):
    """Fixed error codes only: never include provider text or candidate content."""


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 10**9:
        raise EvaluationError("INVALID_INTEGER")
    return value


def bounded_json(raw):
    if not isinstance(raw, str):
        raise EvaluationError("INVALID_RESPONSE_SIZE")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeError:
        raise EvaluationError("INVALID_UNICODE") from None
    if size > MAX_BYTES:
        raise EvaluationError("INVALID_RESPONSE_SIZE")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvaluationError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise EvaluationError("NONFINITE_JSON")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise EvaluationError("INVALID_JSON") from None


@dataclass(frozen=True)
class PlanConfig:
    base_url: str = PLAN_URL
    model: str = MODEL

    def __post_init__(self):
        if self.base_url != PLAN_URL or not isinstance(self.model, str) or self.model not in MODEL_IDENTIFIERS:
            raise EvaluationError("PLAN_ROUTE_OR_MODEL_REJECTED")


@dataclass(frozen=True)
class PreparedRequest:
    body_json: str
    request_digest: str
    prompt_units: int
    max_output_tokens: int

    @property
    def body(self):
        return json.loads(self.body_json)


def prepare(messages, max_output_tokens=1024, config=None, *, thinking=None):
    config = config or PlanConfig()
    # Revalidate even if callers supply an object other than PlanConfig.
    try:
        PlanConfig(config.base_url, config.model)
    except AttributeError:
        raise EvaluationError("INVALID_CONFIG") from None
    integer(max_output_tokens, 1)
    # Only the explicitly frozen non-thinking pilot setting is supported.
    # None preserves legacy request bodies; never accept arbitrary extra fields.
    if thinking is not None and (not isinstance(thinking, dict) or thinking != {"type": "disabled"}):
        raise EvaluationError("THINKING_SETTING_REJECTED")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise EvaluationError("INVALID_MESSAGES")
    for m in messages:
        if (not isinstance(m, dict) or set(m) != {"role", "content"}
                or not isinstance(m["role"], str)
                or m["role"] not in {"system", "user", "assistant"}
                or not isinstance(m["content"], str)):
            raise EvaluationError("INVALID_MESSAGES")
    try:
        body = {"model": config.model, "messages": copy.deepcopy(messages),
                "max_tokens": max_output_tokens, "temperature": 0, "stream": False}
        if thinking is not None:
            body["thinking"] = {"type": "disabled"}
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_BYTES:
            raise EvaluationError("INVALID_REQUEST_SIZE")
        # Deliberately named units: byte+overhead estimate is NOT a verified tokenizer.
        units = sum(len(m["content"].encode("utf-8")) + 256 for m in messages)
        return PreparedRequest(raw, digest(body), units, max_output_tokens)
    except UnicodeError:
        raise EvaluationError("INVALID_UNICODE") from None


@dataclass(frozen=True)
class ImportedResponse:
    candidate: str  # Untrusted text, never executed or included in audit output.
    candidate_digest: str
    model_identity_digest: str
    model_matches_requested: bool
    tokens_in: int
    tokens_out: int
    finish_reason: str


def validate_prepared(request):
    if not isinstance(request, PreparedRequest):
        raise EvaluationError("INVALID_REQUEST")
    body = bounded_json(request.body_json)
    try:
        expected = prepare(body["messages"], body["max_tokens"], PlanConfig(PLAN_URL, body["model"]),
                           thinking=body.get("thinking"))
    except (KeyError, TypeError):
        raise EvaluationError("INVALID_REQUEST") from None
    if request != expected:
        raise EvaluationError("REQUEST_TAMPERED")
    return True


def import_response(raw, requested_model=MODEL):
    if not isinstance(requested_model, str) or requested_model not in MODEL_IDENTIFIERS:
        raise EvaluationError("PLAN_ROUTE_OR_MODEL_REJECTED")
    data = bounded_json(raw)
    try:
        if not isinstance(data, dict) or "error" in data:
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                code = data["error"].get("code")
                if code in {"QuotaExceeded", "InsufficientQuota", "insufficient_quota"}:
                    raise EvaluationError("QUOTA")
                if code in {"AuthenticationError", "Unauthorized", "invalid_api_key"}:
                    raise EvaluationError("AUTH")
            raise EvaluationError("PROVIDER_ERROR")
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise EvaluationError("INVALID_CHOICES")
        choice = choices[0]
        msg = choice["message"]
        if msg.get("tool_calls") or msg.get("function_call") or msg.get("role") != "assistant":
            raise EvaluationError("TOOLS_NOT_ALLOWED")
        content = msg["content"]
        model = data["model"]
        if not isinstance(content, str) or not isinstance(model, str) or not model:
            raise EvaluationError("INVALID_CONTENT_OR_MODEL")
        finish = choice["finish_reason"]
        if finish not in {"stop", "length"}:
            raise EvaluationError("INVALID_FINISH_REASON")
        usage = data["usage"]
        tin = integer(usage["prompt_tokens"])
        tout = integer(usage["completion_tokens"])
        if integer(usage["total_tokens"]) != tin + tout:
            raise EvaluationError("INCONSISTENT_USAGE")
        return ImportedResponse(content, digest(content), digest(model), model == requested_model,
                                tin, tout, finish)
    except (KeyError, TypeError, AttributeError, UnicodeError, RecursionError):
        raise EvaluationError("INVALID_RESPONSE") from None


class OfflinePlanAdapter:
    def prepare(self, messages, max_output_tokens=1024):
        return prepare(messages, max_output_tokens)

    def send(self, *args, **kwargs):
        # Cannot be enabled by changing a flag or providing a credential.
        raise EvaluationError("LIVE_TRANSPORT_DISABLED")


class SimulatedTransport:
    """Prewritten response queue, not an injectable callable or network transport."""
    def __init__(self, responses):
        self._responses = list(responses)
        if any(not isinstance(r, str) for r in self._responses):
            raise EvaluationError("INVALID_SIMULATION")
        self.calls = 0

    def respond(self, request):
        if not isinstance(request, PreparedRequest) or not self._responses:
            raise EvaluationError("SIMULATION_EXHAUSTED")
        self.calls += 1
        return self._responses.pop(0)
