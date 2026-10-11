from copy import deepcopy

P = ["world", -1, "genesis", 0]
E = ["world", -1, "genesis", 1]
N = ["world", -1, "genesis", 2]


def lit(value, type_="int"):
    return {"op": "literal", "type": type_, "value": value}


def var(name):
    return {"op": "var", "name": name}


def expr(op, **kwargs):
    return {"op": op, **kwargs}


def binary(op, a, b):
    return expr(op, a=a, b=b)


def read(field, entity=None):
    return expr("require", value=expr("read", entity=entity or lit(P, "EntityID"), field=field))


def write(field, value, entity=None):
    return {"op": "write", "entity": entity or lit(P, "EntityID"), "field": field, "value": value}


def add(field, value, entity=None):
    return {**write(field, value, entity), "op": "add"}


def emit(name, payload=None):
    return {"op": "emit", "event": name, "payload": payload or lit({}, {"record": {}})}


def handler(id_, event, body, priority=0):
    return {"id": id_, "event": event, "priority": priority, "body": body}


def field(type_="int", **kw):
    return {"type": type_, "authority": "IR", "policy": "exclusive", **kw}


def program(fields=None, handlers=None, events=None, **kw):
    return {"version": "core-v0.1", "fields": fields or {"x": field()},
            "events": {"Go": {"record": {}}, "Tick": {"record": {}}, **(events or {})},
            "handlers": handlers or [], "services": {}, "extensions": {}, **kw}


def initial(**values):
    return [{"id": P, "values": values or {"x": 0}}]


def door_semantic():
    return {"version": "semantic-v0.1", "base": program(
        fields={"key": field(range=[0, 10]), "open": field("bool")},
        events={"Open": {"record": {"player": "EntityID", "door": "EntityID"}},
                "Opened": {"record": {"door": "EntityID"}},
                "MissingKey": {"record": {"player": "EntityID"}}}),
        "nodes": [{"id": "door.open", "library": "Door@1", "event": "Open",
                   "key_field": "key", "open_field": "open"}]}


def health_semantic():
    return {"version": "semantic-v0.1", "base": program(
        fields={"hp": field(policy="ordered_add", clamp={"min": 0, "max_field": "max"}, notify=True),
                "max": field(range=[0, 100]), "life": field({"enum": ["Alive", "Dead"]}),
                "gold": field(policy="ordered_add"), "qty": field()},
        handlers=[handler("damage", "Damage", [add("hp", lit(-5), lit(E, "EntityID"))])],
        events={"Damage": {"record": {}}, "Died": {"record": {"entity": "EntityID"}}}),
        "nodes": [{"id": "health", "library": "Health@1", "hp_field": "hp", "life_field": "life"}]}


def clone(x):
    return deepcopy(x)
