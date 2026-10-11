"""Single-thread semantic reference. Backend commands are recorded, never executed."""
import copy
from collections import defaultdict, deque

from .compiler import compile_program, digest, make_plan
from .types import Fault, canonical_json, checked_numeric, validate_value

DEFAULT_BUDGETS = {"units": 20000, "microsteps": 32, "effects": 8192, "accesses": 4096,
                   "events": 128, "future": 256, "live": 2048, "newborn": 64,
                   "query": 1024, "commands": 64}
RNG_ALGORITHM = "xorshift32-rejection@1"
NUMERIC = "binary64-python3.12-reference@1"


def entity_key(id_):
    return tuple(validate_value("EntityID", id_))


class Runtime:
    def __init__(self, program, entities, seed=1, budgets=None, capabilities=None):
        if program.get("version") == "plan-v0.1":
            self.core = compile_program(program["core"])
            optimized = program.get("optimization") == "constant_fold@1"
            if make_plan(self.core, optimize=optimized) != program: raise Fault("PlanMismatch", phase="plan")
            plan = copy.deepcopy(program)
        else: self.core = compile_program(program)
        self.plan = plan if program.get("version") == "plan-v0.1" else make_plan(self.core)
        self.capabilities = sorted(set(capabilities or []))
        missing = set(self.core["services"])-set(self.capabilities)
        if missing: raise Fault("CapabilityMismatch", {"missing": sorted(missing)}, "service")
        self.budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
        if set(self.budgets) != set(DEFAULT_BUDGETS) or any(type(n) is not int or n < 0 or n > DEFAULT_BUDGETS[k] for k, n in self.budgets.items()):
            raise Fault("BudgetExceeded", {"reason": "profiles may only lower defaults"}, "runtime")
        if type(seed) is not int or not 0 < seed < 2**32: raise Fault("TypeError", {"seed": seed}, "parse")
        world = {}
        for entity in entities:
            if set(entity) != {"id", "values"}: raise Fault("TypeError", phase="parse")
            key = entity_key(entity["id"])
            if key in world: raise Fault("InvalidEntity", {"duplicate": list(key)})
            world[key] = self.check_fields(entity["values"])
        if len(world) > self.budgets["live"]: raise Fault("BudgetExceeded", {"resource": "live"})
        epochs = {key[0] for key in world}
        if len(epochs) > 1: raise Fault("InvalidEntity", {"reason": "one world epoch"})
        self.check_invariants(world)
        self.state = {"format": "snapshot-v0.1", "program_digest": self.core["_digest"],
                      "numeric": NUMERIC, "tick": 0, "cursor": 0, "world_epoch": next(iter(epochs), "world"),
                      "rng": {"algorithm": RNG_ALGORITHM, "state": seed, "draws": 0},
                      "future": [], "outbox": [], "budgets": self.budgets,
                      "capabilities": self.capabilities}
        self.world = world
        self.history = []; self.halted = False; self.failed_request = None

    def check_fields(self, values):
        if not isinstance(values, dict): raise Fault("TypeError", phase="type")
        result = {}
        for field, value in values.items():
            schema = self.core["fields"].get(field)
            if not schema: raise Fault("TypeError", {"field": field}, "type")
            if schema["authority"] != "IR": raise Fault("AuthorityViolation", {"field": field}, "effect")
            result[field] = validate_value(schema["type"], value)
        return result

    def check_invariant(self, values, field, value):
        schema = self.core["fields"][field]
        if "range" in schema and not schema["range"][0] <= value <= schema["range"][1]:
            raise Fault("InvariantViolation", {"field": field, "value": value, "range": schema["range"]})
        if "clamp" in schema:
            clamp = schema["clamp"]; maximum = values.get(clamp["max_field"])
            if maximum is None or maximum < clamp["min"] or not clamp["min"] <= value <= maximum:
                raise Fault("InvariantViolation", {"field": field, "value": value, "reason": "commit range"})

    def check_invariants(self, world):
        for values in world.values():
            for field, value in values.items(): self.check_invariant(values, field, value)

    def snapshot(self):
        return copy.deepcopy({**self.state, "entities": [{"id": list(key), "values": value} for key, value in sorted(self.world.items())]})

    def restore_driver(self, driver):
        if not isinstance(driver, dict) or set(driver) != {"halted", "failed_request"} or type(driver["halted"]) is not bool:
            raise Fault("SnapshotSchemaMismatch", {"reason": "driver schema"}, "migration")
        request = driver["failed_request"]
        if driver["halted"]:
            if not isinstance(request, dict) or set(request) != {"inputs", "observations", "replies", "observation_events", "query_responses"} or not all(isinstance(v, list) for v in request.values()):
                raise Fault("SnapshotSchemaMismatch", {"reason": "failed request"}, "migration")
        elif request is not None: raise Fault("SnapshotSchemaMismatch", {"reason": "unhalted driver"}, "migration")
        self.halted, self.failed_request = driver["halted"], copy.deepcopy(request)

    @classmethod
    def restore(cls, program, snapshot, capabilities=None):
        core = compile_program(program["core"] if program.get("version") == "plan-v0.1" else program)
        expected = {"format", "program_digest", "numeric", "tick", "cursor", "world_epoch", "rng", "future", "outbox", "budgets", "capabilities", "entities"}
        if set(snapshot) != expected or snapshot["format"] != "snapshot-v0.1" or snapshot["program_digest"] != core["_digest"] or snapshot["numeric"] != NUMERIC or snapshot["rng"].get("algorithm") != RNG_ALGORITHM:
            raise Fault("SnapshotSchemaMismatch", phase="migration")
        for key in ["tick", "cursor"]:
            validate_value("int", snapshot[key])
            if snapshot[key] < 0: raise Fault("SnapshotSchemaMismatch", {"field": key}, "migration")
        if set(snapshot["rng"]) != {"algorithm", "state", "draws"}: raise Fault("SnapshotSchemaMismatch", phase="migration")
        validate_value("int", snapshot["rng"]["draws"])
        if snapshot["rng"]["draws"] < 0: raise Fault("SnapshotSchemaMismatch", phase="migration")
        validate_value("string", snapshot["world_epoch"])
        available = capabilities if capabilities is not None else []
        if not set(snapshot["capabilities"]) <= set(available): raise Fault("CapabilityMismatch", phase="migration")
        r = cls(program, snapshot["entities"], snapshot["rng"]["state"], snapshot["budgets"], snapshot["capabilities"])
        if any(key[0] != snapshot["world_epoch"] or key[1] >= snapshot["tick"] for key in r.world):
            raise Fault("SnapshotSchemaMismatch", {"reason": "entity epoch/time"}, "migration")
        if not isinstance(snapshot["future"], list) or len(snapshot["future"]) > r.budgets["future"] or not isinstance(snapshot["outbox"], list):
            raise Fault("SnapshotSchemaMismatch", phase="migration")
        seen = set()
        for item in snapshot["future"]:
            if set(item) != {"tick", "event"} or type(item["tick"]) is not int or item["tick"] < snapshot["tick"]:
                raise Fault("SnapshotSchemaMismatch", phase="migration")
            event = item["event"]
            validate_value("int", item["tick"])
            if set(event) != {"id", "name", "payload"} or not isinstance(event["id"], str) or event["id"] in seen:
                raise Fault("SnapshotSchemaMismatch", phase="migration")
            validate_value("string", event["id"])
            if not event["id"].startswith("schedule:"): raise Fault("SnapshotSchemaMismatch", {"reason": "future UID namespace"}, "migration")
            r.check_event(event, internal=False); seen.add(event["id"])
        command_ids = set()
        for command in snapshot["outbox"]:
            if set(command) != {"id", "service", "args", "source"} or command["service"] not in core["services"]:
                raise Fault("SnapshotSchemaMismatch", phase="migration")
            s = core["services"][command["service"]]
            if s["kind"] != "command": raise Fault("SnapshotSchemaMismatch", phase="migration")
            validate_value("string", command["id"])
            if command["id"] in command_ids: raise Fault("SnapshotSchemaMismatch", {"reason": "duplicate command UID"}, "migration")
            command_ids.add(command["id"])
            validate_value({"record": {"nodeID": "string", "originChain": {"list": "string"}}}, command["source"])
            validate_value(s["input"], command["args"])
        r.state = copy.deepcopy({k: v for k, v in snapshot.items() if k != "entities"})
        return r

    def check_event(self, event, internal=False):
        name = event.get("name")
        if name not in self.core["events"] or not internal and (name == "Tick" or name.startswith("FieldChanged:")):
            raise Fault("TypeError", {"event": name}, "type")
        return validate_value(self.core["events"][name], event.get("payload"))

    def step(self, inputs=None, observations=None, replies=None, observation_events=None, query_responses=None, control=None):
        request = copy.deepcopy({"inputs": [] if inputs is None else inputs,
                                 "observations": [] if observations is None else observations,
                                 "replies": [] if replies is None else replies,
                                 "observation_events": [] if observation_events is None else observation_events,
                                 "query_responses": [] if query_responses is None else query_responses, "control": control})
        txn = Transaction(self)
        try:
            if not all(isinstance(request[k], list) for k in ["inputs", "observations", "replies", "observation_events", "query_responses"]):
                raise Fault("TypeError", {"reason": "input classes must be arrays"}, "parse")
            if control not in {None, "retry"}: raise Fault("TypeError", {"control": control}, "parse")
            if self.halted and control != "retry": raise Fault("RuntimeHalted", {"action": "explicit retry required"})
            if not self.halted and control == "retry": raise Fault("TypeError", {"reason": "no failed tick"}, "parse")
            if self.halted and {k: v for k, v in request.items() if k != "control"} != self.failed_request:
                raise Fault("RetryInputMismatch", {"reason": "retry must preserve frozen input/observation/response journal"})
            txn.run(request)
            self.world, self.state = txn.world, txn.state
            self.halted = False; self.failed_request = None
            result = txn.result("COMMITTED")
        except Fault as fault:
            if not self.halted: self.failed_request = {k: copy.deepcopy(v) for k, v in request.items() if k != "control"}
            self.halted = True
            result = txn.result("ABORTED", fault)
        self.history.append({"request": request, "result": copy.deepcopy(result)})
        return result


