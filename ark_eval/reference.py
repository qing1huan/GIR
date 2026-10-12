"""Evaluator-only hand-authored controls; NEVER imported by prompt construction.

These are source-visible development answers, not holdout or model submissions.
"""
from .tasks import base_program, task


def control_program(task_id, inverted=False):
    def node(op, **args): return {"op": op, **args}
    def var(name): return node("var", name=name)
    def lit(value): return node("literal", type="int", value=value)
    def read(entity, field): return node("require", value=node("read", entity=entity, field=field))
    def emit(name, key, value): return node("emit", event=name, payload=node("record", items={key: value}))
    def branch(condition, yes, no): return node("if", condition=condition, then=yes, **{"else": no})
    p, d = var("player"), var("door")
    cost = task(task_id)["cost"]
    rule = branch(node("le" if inverted else "ge", a=var("keys"), b=var("cost")), [
        node("write", entity=p, field="keys", value=node("sub", a=var("keys"), b=var("cost"))),
        node("write", entity=d, field="open", value=node("literal", type="bool", value=True)),
        emit("Opened", "door", d)], [emit("MissingKey", "player", p)])
    body = [branch(node("and", a=node("is_some", value=node("resolve", entity=p)),
                        b=node("is_some", value=node("resolve", entity=d))),
                   [branch(node("not", value=read(d, "open")), [
                       node("let", name="cost", value=lit(cost)),
                       node("let", name="keys", value=read(p, "keys")), rule], [])], [])]
    program = base_program()
    program["handlers"] = [{"id": "open", "event": "Open", "priority": 0, "body": body}]
    return program


def starter(task_id):
    if task_id == "P-C02-create": return None
    if task_id == "P-C02-modify": return control_program("P-C02-create")
    return control_program(task_id, inverted=True)
