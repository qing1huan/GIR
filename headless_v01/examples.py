"""Public development fixtures translated from the paper cases; no hidden tests."""
import json
from pathlib import Path
from .tests.fixtures import *


def record(items): return expr("record", items=items)
def event(name, **payload): return {"name": name, "payload": payload}
def semantic(core, label):
    handlers = core["handlers"]; base = clone(core); base["handlers"] = []
    return {"version": "semantic-v0.1", "base": base,
            "nodes": [{"id": h["id"], "library": "CoreOverride", "handler": h} for h in handlers]}
def case(id_, spec, entities, steps, expected, **kw):
    return {"id": id_, "development_set": True, "holdout_eligible": False, "program": spec,
            "initial": entities, "steps": steps, "expected": expected, **kw}


def cases():
    output = {}
    door = case("PT01", door_semantic(), [{"id": P, "values": {"key": 1}}, {"id": E, "values": {"open": False}}],
                [{"inputs": [event("Open", player=P, door=E)] * 2}],
                {"status": "COMMITTED", "values": [{"id": P, "field": "key", "value": 0}, {"id": E, "field": "open", "value": True}], "events": {"Opened": 1}})
    output["PT01"] = door
    stack = {"record": {"qty": "int"}}; bag = {"map": stack}
    qty = expr("member", value=expr("require", value=var("found")), name="qty")
    stacked = record({"qty": binary("add", qty, var("qty"))})
    body = [{"op": "let", "name": "inv", "value": read("stacks")},
            {"op": "let", "name": "found", "value": expr("map_get", value=var("inv"), key=var("item"))},
            {"op": "if", "condition": expr("is_some", value=var("found")), "then": [
                write("stacks", expr("map_put", value=var("inv"), key=var("item"), item=stacked))], "else": [
                    {"op": "if", "condition": binary("lt", expr("count", value=var("inv")), lit(2)), "then": [
                        write("stacks", expr("map_put", value=var("inv"), key=var("item"), item=record({"qty": var("qty")})))], "else": [emit("InventoryFull")]}]}]
    body = [{"op": "if", "condition": binary("gt", var("qty"), lit(0)), "then": body, "else": [emit("InvalidQuantity")]}]
    inventory = program(fields={"stacks": field(bag)}, events={"Pickup": {"record": {"item": "string", "qty": "int"}}, "InventoryFull": {"record": {}}, "InvalidQuantity": {"record": {}}}, handlers=[handler("bag", "Pickup", body)])
    output["PT02"] = case("PT02", semantic(inventory, "Inventory"), initial(stacks={}),
                          [{"inputs": [event("Pickup", item=item, qty=qty_) for item, qty_ in [("A", 2), ("B", 1), ("C", 1), ("A", 1)]]}],
                          {"status": "COMMITTED", "values": [{"id": P, "field": "stacks", "value": {"A": {"qty": 3}, "B": {"qty": 1}}}], "events": {"InventoryFull": 1}})
    output["PT02-invalid"] = case("PT02-invalid", semantic(inventory, "Inventory"), initial(stacks={}),
        [{"inputs": [event("Pickup", item="A", qty=0)]}], {"status": "COMMITTED", "values": [{"id": P, "field": "stacks", "value": {}}], "events": {"InvalidQuantity": 1}})
    output["PT02-overflow"] = case("PT02-overflow", semantic(inventory, "Inventory"), initial(stacks={"A": {"qty": 2**53-1}}),
        [{"inputs": [event("Pickup", item="A", qty=1)]}], {"status": "ABORTED", "error": "NumericOverflow", "rollback": True})
    found_wood = expr("map_get", value=var("inv"), key=lit("wood", "string"))
    wood_qty = expr("member", value=expr("require", value=var("wood")), name="qty")
    craft_body = [{"op": "let", "name": "inv", "value": read("stacks")}, {"op": "let", "name": "wood", "value": found_wood},
        {"op": "if", "condition": binary("and", expr("is_some", value=var("wood")), binary("ge", wood_qty, lit(2))), "then": [
            {"op": "let", "name": "next", "value": var("inv")},
            {"op": "if", "condition": binary("eq", wood_qty, lit(2)), "then": [
                {"op": "let", "name": "next", "value": expr("map_remove", value=var("next"), key=lit("wood", "string"))}], "else": [
                {"op": "let", "name": "next", "value": expr("map_put", value=var("next"), key=lit("wood", "string"), item=record({"qty": binary("sub", wood_qty, lit(2))}))}]},
            {"op": "let", "name": "next", "value": expr("map_put", value=var("next"), key=lit("plank", "string"), item=lit({"qty": 1}, stack))},
            {"op": "if", "condition": binary("le", expr("count", value=var("next")), lit(1)), "then": [write("stacks", var("next")), emit("Crafted")], "else": [emit("InventoryFull")]},
        ], "else": [emit("InsufficientMaterial")]}]
    craft = program(fields={"stacks": field(bag)}, events={n: {"record": {}} for n in ["Craft", "Crafted", "InventoryFull", "InsufficientMaterial"]}, handlers=[handler("recipe", "Craft", craft_body)])
    for id_, wood, result, emitted in [("PT09", 2, {"plank": {"qty": 1}}, "Crafted"), ("PT09-insufficient", 1, {"wood": {"qty": 1}}, "InsufficientMaterial"),
                                     ("PT09-full", 3, {"wood": {"qty": 3}}, "InventoryFull")]:
        output[id_] = case(id_, semantic(craft, "Craft"), initial(stacks={"wood": {"qty": wood}}), [{"inputs": [event("Craft")]}],
                           {"status": "COMMITTED", "values": [{"id": P, "field": "stacks", "value": result}], "events": {emitted: 1}})
    health = health_semantic(); base = health["base"]
    base["events"].update({name: {"record": {"drop": "EntityID"}} for name in ["DropReady", "Pickup", "Picked"]})
    base["handlers"].extend([
        handler("loot", "Died", [{"op": "spawn", "name": "d", "values": {"qty": lit(5)}}, {"op": "emit", "event": "DropReady", "payload": record({"drop": var("d")})}]),
        handler("ready", "DropReady", [{"op": "emit", "event": "Pickup", "payload": record({"drop": var("drop")})}] * 2),
        handler("pickup", "Pickup", [{"op": "if", "condition": expr("is_some", value=expr("resolve", entity=var("drop"))), "then": [
            add("gold", read("qty", var("drop"))), {"op": "despawn", "entity": var("drop")},
            {"op": "emit", "event": "Picked", "payload": record({"drop": var("drop")})}], "else": []}]),
    ])
    output["PT11"] = case("PT11", health, [{"id": P, "values": {"gold": 0}}, {"id": E, "values": {"hp": 2, "max": 2, "life": "Alive"}}],
                          [{"inputs": [event("Damage")]}], {"status": "COMMITTED", "entity_count": 2, "microsteps": 8,
                          "values": [{"id": P, "field": "gold", "value": 5}, {"id": E, "field": "hp", "value": 0}, {"id": E, "field": "life", "value": "Dead"}], "events": {"Died": 1, "Picked": 1}})
    service = {"Backend.createStaticRoom@1": {"kind": "command", "input": {"record": {"target": "EntityID", "point": "vec2"}},
               "determinism": "BackendSemantic", "cost": 1, "delivery": "idempotent", "resource_field": "target"}}
    room_body = [{"op": "let", "name": "points", "value": lit([[0., 0.], [4., 0.], [4., 4.]], {"list": "vec2"})},
        {"op": "let", "name": "ids", "value": lit([], {"list": "EntityID"})},
        {"op": "repeat", "name": "i", "count": lit(3), "max": 3, "body": [
            {"op": "spawn", "name": "room", "values": {"position": expr("at", value=var("points"), index=var("i"))}},
            {"op": "let", "name": "ids", "value": expr("append", value=var("ids"), item=var("room"))}]},
        {"op": "repeat", "name": "i", "count": lit(2), "max": 2, "body": [
            {"op": "spawn", "name": "link", "values": {"a": expr("at", value=var("ids"), index=var("i")), "b": expr("at", value=var("ids"), index=binary("add", var("i"), lit(1)))}}]},
        {"op": "foreach", "name": "room", "items": var("ids"), "max": 3, "body": [
            {"op": "command", "service": "Backend.createStaticRoom@1", "args": record({"target": var("room"), "point": read("position", var("room"))})}]},
    ]
    dungeon = program(fields={"position": field("vec2"), "a": field("EntityID"), "b": field("EntityID")}, handlers=[handler("rooms", "Go", room_body)], services=service)
    output["PT12"] = case("PT12", semantic(dungeon, "Dungeon"), [], [{"inputs": [event("Go")]}], {"status": "COMMITTED", "entity_count": 5, "commands": 3, "reachable_rooms": 3}, capabilities=list(service))
    nav_args = {"record": {"target": "EntityID", "point": "vec2"}}
    nav_result = {"result": {"ok": "string", "err": {"enum": ["NoPath"]}}}
    def offset(i): return expr("at", value=expr("at", value=var("offsets"), index=var("i")), index=lit(i))
    def coordinate(i): return binary("add", expr("at", value=var("origin"), index=lit(i)), offset(i))
    formation_body = [{"op": "let", "name": "ids", "value": expr("query", fields=["desired"])},
        {"op": "let", "name": "origin", "value": expr("observe", entity=lit(P, "EntityID"), field="pose")},
        {"op": "let", "name": "offsets", "value": lit([[-1., 0.], [0., -1.], [1., 0.]], {"list": "vec2"})},
        {"op": "repeat", "name": "i", "count": expr("count", value=var("ids")), "max": 3, "body": [
            {"op": "let", "name": "id", "value": expr("at", value=var("ids"), index=var("i"))},
            write("desired", expr("vec2", x=coordinate(0), y=coordinate(1)), var("id")),
            {"op": "command", "service": "Nav.moveTo@1", "args": record({"target": var("id"), "point": read("desired", var("id"))})}]}]
    followers = [E, N, ["world", -1, "genesis", 3]]
    nav = program(fields={"pose": field("vec2", authority="Backend"), "desired": field("vec2"), "move": field("string")},
        services={"Nav.moveTo@1": {"kind": "command", "input": nav_args, "determinism": "BackendSemantic", "cost": 1, "delivery": "idempotent", "resource_field": "target"}},
        events={"FormationRequest": {"record": {}}, "NavReply": {"record": {"target": "EntityID", "result": nav_result}}},
        handlers=[handler("formation", "FormationRequest", formation_body), handler("ack", "NavReply", [
            {"op": "if", "condition": expr("is_ok", value=var("result")), "then": [write("move", lit("Accepted", "string"), var("target"))], "else": [write("move", lit("Blocked", "string"), var("target"))]}])])
    output["PT13"] = case("PT13", semantic(nav, "Formation"), [{"id": P, "values": {}}]+[{"id": f, "values": {"desired": [0., 0.], "move": "Idle"}} for f in followers],
        [{"inputs": [event("FormationRequest")], "observations": [{"id": P, "values": {"pose": [0., 0.]}}]},
         {"replies": [event("NavReply", target=f, result={"ok": "Accepted"} if i < 2 else {"err": "NoPath"}) for i, f in enumerate(followers)]}],
        {"status": "COMMITTED", "commands": 3, "values": [{"id": f, "field": "move", "value": "Accepted" if i < 2 else "Blocked"} for i, f in enumerate(followers)] +
                     [{"id": f, "field": "desired", "value": point} for f, point in zip(followers, [[-1., 0.], [0., -1.], [1., 0.]])]}, capabilities=["Nav.moveTo@1"])
    conflict = program(handlers=[handler("reward", "Reward", [add("x", lit(3))]), handler("a", "Go", [write("x", lit(1)), {"op": "spawn", "name": "d", "values": {"x": lit(1)}}, {"op": "draw", "name": "n", "bound": lit(10)}]),
                                handler("b", "Go", [write("x", lit(2))])], events={"Reward": {"record": {}}})
    output["PT14"] = case("PT14", semantic(conflict, "Conflict"), initial(x=0), [{"inputs": [event("Reward"), event("Go")]}], {"status": "ABORTED", "error": "WriteConflict", "rollback": True})
    cycle = program(handlers=[handler("ping", "Go", [add("x", lit(1)), emit("Go")])])
    output["PT15"] = case("PT15", semantic(cycle, "Cycle"), initial(x=0), [{"inputs": [event("Go")]}], {"status": "ABORTED", "error": "BudgetExceeded", "rollback": True}, budgets={"microsteps": 4})
    return output


def main():
    destination = Path(__file__).resolve().parent / "cases"
    destination.mkdir(exist_ok=True)
    for id_, fixture in cases().items():
        (destination / (id_+".json")).write_text(json.dumps(fixture, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"public_cases_written": len(cases()), "destination": str(destination)}))


if __name__ == "__main__": main()