class Transaction:
    def __init__(self, runtime):
        self.runtime, self.core = runtime, runtime.core
        self.world, self.state = copy.deepcopy(runtime.world), copy.deepcopy(runtime.state)
        self.tick = self.state["tick"]; self.microstep = -1
        self.counts = defaultdict(int); self.events = []; self.effects = []; self.reads = []; self.commands = []
        self.observations = {}; self.query_responses = []; self.job = None; self.source = None
        self.current_event = None

    def charge(self, resource="units", count=1):
        self.counts[resource] += count
        if self.counts[resource] > self.runtime.budgets[resource]:
            raise Fault("BudgetExceeded", {"resource": resource, "attempted": self.counts[resource], "limit": self.runtime.budgets[resource]})

    def access(self, op, entity=None, field=None, **extra):
        self.charge("accesses"); self.charge()
        self.reads.append({"op": op, "entity": entity, "field": field, "job": self.job,
                           "source": copy.deepcopy(self.source), **extra})

    def result(self, status, fault=None):
        error = None
        if fault:
            source = fault.source or self.source
            error = {"code": fault.code, "phase": fault.phase, "tick": self.tick, "microstep": self.microstep,
                     "job": self.job, "event": self.current_event["id"] if self.current_event else None,
                     "sourceNode": source.get("nodeID") if source else None,
                     "loweringTrace": source.get("originChain", []) if source else [],
                     "details": fault.details, "retryability": "explicit_retry_or_drop"}
        return copy.deepcopy({"status": status, "tick": self.tick, "events": self.events,
                              "reads": self.reads, "effects": self.effects,
                              "commands": self.commands if status == "COMMITTED" else [],
                              "error": error, "budget_attempted": dict(self.counts),
                              "state_digest": digest(self.runtime.snapshot())})

    def run(self, request):
        for observation in request["observations"]:
            if not isinstance(observation, dict) or set(observation) != {"id", "values"} or not isinstance(observation["values"], dict): raise Fault("TypeError", phase="parse")
            key = entity_key(observation["id"])
            if key in self.observations or key not in self.world: raise Fault("InvalidEntity", {"observation": list(key)})
            values = {}
            for field, value in observation["values"].items():
                schema = self.core["fields"].get(field)
                if not schema or schema["authority"] != "Backend": raise Fault("AuthorityViolation", {"field": field})
                values[field] = validate_value(schema["type"], value)
            self.observations[key] = values
        self.query_responses = request["query_responses"]
        response_keys = set()
        for response in self.query_responses:
            if not isinstance(response, dict) or set(response) != {"service", "args", "epoch", "value"}: raise Fault("TypeError", phase="service")
            service = self.core["services"].get(response["service"])
            if not service or service["kind"] != "query": raise Fault("CapabilityMismatch", phase="service")
            validate_value(service["input"], response["args"]); validate_value(service["output"], response["value"])
            validate_value("int", response["epoch"])
            key = canonical_json([response["service"], response["args"], response["epoch"]])
            if key in response_keys: raise Fault("TypeError", {"reason": "duplicate service response"}, "service")
            response_keys.add(key)
        due = [x["event"] for x in self.state["future"] if x["tick"] == self.tick]
        self.state["future"] = [x for x in self.state["future"] if x["tick"] != self.tick]
        def admit(events):
            result = []
            for event in events:
                if not isinstance(event, dict) or set(event) != {"name", "payload"}: raise Fault("TypeError", phase="parse")
                payload = self.runtime.check_event(event)
                result.append({"id": "root:"+str(self.state["cursor"]), "name": event["name"], "payload": payload})
                self.state["cursor"] += 1
            return result
        queue = deque(admit(request["replies"])+admit(request["inputs"])+copy.deepcopy(due)+admit(request["observation_events"]))
        queue.append({"id": "root:"+str(self.state["cursor"]), "name": "Tick", "payload": {}})
        self.state["cursor"] += 1
        while queue:
            self.current_event = queue[0]; self.job = None; self.source = None
            self.microstep += 1; self.charge("microsteps"); self.charge()
            event = queue.popleft(); self.events.append(copy.deepcopy(event))
            sm = copy.deepcopy(self.world); effects = []
            for handler_id in self.runtime.plan["dispatch"][event["name"]]:
                h = next(h for h in self.core["handlers"] if h["id"] == handler_id)
                self.job = canonical_json([h["priority"], h["id"], event["id"], ""])
                job = Job(self, h, event, sm)
                job.execute(h["body"], copy.deepcopy(event["payload"]))
                effects.extend(job.effects)
            emissions, notifications = self.merge(sm, effects, event)
            queue.extend(emissions + notifications)
        self.state["tick"] = checked_numeric("int", self.state["tick"]+1)
        self.state["outbox"].extend(copy.deepcopy(self.commands))

    def merge(self, sm, effects, event):
        cells = defaultdict(list); deletes = defaultdict(list); commands = defaultdict(list)
        for effect in effects:
            if effect["op"] in {"write", "add"}: cells[(tuple(effect["entity"]), effect["field"])].append(effect)
            elif effect["op"] == "despawn": deletes[tuple(effect["entity"])].append(effect)
            elif effect["op"] == "command":
                s = self.core["services"][effect["service"]]
                commands[(effect["service"], canonical_json(effect["args"][s["resource_field"]]))].append(effect)
        def conflict(intents, detail):
            self.job, self.source = intents[-1]["job"], intents[-1]["source"]
            raise Fault("WriteConflict", {"resource": detail, "jobs": [e["job"] for e in intents]})
        for key, intents in deletes.items():
            if len({e["job"] for e in intents}) > 1: conflict(intents, list(key))
            for (entity, field), writes in cells.items():
                if key == entity and any(e["job"] != intents[0]["job"] for e in writes): conflict(intents+writes, [list(key), field])
        for key, intents in commands.items():
            # Commands declare exclusive resource ownership, including multiple same-job commands.
            if len(intents) > 1: conflict(intents, list(key))
        world = copy.deepcopy(sm)
        for effect in effects:
            if effect["op"] == "spawn":
                id_ = tuple(effect["value"]["id"])
                if id_ in world: raise Fault("InvalidEntity", {"duplicate": list(id_)})
                world[id_] = copy.deepcopy(effect["value"]["values"])
        for (entity, field), intents in sorted(cells.items()):
            schema = self.core["fields"][field]; policy = schema.get("policy", "exclusive")
            jobs = {e["job"] for e in intents}
            if policy == "exclusive" and len(jobs) > 1: conflict(intents, [list(entity), field])
            if policy == "priority_set":
                top = max(e["priority"] for e in intents); winners = [e for e in intents if e["priority"] == top]
                if len({e["job"] for e in winners}) > 1: conflict(winners, [list(entity), field])
                intents = winners
            if entity not in world: raise Fault("InvalidEntity", {"entity": list(entity)})
            value = world[entity].get(field)
            for effect in intents:
                if effect["op"] == "write": value = copy.deepcopy(effect["value"])
                else:
                    if value is None: raise Fault("InvalidEntity", {"field": field})
                    value = checked_numeric(schema["type"], value+effect["value"])
            world[entity][field] = validate_value(schema["type"], value)
        # Cross-field commit invariants inspect all merged fields, independent of FieldID order.
        for (entity, field), intents in sorted(cells.items()):
            schema = self.core["fields"][field]
            if "clamp" in schema and any(e["op"] == "add" for e in intents):
                c = schema["clamp"]; maximum = world[entity].get(c["max_field"])
                self.schema_read(entity, field, c["max_field"])
                if maximum is None or maximum < c["min"]: raise Fault("InvariantViolation", {"field": field})
                value = max(c["min"], min(maximum, world[entity][field]))
                world[entity][field] = validate_value(schema["type"], value)
        for entity in deletes: world.pop(entity, None)
        if len(world) > self.runtime.budgets["live"]: raise Fault("BudgetExceeded", {"resource": "live", "attempted": len(world)})
        for entity, values in sorted(world.items()):
            for field, value in sorted(values.items()):
                self.charge()
                if "clamp" in self.core["fields"][field]: self.schema_read(entity, field, self.core["fields"][field]["clamp"]["max_field"])
                self.runtime.check_invariant(values, field, value)
        emissions, notifications = [], []
        for effect in effects:
            if effect["op"] == "emit": emissions.append(copy.deepcopy(effect["value"]))
            elif effect["op"] == "schedule":
                self.state["future"].append({"tick": checked_numeric("int", self.tick+effect["delay"]), "event": copy.deepcopy(effect["value"])})
                if len(self.state["future"]) > self.runtime.budgets["future"]: raise Fault("BudgetExceeded", {"resource": "future"})
            elif effect["op"] == "command":
                self.commands.append({"id": effect["id"], "service": effect["service"], "args": effect["args"], "source": effect["source"]})
        # Changes concern existing, retained cells; spawn initializes, despawn removes.
        for entity in sorted(set(sm)&set(world)):
            for field in sorted(set(sm[entity])&set(world[entity])):
                if self.core["fields"][field].get("notify") and sm[entity][field] != world[entity][field]:
                    self.charge("events"); self.charge()
                    payload = {"entity": list(entity), "old": sm[entity][field], "new": world[entity][field]}
                    name = "FieldChanged:"+field
                    validate_value(self.core["events"][name], payload)
                    notifications.append({"id": "changed:"+digest([event["id"], list(entity), field]), "name": name, "payload": copy.deepcopy(payload)})
        self.world = world
        return emissions, notifications

    def schema_read(self, entity, field, dependency):
        self.source = {"nodeID": "schema:"+field, "originChain": ["Schema:"+field, "CommitInvariant"]}
        self.access("invariant_dependency", list(entity), dependency)


