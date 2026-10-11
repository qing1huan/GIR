"""Auditable lowering, full static checks, and cost-preserving constant-fold Plans."""
import copy
import hashlib
import json

from .types import Fault, LIMITS, canonical_json, validate_type, validate_value


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def lower_semantic(semantic):
    if semantic.get("version") != "semantic-v0.1": raise Fault("TypeError", phase="type")
    core = copy.deepcopy(semantic["base"])
    seen = set()
    def v(name): return {"op": "var", "name": name}
    def l(value, type_): return {"op": "literal", "type": type_, "value": value}
    def read(entity, field): return {"op": "require", "value": {"op": "read", "entity": entity, "field": field}}
    def binary(op, a, b): return {"op": op, "a": a, "b": b}
    def write(entity, field, value): return {"op": "write", "entity": entity, "field": field, "value": value}
    def record(items): return {"op": "record", "items": items}
    def emit(event, items): return {"op": "emit", "event": event, "payload": record(items)}
    def mark(value, chain):
        if isinstance(value, dict):
            if "op" in value: value["_origin"] = chain
            for key, child in list(value.items()):
                if key != "_origin": mark(child, chain)
        elif isinstance(value, list):
            for child in value: mark(child, chain)
    for node in semantic["nodes"]:
        if node["id"] in seen: raise Fault("TypeError", {"duplicate_node": node["id"]}, "type")
        seen.add(node["id"])
        if "coreOverride" in node:
            raise Fault("CoreOverrideConflict", {"node": node["id"], "reason": "use explicit CoreOverride library"}, "type")
        library = node["library"]
        if library == "Door@1":
            p, d = v("player"), v("door")
            body = [{"op": "if", "condition": binary("and",
                       {"op": "is_some", "value": {"op": "resolve", "entity": p}},
                       {"op": "is_some", "value": {"op": "resolve", "entity": d}}), "then": [
                {"op": "if", "condition": {"op": "not", "value": read(d, node["open_field"])}, "then": [
                    {"op": "if", "condition": binary("ge", read(p, node["key_field"]), l(1, "int")), "then": [
                        write(p, node["key_field"], binary("sub", read(p, node["key_field"]), l(1, "int"))),
                        write(d, node["open_field"], l(True, "bool")), emit("Opened", {"door": d}),
                    ], "else": [emit("MissingKey", {"player": p})]},
                ], "else": []},
            ], "else": []}]
            handler = {"id": node["id"], "event": node["event"], "priority": 0, "body": body}
        elif library == "Health@1":
            entity = v("entity"); life_type = core["fields"][node["life_field"]]["type"]
            condition = binary("and", binary("and", binary("gt", v("old"), l(0, "int")),
                                                binary("le", v("new"), l(0, "int"))),
                               binary("eq", read(entity, node["life_field"]), l("Alive", life_type)))
            handler = {"id": node["id"], "event": "FieldChanged:" + node["hp_field"], "priority": 0,
                       "body": [{"op": "if", "condition": condition, "then": [
                           write(entity, node["life_field"], l("Dead", life_type)), emit("Died", {"entity": entity}),
                       ], "else": []}]}
        elif library == "CoreOverride":
            handler = copy.deepcopy(node["handler"])
            handler["id"] = node["id"]
        else:
            raise Fault("CapabilityMismatch", {"library": library}, "type")
        mark(handler["body"], ["Semantic:"+node["id"], library])
        core["handlers"].append(handler)
    handlers = {h["id"]: h for h in core["handlers"] if h["id"] in seen}
    inputs = {id_: h["event"] for id_, h in handlers.items()}
    connected = set()
    def outputs(x):
        result = set()
        if isinstance(x, list):
            for child in x: result |= outputs(child)
        elif isinstance(x, dict) and "op" in x:
            if x["op"] in {"emit", "schedule"}: result.add(x["event"])
            for key, child in x.items():
                if key != "_origin" and not (x["op"] == "literal" and key == "value"): result |= outputs(child)
        return result
    for connection in semantic.get("connections", []):
        if set(connection) != {"from", "port", "to", "input"}: raise Fault("TypeError", {"reason": "closed event connection"}, "type")
        source, target = connection["from"], connection["to"]
        if source not in handlers or target not in handlers: raise Fault("UnknownNode", {"connection": connection}, "type")
        if connection["port"] not in outputs(handlers[source]["body"]) or connection["input"] != inputs[target]: raise Fault("UnknownPort", {"connection": connection}, "type")
        if target in connected: raise Fault("PortConflict", {"node": target}, "type")
        if core["events"].get(connection["port"]) != core["events"].get(inputs[target]): raise Fault("PortTypeMismatch", {"connection": connection}, "type")
        handlers[target]["event"] = connection["port"]; connected.add(target)
    return core


