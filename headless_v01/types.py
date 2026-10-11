"""Closed types, bounded JSON values and canonical persistence."""
import copy
import json
import math

SAFE_INT = 2**53 - 1
LIMITS = {"type_depth": 16, "value_depth": 32, "length": 1024,
          "string_bytes": 65536, "value_nodes": 16384, "value_bytes": 1048576,
          "ast_nodes": 10000, "ast_depth": 64}


class Fault(Exception):
    def __init__(self, code, details=None, phase="runtime", source=None):
        self.code, self.details, self.phase, self.source = code, details or {}, phase, source
        super().__init__(code + ": " + str(self.details))


def fail(code, **details):
    raise Fault(code, details)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def validate_type(type_, limits=None, depth=1):
    limits = {**LIMITS, **(limits or {})}
    if depth > limits["type_depth"]:
        fail("BudgetExceeded", resource="type_depth", attempted=depth)
    if isinstance(type_, str):
        if type_ not in {"bool", "int", "number", "string", "vec2", "EntityID"}:
            fail("TypeError", unknown_type=type_)
        return
    if not isinstance(type_, dict) or len(type_) != 1:
        fail("TypeError", type=type_)
    kind, spec = next(iter(type_.items()))
    if kind in {"list", "map", "option"}:
        validate_type(spec, limits, depth+1)
    elif kind == "record" and isinstance(spec, dict):
        for name, child in spec.items():
            if not isinstance(name, str): fail("TypeError", member=name)
            validate_type(child, limits, depth+1)
    elif kind == "enum" and isinstance(spec, list) and spec and all(isinstance(x, str) for x in spec) and len(set(spec)) == len(spec):
        return
    elif kind == "result" and isinstance(spec, dict) and set(spec) == {"ok", "err"}:
        for child in spec.values(): validate_type(child, limits, depth+1)
    else:
        fail("TypeError", type=type_)


def _measure(value, limits):
    nodes = 0

    def visit(x, depth):
        nonlocal nodes
        nodes += 1
        if nodes > limits["value_nodes"] or depth > limits["value_depth"]:
            fail("BudgetExceeded", resource="value_nodes/depth", nodes=nodes, depth=depth)
        if isinstance(x, str):
            try: size = len(x.encode("utf-8"))
            except UnicodeEncodeError: fail("TypeError", string="not Unicode scalar sequence")
            if size > limits["string_bytes"]: fail("BudgetExceeded", resource="string_bytes", attempted=size)
        elif isinstance(x, (dict, list)):
            if len(x) > limits["length"]: fail("BudgetExceeded", resource="container_length", attempted=len(x))
            if isinstance(x, dict):
                for k, v in x.items():
                    if not isinstance(k, str): fail("TypeError", key=k)
                    visit(k, depth+1); visit(v, depth+1)
            else:
                for item in x: visit(item, depth+1)
        elif x is None or type(x) in (int, float, bool):
            return
        else: fail("TypeError", value_kind=type(x).__name__)

    visit(value, 1)
    try: size = len(canonical_json(value).encode("utf-8"))
    except (ValueError, OverflowError): fail("NumericOverflow", value="nonfinite")
    if size > limits["value_bytes"]: fail("BudgetExceeded", resource="value_bytes", attempted=size)


def validate_value(type_, value, limits=None):
    limits = {**LIMITS, **(limits or {})}
    validate_type(type_, limits)
    _measure(value, limits)

    def check(t, v):
        if isinstance(t, str):
            if t == "bool":
                if type(v) is not bool: fail("TypeError", expected=t)
                return v
            if t == "int":
                if type(v) is not int: fail("TypeError", expected=t)
                if abs(v) > SAFE_INT: fail("NumericOverflow", value=v)
                return v
            if t == "number":
                if type(v) not in (int, float): fail("TypeError", expected=t)
                try: n = float(v)
                except OverflowError: fail("NumericOverflow", value=v)
                if not math.isfinite(n): fail("NumericOverflow", value=str(v))
                return 0.0 if n == 0 else n
            if t == "string":
                if type(v) is not str: fail("TypeError", expected=t)
                return v
            if t == "vec2":
                if type(v) is not list or len(v) != 2: fail("TypeError", expected=t)
                return [check("number", n) for n in v]
            if t == "EntityID":
                if type(v) is not list or len(v) != 4 or type(v[0]) is not str or type(v[2]) is not str:
                    fail("TypeError", expected=t)
                tick, ordinal = check("int", v[1]), check("int", v[3])
                if tick < -1 or ordinal < 0: fail("TypeError", expected=t)
                return [v[0], tick, v[2], ordinal]
        kind, spec = next(iter(t.items()))
        if kind == "enum":
            if type(v) is not str or v not in spec: fail("TypeError", expected=t)
            return v
        if kind == "record":
            if type(v) is not dict or set(v) != set(spec): fail("TypeError", expected=t)
            return {k: check(child, v[k]) for k, child in spec.items()}
        if kind == "map":
            if type(v) is not dict: fail("TypeError", expected=t)
            return {k: check(spec, n) for k, n in v.items()}
        if kind == "list":
            if type(v) is not list: fail("TypeError", expected=t)
            return [check(spec, n) for n in v]
        if kind == "option":
            if type(v) is not dict: fail("TypeError", expected=t)
            if v == {"none": True}: return {"none": True}
            if set(v) == {"some"}: return {"some": check(spec, v["some"])}
            fail("TypeError", expected=t)
        if kind == "result":
            if type(v) is not dict or len(v) != 1: fail("TypeError", expected=t)
            tag = next(iter(v))
            if tag not in spec: fail("TypeError", expected=t)
            return {tag: check(spec[tag], v[tag])}
        fail("TypeError", expected=t)

    return copy.deepcopy(check(type_, value))


def checked_numeric(type_, value):
    return validate_value(type_, value)