class Job:
    def __init__(self, txn, handler, event, snapshot):
        self.txn, self.handler, self.event, self.sm = txn, handler, event, snapshot
        self.overlay = copy.deepcopy(snapshot); self.effects = []; self.spawn_ordinal = 0; self.emit_ordinal = 0

    def source(self, node):
        self.txn.source = node["_source"]
        self.txn.charge()

    def eval(self, node, env):
        self.source(node); op = node["op"]
        fold = self.txn.runtime.plan.get("folds", {}).get(node["_source"]["nodeID"])
        if fold is not None:
            for source in fold["trace"][1:]:
                self.txn.source = source; self.txn.charge()
            return validate_value(node["_type"], fold["value"])
        def e(key): return self.eval(node[key], env)
        if op == "literal": value = copy.deepcopy(node["value"])
        elif op == "var": value = copy.deepcopy(env[node["name"]])
        elif op in {"read", "observe", "resolve"}:
            id_ = e("entity"); key = entity_key(id_); self.txn.source = node["_source"]
            if op == "resolve":
                self.txn.access(op, id_); value = {"some": id_} if key in self.overlay else {"none": True}
            else:
                field = node["field"]; self.txn.access(op, id_, field)
                if op == "read":
                    values = self.overlay.get(key, {}); value = {"some": copy.deepcopy(values[field])} if field in values else {"none": True}
                else:
                    if key not in self.overlay or field not in self.txn.observations.get(key, {}): raise Fault("ObservationMissing", {"entity": id_, "field": field})
                    value = copy.deepcopy(self.txn.observations[key][field])
        elif op == "require":
            option = e("value")
            if "some" not in option: raise Fault("InvalidEntity", {"reason": "require(None)"}, source=node["_source"])
            value = option["some"]
        elif op == "is_some": value = "some" in e("value")
        elif op == "is_ok": value = "ok" in e("value")
        elif op == "vec2": value = [e("x"), e("y")]
        elif op == "some": value = {"some": e("value")}
        elif op == "not": value = not e("value")
        elif op == "select": value = e("then") if e("condition") else e("else")
        elif op in {"and", "or"}:
            # Explicit short circuit: invalid branch reads are not performed.
            a = e("a"); value = a and e("b") if op == "and" else a or e("b")
        elif op in {"add", "sub", "mul", "div", "eq", "lt", "le", "gt", "ge", "contains", "concat"}:
            a, b = e("a"), e("b")
            if op == "add": value = a+b
            elif op == "sub": value = a-b
            elif op == "mul": value = a*b
            elif op == "div":
                if b == 0: raise Fault("NumericOverflow", {"operation": "division by zero"}, source=node["_source"])
                value = a/b
            elif op == "eq": value = a == b
            elif op == "lt": value = a < b
            elif op == "le": value = a <= b
            elif op == "gt": value = a > b
            elif op == "ge": value = a >= b
            elif op == "contains": value = b in a
            elif op == "concat": value = a+b
        elif op == "query":
            value = []
            for key, fields in sorted(self.sm.items()):
                for field in node["fields"]:
                    self.txn.source = node["_source"]; self.txn.access("query_dependency", list(key), field, present=field in fields)
                if all(field in fields for field in node["fields"]):
                    selected = True
                    if "where" in node:
                        inner = dict(env); inner[node["bind"]] = list(key)
                        overlay = self.overlay
                        try:
                            self.overlay = self.sm
                            selected = self.eval(node["where"], inner)
                        finally: self.overlay = overlay
                    if selected: value.append(list(key))
            if len(value) > self.txn.runtime.budgets["query"]: raise Fault("BudgetExceeded", {"resource": "query", "attempted": len(value)}, source=node["_source"])
        elif op in {"map", "filter"}:
            items = e("items"); value = []
            for item in items:
                self.txn.charge(); inner = dict(env); inner[node["bind"]] = copy.deepcopy(item)
                result = self.eval(node["body" if op == "map" else "where"], inner)
                if op == "map": value.append(result)
                elif result: value.append(item)
        elif op == "nearest":
            ids, points, origin = e("items"), e("points"), e("origin")
            self.txn.source = node["_source"]
            if len(ids) != len(points): raise Fault("TypeError", {"reason": "nearest requires parallel lists"}, source=node["_source"])
            ranked = []
            for id_, point in zip(ids, points):
                self.txn.charge(count=5)
                dx = checked_numeric("number", point[0]-origin[0]); dy = checked_numeric("number", point[1]-origin[1])
                d2 = checked_numeric("number", checked_numeric("number", dx*dx)+checked_numeric("number", dy*dy))
                ranked.append((d2, entity_key(id_), id_))
            value = {"some": min(ranked)[2]} if ranked else {"none": True}
        elif op == "record": value = {k: self.eval(n, env) for k, n in node["items"].items()}
        elif op == "result": value = {node["tag"]: e("value")}
        elif op == "member": value = e("value")[node["name"]]
        elif op == "count": value = len(e("value"))
        elif op == "map_keys": value = sorted(e("value"))
        elif op == "map_get":
            container, key = e("value"), e("key"); value = {"some": container[key]} if key in container else {"none": True}
        elif op in {"map_put", "map_remove"}:
            value, key = e("value"), e("key")
            if op == "map_put": value[key] = e("item")
            else: value.pop(key, None)
        elif op in {"at", "remove_at"}:
            container, index = e("value"), e("index")
            if not 0 <= index < len(container): raise Fault("IndexOutOfRange", {"index": index}, source=node["_source"])
            if op == "at": value = container[index]
            else: container.pop(index); value = container
        elif op == "append": value = e("value"); value.append(e("item"))
        elif op == "service_query":
            args = e("args"); service = self.txn.core["services"][node["service"]]
            self.txn.charge(count=service["cost"])
            self.txn.source = node["_source"]; self.txn.access("service_query", service=node["service"], args=args, epoch=self.txn.tick)
            responses = [r for r in self.txn.query_responses if r["service"] == node["service"] and r["epoch"] == self.txn.tick and r["args"] == args]
            if not responses: raise Fault("ServiceResponseMissing", {"service": node["service"], "epoch": self.txn.tick}, "service", node["_source"])
            value = copy.deepcopy(responses[0]["value"])
        elif op == "extension":
            value = e("input"); extension = self.txn.core["extensions"][node["extension"]]
            self.txn.charge(count=extension["cost"])
            value = self.eval(extension["body"], {"input": value})
        else: raise Fault("TypeError", {"operation": op})
        try: return validate_value(node["_type"], value)
        except Fault as fault: fault.source = node["_source"]; raise

    def record(self, node, **values):
        self.txn.source = node["_source"]; self.txn.charge("effects"); self.txn.charge()
        effect = {"op": node["op"], "job": self.txn.job, "priority": self.handler["priority"],
                  "ordinal": len(self.effects), "source": copy.deepcopy(node["_source"]), **copy.deepcopy(values)}
        self.effects.append(effect); self.txn.effects.append(copy.deepcopy(effect))

    def execute(self, body, env):
        for node in body:
            self.source(node); op = node["op"]
            def e(key): return self.eval(node[key], env)
            if op == "let": env[node["name"]] = e("value")
            elif op in {"write", "add"}:
                id_, value = e("entity"), e("value"); key = entity_key(id_); field = node["field"]
                schema = self.txn.core["fields"][field]
                self.txn.source = node["_source"]; self.txn.charge("accesses")
                if key not in self.overlay: raise Fault("InvalidEntity", {"entity": id_})
                if schema.get("notify") and field not in self.overlay[key]: raise Fault("InvalidEntity", {"reason": "notified cell must be initialized by spawn", "field": field})
                if op == "write":
                    if "clamp" in schema: self.txn.schema_read(key, field, schema["clamp"]["max_field"])
                    self.txn.source = node["_source"]
                    self.txn.runtime.check_invariant(self.overlay[key], field, value); preview = value
                else:
                    self.txn.access("add_base", id_, field)
                    if field not in self.overlay[key]: raise Fault("InvalidEntity", {"entity": id_, "field": field})
                    preview = checked_numeric(schema["type"], self.overlay[key][field]+value)
                self.overlay[key][field] = copy.deepcopy(preview)
                self.record(node, entity=id_, field=field, value=value)
            elif op == "assert":
                if not e("condition"): raise Fault("AssertionFailed", source=node["_source"])
            elif op == "if":
                self.scoped(node["then"] if e("condition") else node.get("else", []), env)
            elif op in {"foreach", "repeat"}:
                items = e("items") if op == "foreach" else e("count")
                n = len(items) if op == "foreach" else items
                if n < 0 or n > node["max"]: raise Fault("BudgetExceeded", {"resource": "loop", "attempted": n, "max": node["max"]}, source=node["_source"])
                iterable = items if op == "foreach" else range(n)
                for value in iterable:
                    self.txn.charge(); self.scoped(node["body"], env, {node["name"]: value})
            elif op == "spawn":
                values = {k: self.eval(n, env) for k, n in node["values"].items()}
                values = self.txn.runtime.check_fields(values)
                for field, value in values.items():
                    if "clamp" in self.txn.core["fields"][field]:
                        self.txn.charge("accesses"); self.txn.charge()
                        self.txn.reads.append({"op": "spawn_invariant", "field": field, "dependency": self.txn.core["fields"][field]["clamp"]["max_field"], "job": self.txn.job, "source": node["_source"]})
                    self.txn.runtime.check_invariant(values, field, value)
                self.txn.charge("newborn")
                id_ = [self.txn.state["world_epoch"], self.txn.tick, self.txn.job, self.spawn_ordinal]
                self.spawn_ordinal += 1; self.overlay[tuple(id_)] = copy.deepcopy(values); env[node["name"]] = id_
                self.record(node, value={"id": id_, "values": values})
            elif op == "despawn":
                id_ = e("entity"); key = entity_key(id_)
                if key not in self.overlay: raise Fault("InvalidEntity", {"entity": id_}, source=node["_source"])
                self.overlay.pop(key); self.record(node, entity=id_)
            elif op in {"emit", "schedule"}:
                payload = e("payload")
                delay = e("delay") if op == "schedule" else None
                if delay is not None and delay < 1: raise Fault("TypeError", {"delay": delay}, source=node["_source"])
                self.txn.charge("events")
                identity = [op, self.event["id"], self.txn.job, self.emit_ordinal]
                id_ = op+":"+digest(identity); self.emit_ordinal += 1
                event = {"id": id_, "name": node["event"], "payload": copy.deepcopy(payload)}
                self.record(node, value=event, identity=identity, **({"delay": delay} if delay is not None else {}))
            elif op == "draw":
                bound = e("bound")
                if not 0 < bound <= 2**32: raise Fault("TypeError", {"bound": bound}, source=node["_source"])
                threshold = 2**32 - 2**32 % bound
                while True:  # Host rejection loop is charged and terminates on the same units budget.
                    self.txn.charge(); rng = self.txn.state["rng"]; x = rng["state"]
                    x ^= (x << 13) & 0xffffffff; x ^= x >> 17; x ^= (x << 5) & 0xffffffff; x &= 0xffffffff
                    rng["state"] = x; rng["draws"] += 1
                    if x < threshold: break
                env[node["name"]] = x % bound; self.record(node, value=env[node["name"]], bound=bound)
            elif op == "command":
                args = e("args"); service = self.txn.core["services"][node["service"]]
                resource = args[service["resource_field"]]
                if service["input"]["record"][service["resource_field"]] == "EntityID" and entity_key(resource) not in self.overlay:
                    raise Fault("InvalidEntity", {"target": resource}, source=node["_source"])
                self.txn.charge("commands"); self.txn.charge(count=service["cost"])
                id_ = canonical_json(["command", self.txn.tick, self.txn.job, len(self.effects)])
                self.record(node, id=id_, service=node["service"], args=args)
            else: raise Fault("TypeError", {"operation": op})

    def scoped(self, body, env, bindings=None):
        bindings = bindings or {}
        inner = copy.deepcopy(env); inner.update(copy.deepcopy(bindings))
        self.execute(body, inner)
        for name in set(env)-set(bindings): env[name] = inner[name]


def replay(program, entities, journal, seed=1, budgets=None, capabilities=None, checkpoint=None, driver=None):
    runtime = Runtime.restore(program, checkpoint, capabilities) if checkpoint else Runtime(program, entities, seed, budgets, capabilities)
    if driver is not None: runtime.restore_driver(driver)
    for index, record in enumerate(journal):
        actual = runtime.step(**record["request"])
        if actual != record["result"]: raise Fault("ReplayMismatch", {"record": index})
    return runtime