def compile_program(spec, limits=None):
    try: return _compile_program(spec, limits)
    except Fault: raise
    except (KeyError, TypeError, AttributeError, ValueError, UnicodeError) as error:
        raise Fault("TypeError", {"reason": "malformed closed AST", "kind": type(error).__name__}, "parse") from error


def _compile_program(spec, limits=None):
    if spec.get("version") == "semantic-v0.1": spec = lower_semantic(spec)
    if spec.get("_compiled"):
        payload = {k: v for k, v in spec.items() if k != "_digest"}
        if digest(payload) != spec.get("_digest"): raise Fault("PlanMismatch", phase="plan")
        # A serialized annotation/digest is not an authorization to skip static checking.
        def strip(value):
            if isinstance(value, list): return [strip(x) for x in value]
            if not isinstance(value, dict): return value
            if "op" in value and isinstance(value.get("_source"), dict) and "originChain" in value["_source"]:
                result = {k: copy.deepcopy(v) if value["op"] == "literal" and k == "value" else strip(v)
                          for k, v in value.items() if k not in {"_source", "_type"}}
                result["_origin"] = value["_source"]["originChain"][:-1]
                return result
            return {k: strip(v) for k, v in value.items()}
        spec = strip({k: v for k, v in spec.items() if k not in {"_compiled", "_digest"}})
    core = copy.deepcopy(spec)
    limits = {**LIMITS, **(limits or {})}
    nodes = 0
    def measure(x, depth=1):
        nonlocal nodes
        nodes += 1
        if nodes > limits["ast_nodes"] or depth > limits["ast_depth"]:
            raise Fault("BudgetExceeded", {"resource": "AST", "nodes": nodes, "depth": depth}, "parse")
        if isinstance(x, dict):
            for child in x.values(): measure(child, depth+1)
        elif isinstance(x, list):
            for child in x: measure(child, depth+1)
    measure(core)
    # JSON AST has value semantics: host-side aliases must become distinct occurrences.
    # Measure first so cycles/deep inputs fail before serialization recursion.
    try: core = json.loads(canonical_json(core))
    except (ValueError, UnicodeError, TypeError): raise Fault("TypeError", {"reason": "not canonical JSON AST"}, "parse")
    if core.get("version") != "core-v0.1" or set(core) - {"version", "fields", "events", "handlers", "services", "extensions"}:
        raise Fault("TypeError", {"reason": "closed Core root"}, "parse")
    for key in ["fields", "events", "services", "extensions"]:
        if not isinstance(core.get(key), dict): raise Fault("TypeError", {"section": key}, "parse")
    if not isinstance(core.get("handlers"), list): raise Fault("TypeError", phase="parse")
    fields, events = core["fields"], core["events"]
    for name, field in fields.items():
        if set(field) - {"type", "authority", "policy", "range", "clamp", "notify"}: raise Fault("TypeError", {"field": name}, "type")
        validate_type(field["type"], limits)
        if field.get("authority") not in {"IR", "Backend"} or field.get("policy", "exclusive") not in {"exclusive", "ordered_add", "priority_set"}:
            raise Fault("TypeError", {"field": name}, "type")
        if field.get("policy") == "ordered_add" and field["type"] not in ("int", "number"):
            raise Fault("TypeError", {"field": name, "reason": "numeric reducer"}, "type")
        if "range" in field:
            bounds = field["range"]
            if field["type"] not in ("int", "number") or not isinstance(bounds, list) or len(bounds) != 2:
                raise Fault("TypeError", {"field": name}, "type")
            for n in bounds: validate_value(field["type"], n, limits)
            if bounds[0] > bounds[1]: raise Fault("TypeError", {"field": name}, "type")
        if "clamp" in field:
            clamp = field["clamp"]
            if set(clamp) != {"min", "max_field"} or field["type"] not in ("int", "number"):
                raise Fault("TypeError", {"field": name}, "type")
            validate_value(field["type"], clamp["min"], limits)
            maximum = fields.get(clamp["max_field"])
            if not maximum or maximum["type"] != field["type"] or maximum["authority"] != "IR":
                raise Fault("TypeError", {"field": name, "reason": "clamp max ABI"}, "type")
        if field.get("notify"):
            if field["authority"] != "IR": raise Fault("AuthorityViolation", {"field": name}, "effect")
            derived = {"record": {"entity": "EntityID", "old": field["type"], "new": field["type"]}}
            event_name = "FieldChanged:"+name
            if event_name in events and events[event_name] != derived: raise Fault("TypeError", {"event": event_name}, "type")
            events[event_name] = derived
    if events.get("Tick") != {"record": {}}: raise Fault("TypeError", {"event": "Tick"}, "type")
    for name, type_ in events.items():
        validate_type(type_, limits)
        if not isinstance(type_, dict) or set(type_) != {"record"}: raise Fault("TypeError", {"event": name}, "type")
    for service, s in core["services"].items():
        if set(s) - {"kind", "input", "output", "determinism", "cost", "delivery", "resource_field"}:
            raise Fault("TypeError", {"service": service}, "type")
        if "@" not in service or s.get("kind") not in {"query", "command"} or type(s.get("cost")) is not int or s["cost"] < 1:
            raise Fault("TypeError", {"service": service}, "type")
        validate_type(s["input"], limits)
        if s["kind"] == "query":
            validate_type(s["output"], limits)
            if s.get("determinism") != "RecordedObservation": raise Fault("CapabilityMismatch", {"service": service}, "type")
        else:
            if s.get("delivery") not in {"idempotent", "at-most-once"} or s.get("determinism") != "BackendSemantic":
                raise Fault("CapabilityMismatch", {"service": service}, "type")
            if not isinstance(s["input"], dict) or "record" not in s["input"] or s.get("resource_field") not in s["input"]["record"]:
                raise Fault("TypeError", {"service": service, "reason": "explicit resource key"}, "type")
    counter = 0
    def mark(n):
        nonlocal counter
        counter += 1
        node_id = "core#"+str(counter)
        n["_source"] = {"nodeID": node_id, "originChain": list(n.pop("_origin", ["CoreOverride"]))+[node_id]}
    def require_type(actual, expected):
        if actual != expected: raise Fault("TypeError", {"actual": actual, "expected": expected}, "type")
    def field_type(n, write=False):
        if n.get("field") not in fields: raise Fault("TypeError", {"field": n.get("field")}, "type")
        f = fields[n["field"]]
        if write and f["authority"] != "IR": raise Fault("AuthorityViolation", {"field": n["field"]}, "effect")
        return f["type"]
    def expr(n, env, pure=False):
        if not isinstance(n, dict) or not isinstance(n.get("op"), str): raise Fault("TypeError", phase="parse")
        op = n["op"]
        keys = {"literal": {"type", "value"}, "var": {"name"}, "read": {"entity", "field"},
                "observe": {"entity", "field"}, "resolve": {"entity"}, "require": {"value"},
                "is_some": {"value"}, "is_ok": {"value"}, "select": {"condition", "then", "else"}, "vec2": {"x", "y"}, "not": {"value"}, "count": {"value"}, "map_keys": {"value"},
                "map_get": {"value", "key"}, "map_put": {"value", "key", "item"}, "map_remove": {"value", "key"},
                "at": {"value", "index"}, "append": {"value", "item"}, "remove_at": {"value", "index"},
                "record": {"items"}, "result": {"tag", "value", "ok_type", "err_type"}, "member": {"value", "name"}, "some": {"value"},
                "query": {"fields", "bind", "where"}, "map": {"items", "bind", "body"},
                "filter": {"items", "bind", "where"}, "nearest": {"items", "points", "origin"},
                "service_query": {"service", "args"}, "extension": {"extension", "input"}}
        for b in ["add", "sub", "mul", "div", "eq", "lt", "le", "gt", "ge", "and", "or", "contains", "concat"]: keys[b] = {"a", "b"}
        if op not in keys or set(n)-{"op", "_origin"}-keys[op]: raise Fault("TypeError", {"op": op, "reason": "unknown op/member"}, "parse")
        if pure and op in {"read", "observe", "resolve", "query", "service_query", "extension"}:
            raise Fault("EffectViolation", {"op": op}, "effect")
        def e(key): return expr(n[key], env, pure)
        if op == "literal":
            validate_value(n["type"], n["value"], limits); t = n["type"]
        elif op == "var":
            if n["name"] not in env: raise Fault("TypeError", {"variable": n["name"]}, "type")
            t = env[n["name"]]
        elif op in {"read", "observe", "resolve"}:
            require_type(e("entity"), "EntityID")
            if op == "resolve": t = {"option": "EntityID"}
            else:
                t = field_type(n)
                if fields[n["field"]]["authority"] != ("Backend" if op == "observe" else "IR"):
                    raise Fault("AuthorityViolation", {"op": op, "field": n["field"]}, "effect")
                if op == "read": t = {"option": t}
        elif op in {"require", "is_some", "some"}:
            child = e("value")
            if op == "some": t = {"option": child}
            else:
                if not isinstance(child, dict) or set(child) != {"option"}: raise Fault("TypeError", {"op": op}, "type")
                t = child["option"] if op == "require" else "bool"
        elif op == "not": require_type(e("value"), "bool"); t = "bool"
        elif op == "select": require_type(e("condition"), "bool"); t = e("then"); require_type(e("else"), t)
        elif op == "is_ok":
            child = e("value")
            if not isinstance(child, dict) or set(child) != {"result"}: raise Fault("TypeError", phase="type")
            t = "bool"
        elif op == "vec2": require_type(e("x"), "number"); require_type(e("y"), "number"); t = "vec2"
        elif op in {"add", "sub", "mul", "div", "eq", "lt", "le", "gt", "ge", "and", "or", "contains", "concat"}:
            a, b = e("a"), e("b")
            if op == "contains":
                if not isinstance(a, dict) or set(a) != {"list"}: raise Fault("TypeError", phase="type")
                require_type(a["list"], b); t = "bool"
            elif op == "concat":
                require_type(a, b)
                if not isinstance(a, dict) or set(a) != {"list"}: raise Fault("TypeError", phase="type")
                t = a
            else:
                require_type(a, b)
                if op in {"and", "or"}: require_type(a, "bool"); t = "bool"
                elif op == "eq": t = "bool"
                else:
                    if a not in ("int", "number"): raise Fault("TypeError", phase="type")
                    if op == "div": require_type(a, "number")
                    t = "bool" if op in {"lt", "le", "gt", "ge"} else a
        elif op == "query":
            if not isinstance(n["fields"], list) or any(x not in fields for x in n["fields"]): raise Fault("TypeError", phase="type")
            if any(fields[x]["authority"] != "IR" for x in n["fields"]): raise Fault("AuthorityViolation", phase="effect")
            if ("where" in n) != ("bind" in n): raise Fault("TypeError", {"reason": "query bind/where pair"}, "type")
            if "where" in n:
                inner = dict(env); inner[n["bind"]] = "EntityID"
                require_type(expr(n["where"], inner, pure), "bool")
            t = {"list": "EntityID"}
        elif op in {"map", "filter"}:
            items = e("items")
            if not isinstance(items, dict) or set(items) != {"list"}: raise Fault("TypeError", phase="type")
            inner = dict(env); inner[n["bind"]] = items["list"]
            value_type = expr(n["body" if op == "map" else "where"], inner, pure)
            if op == "filter": require_type(value_type, "bool"); t = items
            else: t = {"list": value_type}
        elif op == "nearest":
            require_type(e("items"), {"list": "EntityID"}); require_type(e("points"), {"list": "vec2"}); require_type(e("origin"), "vec2")
            t = {"option": "EntityID"}
        elif op == "record": t = {"record": {name: expr(child, env, pure) for name, child in n["items"].items()}}
        elif op == "result":
            t = {"result": {"ok": n["ok_type"], "err": n["err_type"]}}
            if n["tag"] not in {"ok", "err"}: raise Fault("TypeError", phase="type")
            require_type(e("value"), t["result"][n["tag"]])
        elif op == "member":
            child = e("value")
            if not isinstance(child, dict) or "record" not in child or n["name"] not in child["record"]: raise Fault("TypeError", phase="type")
            t = child["record"][n["name"]]
        elif op in {"count", "map_keys", "map_get", "map_put", "map_remove", "at", "append", "remove_at"}:
            child = e("value")
            if op == "at" and child == "vec2":
                require_type(e("index"), "int"); n["_type"] = "number"; mark(n); return "number"
            if not isinstance(child, dict) or next(iter(child)) not in {"map", "list"}: raise Fault("TypeError", phase="type")
            if op == "count": t = "int"
            elif op.startswith("map_"):
                if "map" not in child: raise Fault("TypeError", phase="type")
                if op != "map_keys": require_type(e("key"), "string")
                if op == "map_put": require_type(e("item"), child["map"])
                t = {"list": "string"} if op == "map_keys" else {"option": child["map"]} if op == "map_get" else child
            else:
                if "list" not in child: raise Fault("TypeError", phase="type")
                if op == "append": require_type(e("item"), child["list"])
                else: require_type(e("index"), "int")
                t = child["list"] if op == "at" else child
        elif op == "service_query":
            service = core["services"].get(n["service"])
            if not service or service["kind"] != "query": raise Fault("CapabilityMismatch", phase="type")
            require_type(e("args"), service["input"]); t = service["output"]
        elif op == "extension":
            extension = core["extensions"].get(n["extension"])
            if not extension: raise Fault("CapabilityMismatch", phase="type")
            require_type(e("input"), extension["input"]); t = extension["output"]
        validate_type(t, limits); n["_type"] = copy.deepcopy(t); mark(n); return t
    def statements(body, env):
        if not isinstance(body, list): raise Fault("TypeError", phase="parse")
        for n in body:
            op = n.get("op")
            keys = {"let": {"name", "value"}, "write": {"entity", "field", "value"}, "add": {"entity", "field", "value"},
                    "assert": {"condition"}, "if": {"condition", "then", "else"}, "foreach": {"name", "items", "max", "body"},
                    "repeat": {"name", "count", "max", "body"}, "spawn": {"name", "values"}, "despawn": {"entity"},
                    "emit": {"event", "payload"}, "schedule": {"event", "payload", "delay"},
                    "draw": {"name", "bound"}, "command": {"service", "args"}}
            if op not in keys or set(n)-{"op", "_origin"}-keys[op]: raise Fault("TypeError", {"op": op}, "parse")
            if op == "let":
                t = expr(n["value"], env)
                if n["name"] in env: require_type(env[n["name"]], t)
                env[n["name"]] = t
            elif op in {"write", "add"}:
                require_type(expr(n["entity"], env), "EntityID"); t = field_type(n, True)
                require_type(expr(n["value"], env), t)
                policy = fields[n["field"]].get("policy", "exclusive")
                if op == "add" and (t not in ("int", "number") or policy == "priority_set") or op == "write" and policy == "ordered_add":
                    raise Fault("EffectViolation", {"op": op, "policy": policy}, "effect")
            elif op in {"assert", "if"}:
                require_type(expr(n["condition"], env), "bool")
                if op == "if": statements(n["then"], dict(env)); statements(n.get("else", []), dict(env))
            elif op in {"foreach", "repeat"}:
                if type(n["max"]) is not int or n["max"] < 0 or n["max"] > 1024: raise Fault("BudgetExceeded", {"loop_max": n["max"]}, "type")
                t = expr(n["items" if op == "foreach" else "count"], env)
                if op == "foreach":
                    if not isinstance(t, dict) or set(t) != {"list"}: raise Fault("TypeError", phase="type")
                    t = t["list"]
                else: require_type(t, "int")
                inner = dict(env); inner[n["name"]] = t; statements(n["body"], inner)
            elif op == "spawn":
                for name, value in n["values"].items():
                    t = field_type({"field": name}, True); require_type(expr(value, env), t)
                if n["name"] in env: require_type(env[n["name"]], "EntityID")
                env[n["name"]] = "EntityID"
            elif op == "despawn": require_type(expr(n["entity"], env), "EntityID")
            elif op in {"emit", "schedule"}:
                if n["event"] not in events or n["event"] == "Tick" or n["event"].startswith("FieldChanged:"):
                    raise Fault("TypeError", {"event": n["event"]}, "type")
                require_type(expr(n["payload"], env), events[n["event"]])
                if op == "schedule": require_type(expr(n["delay"], env), "int")
            elif op == "draw":
                require_type(expr(n["bound"], env), "int")
                if n["name"] in env: require_type(env[n["name"]], "int")
                env[n["name"]] = "int"
            elif op == "command":
                s = core["services"].get(n["service"])
                if not s or s["kind"] != "command": raise Fault("CapabilityMismatch", phase="effect")
                require_type(expr(n["args"], env), s["input"])
            mark(n)
    for name, extension in core["extensions"].items():
        if set(extension) != {"input", "output", "body", "cost"} or type(extension["cost"]) is not int or extension["cost"] < 1:
            raise Fault("TypeError", {"extension": name}, "type")
        validate_type(extension["input"], limits); validate_type(extension["output"], limits)
        require_type(expr(extension["body"], {"input": extension["input"]}, pure=True), extension["output"])
    ids = set()
    for h in core["handlers"]:
        if set(h) != {"id", "event", "priority", "body"} or not isinstance(h["id"], str) or h["id"] in ids or type(h["priority"]) is not int or h["event"] not in events:
            raise Fault("TypeError", {"handler": h.get("id")}, "type")
        ids.add(h["id"]); statements(h["body"], dict(events[h["event"]]["record"]))
    core["_compiled"] = True; core["_digest"] = digest(core)
    return core


def reexpand_semantic(semantic, overrides=None):
    core = lower_semantic(semantic)
    for node_id, override in (overrides or {}).items():
        index = next((i for i, h in enumerate(core["handlers"]) if h["id"] == node_id), None)
        if index is None: raise Fault("UnknownNode", {"node": node_id}, "type")
        if digest(core["handlers"][index]) != override["base_digest"]:
            raise Fault("CoreOverrideConflict", {"node": node_id, "reason": "automatic expansion changed"}, "type")
        manual = copy.deepcopy(override["handler"])
        if manual["id"] != node_id: raise Fault("TypeError", {"node": node_id}, "type")
        def mark_override(x):
            if isinstance(x, list):
                for item in x: mark_override(item)
            elif isinstance(x, dict) and "op" in x:
                x["_origin"] = ["Semantic:"+node_id, "CoreOverride"]
                for key, child in x.items():
                    if key != "_origin" and not (x["op"] == "literal" and key == "value"): mark_override(child)
        mark_override(manual["body"]); core["handlers"][index] = manual
    return compile_program(core)


def make_plan(core, optimize=False):
    core = compile_program(core)
    dispatch = {event: [h["id"] for h in sorted(core["handlers"], key=lambda h: (h["priority"], h["id"])) if h["event"] == event]
                for event in core["events"]}
    plan = {"version": "plan-v0.1", "core": core, "dispatch": dispatch,
            "optimization": "constant_fold@1" if optimize else "none", "core_digest": core["_digest"]}
    if optimize:
        folds = {}
        def constant(n):
            if not isinstance(n, dict) or "_source" not in n: return None
            op = n["op"]
            if op == "literal": return validate_value(n["_type"], n["value"]), [n["_source"]]
            if op not in {"add", "sub", "mul", "div", "eq", "lt", "le", "gt", "ge", "not"}: return None
            a = constant(n["value"] if op == "not" else n["a"])
            b = None if op == "not" else constant(n["b"])
            if a is None or op != "not" and b is None: return None
            av, at = a; bv, bt = b if b is not None else (None, [])
            try:
                if op == "add": value = av+bv
                elif op == "sub": value = av-bv
                elif op == "mul": value = av*bv
                elif op == "div": value = av/bv
                elif op == "eq": value = av == bv
                elif op == "lt": value = av < bv
                elif op == "le": value = av <= bv
                elif op == "gt": value = av > bv
                elif op == "ge": value = av >= bv
                else: value = not av
                return validate_value(n["_type"], value), [n["_source"]]+at+bt
            except (Fault, ArithmeticError): return None
        def collect(x):
            if isinstance(x, list):
                for child in x: collect(child)
            elif isinstance(x, dict):
                value = constant(x)
                if value is not None and x.get("op") != "literal":
                    folds[x["_source"]["nodeID"]] = {"value": value[0], "trace": value[1]}
                    return
                for key, child in x.items():
                    if key not in {"_source", "_type"} and not (x.get("op") == "literal" and key == "value"): collect(child)
        collect(core["handlers"]); collect([e["body"] for e in core["extensions"].values()])
        plan["folds"] = folds
    return plan
