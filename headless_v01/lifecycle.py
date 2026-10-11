"""Atomic definition controls; gameplay still executes on the single shared Core runtime."""
import copy

from .compiler import compile_program, digest, make_plan
from .runtime import Runtime, Transaction, Job, entity_key
from .types import Fault, checked_numeric, validate_type, validate_value


class Lifecycle:
    def __init__(self, base, initial, definitions, default, seed=1, budgets=None, capabilities=None):
        self.base = copy.deepcopy(base)
        abi_only = copy.deepcopy(self.base); abi_only["handlers"] = []
        compile_program(abi_only)  # Global handlers may reference installed namespaces.
        self.options = {"seed": seed, "budgets": budgets, "capabilities": capabilities or []}
        self.packages = {}
        for definition in definitions:
            definition = self.package(definition)
            if definition["id"] in self.packages: raise Fault("TypeError", {"reason": "duplicate definition"}, "type")
            self.packages[definition["id"]] = definition
        if default not in self.packages: raise Fault("CapabilityMismatch", {"definition": default}, "migration")
        self.default = default; self.registry = {}; self.serial = 0; self.revision = 0; self.in_tick = False
        self.runtime = Runtime(self.compose(), initial, **self.options)
        self.initial_config = {"base": copy.deepcopy(base), "initial": copy.deepcopy(initial), "definitions": copy.deepcopy(definitions), "default": default, **self.options}
        self.journal = []

    def abi(self):
        return digest({k: self.base[k] for k in ["fields", "events", "services"]})

    def package(self, definition):
        p = copy.deepcopy(definition)
        if "digest" in p:
            expected = p.pop("digest")
            if digest(p) != expected: raise Fault("SnapshotSchemaMismatch", {"reason": "package digest"}, "migration")
        if set(p)-{"id", "state_type", "handlers", "parameters", "parameter_type", "abi_digest"}:
            raise Fault("TypeError", {"reason": "closed definition"}, "type")
        validate_value("string", p["id"]); validate_type(p["state_type"]); validate_type(p["parameter_type"])
        if not isinstance(p["state_type"], dict) or "record" not in p["state_type"]: raise Fault("TypeError", {"reason": "typed state record"}, "type")
        p["parameters"] = validate_value(p["parameter_type"], p["parameters"])
        if p.get("abi_digest", self.abi()) != self.abi(): raise Fault("CapabilityMismatch", {"reason": "shared ABI changed"}, "migration")
        p["abi_digest"] = self.abi(); p["digest"] = digest(p)
        return p

    def compose(self, packages=None, base=None):
        base = copy.deepcopy(base or self.base); packages = self.packages if packages is None else packages
        for id_, package in sorted(packages.items()):
            field = "state:"+id_
            if field in base["fields"]: raise Fault("TypeError", {"reason": "reserved state namespace"}, "type")
            base["fields"][field] = {"type": package["state_type"], "authority": "IR", "policy": "exclusive"}
            for handler in package["handlers"]:
                h = copy.deepcopy(handler)
                abi = base["events"].get(h["event"], {}).get("record", {})
                if abi.get("instance") != "EntityID" or abi.get("definition") != "string":
                    raise Fault("TypeError", {"reason": "targeted instance event ABI"}, "type")
                h["id"] = "definition:"+id_+"/"+h["id"]
                h["body"] = [{"op": "if", "condition": {"op": "and",
                    "a": {"op": "eq", "a": {"op": "var", "name": "definition"}, "b": {"op": "literal", "type": "string", "value": id_}},
                    "b": {"op": "is_some", "value": {"op": "read", "entity": {"op": "var", "name": "instance"}, "field": field}}},
                    "then": h["body"], "else": []}]
                def mark(x):
                    if isinstance(x, list):
                        for child in x: mark(child)
                    elif isinstance(x, dict) and "op" in x:
                        x["_origin"] = ["Definition:"+id_, h["id"], "CoreOverride"]
                        for key, child in x.items():
                            if key != "_origin" and not (x["op"] == "literal" and key == "value"): mark(child)
                mark(h["body"]); base["handlers"].append(h)
        return compile_program(base)

    def boundary(self):
        if self.in_tick or self.runtime.halted: raise Fault("PatchBoundary", {"reason": "committed tick boundary required"}, "migration")

    def snapshot(self):
        return copy.deepcopy({"format": "lifecycle-v0.1", "base": self.base, "packages": self.packages,
            "default": self.default, "registry": [{"id": list(k), **v} for k, v in sorted(self.registry.items())],
            "serial": self.serial, "revision": self.revision, "runtime": self.runtime.snapshot(),
            "driver": {"halted": self.runtime.halted, "failed_request": self.runtime.failed_request}, "initial_config": self.initial_config})

    def record(self, op, args, outcome):
        self.journal.append({"op": op, "args": copy.deepcopy(args), "outcome": copy.deepcopy(outcome), "snapshot_digest": digest(self.snapshot())})

    def commit(self, core, snapshot):
        snapshot = copy.deepcopy(snapshot); snapshot["program_digest"] = core["_digest"]
        candidate = Runtime.restore(core, snapshot, self.options["capabilities"])
        candidate.history = copy.deepcopy(self.runtime.history)
        self.runtime = candidate

    def create_instance(self, state, definition=None, reservations=None):
        self.boundary(); id_ = definition or self.default
        if id_ not in self.packages: raise Fault("CapabilityMismatch", {"definition": id_}, "migration")
        state = validate_value(self.packages[id_]["state_type"], state)
        reservations = validate_value({"list": "string"}, [] if reservations is None else reservations)
        serial, revision = checked_numeric("int", self.serial+1), checked_numeric("int", self.revision+1)
        snapshot = self.runtime.snapshot(); entity = [snapshot["world_epoch"], snapshot["tick"]-1, "instance-control", self.serial]
        if tuple(entity) in self.runtime.world: raise Fault("SnapshotSchemaMismatch", {"reason": "reused control ID"}, "migration")
        snapshot["entities"].append({"id": entity, "values": {"state:"+id_: state}})
        self.commit(self.compose(), snapshot)
        self.registry[tuple(entity)] = {"definition": id_, "reservations": reservations, "subscriptions": [h["event"] for h in self.packages[id_]["handlers"]]}
        self.serial, self.revision = serial, revision
        self.record("create_instance", {"state": state, "definition": id_, "reservations": reservations}, entity)
        return entity

    def binding(self, id_):
        if entity_key(id_) not in self.registry: raise Fault("InvalidEntity", {"instance": id_}, "migration")
        return self.registry[entity_key(id_)]["definition"]

    def instance_state(self, id_):
        definition = self.binding(id_)
        return copy.deepcopy(self.runtime.world[entity_key(id_)]["state:"+definition])

    def target_event(self, name, id_, **payload):
        event = {"name": name, "payload": {**payload, "instance": id_, "definition": self.binding(id_)}}
        self.runtime.check_event(event)
        return event

    def step(self, **request):
        self.in_tick = True
        try:
            candidate = Runtime.restore(self.runtime.plan, self.runtime.snapshot(), self.options["capabilities"])
            candidate.restore_driver({"halted": self.runtime.halted, "failed_request": self.runtime.failed_request})
            candidate.history = copy.deepcopy(self.runtime.history)
            result = candidate.step(**request)
            registry = copy.deepcopy(self.registry)
            if result["status"] == "COMMITTED":
                try:
                    live = {}
                    for key, values in candidate.world.items():
                        states = [id_ for id_ in self.packages if "state:"+id_ in values]
                        if len(states) > 1 or key in registry and states != [registry[key]["definition"]]:
                            raise Fault("NamespaceViolation", {"entity": list(key), "states": states}, "runtime")
                        if states:
                            id_ = states[0]
                            live[key] = registry.get(key, {"definition": id_, "reservations": [], "subscriptions": [h["event"] for h in self.packages[id_]["handlers"]]})
                    registry = live
                except Fault as fault:
                    offending = next((e for e in reversed(result["effects"]) if e.get("field", "").startswith("state:")), None)
                    source = offending["source"] if offending else {"nodeID": "schema:namespace", "originChain": ["Lifecycle", "NamespaceOwnership"]}
                    result.update(status="ABORTED", commands=[], state_digest=digest(self.runtime.snapshot()), error={
                        "code": fault.code, "phase": fault.phase, "tick": result["tick"], "microstep": len(result["events"])-1,
                        "job": offending["job"] if offending else None, "event": result["events"][-1]["id"] if result["events"] else None,
                        "sourceNode": source["nodeID"], "loweringTrace": source["originChain"], "details": fault.details, "retryability": "explicit_retry_or_drop"})
                    self.runtime.halted = True; self.runtime.failed_request = {k: v for k, v in candidate.history[-1]["request"].items() if k != "control"}
                    self.runtime.history.append({"request": candidate.history[-1]["request"], "result": copy.deepcopy(result)})
                    candidate = None
            if candidate is not None: self.runtime, self.registry = candidate, registry
        finally: self.in_tick = False
        self.record("step", request, result)
        return result

    def install_global_handlers(self, handlers):
        self.boundary(); base = copy.deepcopy(self.base); base["handlers"] = copy.deepcopy(handlers)
        revision = checked_numeric("int", self.revision+1)
        core = self.compose(base=base); self.commit(core, self.runtime.snapshot()); self.base = base; self.revision = revision
        result = {"status": "COMMITTED"}
        self.record("install_global_handlers", {"handlers": handlers}, result)
        return result

    def activate(self, definition, mode="preserve", migration=None, queue_policy=None):
        args = {"definition": definition, "mode": mode, "migration": migration, "queue_policy": queue_policy}
        try:
            self.boundary()
            revision = checked_numeric("int", self.revision+1)
            if mode not in {"preserve", "rebase", "restart"}: raise Fault("TypeError", {"mode": mode}, "migration")
            package = self.package(definition); id_ = package["id"]
            if id_ in self.packages and package != self.packages[id_]: raise Fault("CapabilityMismatch", {"reason": "immutable definition version"}, "migration")
            packages = copy.deepcopy(self.packages); packages[id_] = package
            core = self.compose(packages); snapshot = self.runtime.snapshot(); registry = copy.deepcopy(self.registry)
            selected = {k for k, v in registry.items() if v["definition"] == self.default}
            cancelled = []; released = []
            if mode == "rebase":
                if migration is None or queue_policy != "rebind_preserve_time": raise Fault("MigrationFailed", {"reason": "explicit migration and queue policy required"}, "migration")
                result_type = {"result": {"ok": package["state_type"], "err": "string"}}
                body = copy.deepcopy(migration)
                def pure(node):
                    if isinstance(node, list):
                        for child in node: pure(child)
                    elif isinstance(node, dict) and "op" in node:
                        if node["op"] in {"read", "observe", "resolve", "query", "service_query", "extension"}: raise Fault("EffectViolation", {"op": node["op"]}, "effect")
                        for key, child in node.items():
                            if not (node["op"] == "literal" and key == "value"): pure(child)
                pure(body)
                probe = copy.deepcopy(self.base); probe["events"]["MigrationProbe"] = {"record": {"input": self.packages[self.default]["state_type"]}}
                probe["handlers"] = [{"id": "migration_probe", "priority": 0, "event": "MigrationProbe", "body": [{"op": "let", "name": "out", "value": body}]}]
                body_type = compile_program(probe)["handlers"][0]["body"][0]["value"]["_type"]
                if body_type == package["state_type"]:
                    body = {"op": "result", "tag": "ok", "value": body, "ok_type": package["state_type"], "err_type": "string"}
                elif body_type != result_type: raise Fault("TypeError", {"reason": "migration must return typed state or Result"}, "type")
                evaluator_core = copy.deepcopy(self.base); evaluator_core["handlers"] = []
                evaluator_core["extensions"] = {"Migration@1": {"input": self.packages[self.default]["state_type"], "output": result_type, "body": body, "cost": 1}}
                evaluator = Runtime(compile_program(evaluator_core), [], **self.options); txn = Transaction(evaluator)
                job = Job(txn, {"priority": 0}, {"id": "migration"}, {})
                for entity in snapshot["entities"]:
                    key = tuple(entity["id"])
                    if key in selected:
                        txn.charge(); result = job.eval(evaluator.core["extensions"]["Migration@1"]["body"], {"input": entity["values"]["state:"+self.default]})
                        if "err" in result: raise Fault("MigrationFailed", {"instance": entity["id"], "error": result["err"]}, "migration")
                        entity["values"].pop("state:"+self.default); entity["values"]["state:"+id_] = validate_value(package["state_type"], result["ok"])
                        registry[key]["definition"] = id_; registry[key]["subscriptions"] = [h["event"] for h in package["handlers"]]
                for future in snapshot["future"]:
                    payload = future["event"]["payload"]
                    if tuple(payload.get("instance", [])) in selected and payload.get("definition") == self.default: payload["definition"] = id_
            elif mode == "restart":
                retained = []
                for future in snapshot["future"]:
                    if tuple(future["event"]["payload"].get("instance", [])) in selected:
                        cancelled.append({"code": "TargetCancelled", "event": future["event"], "dueTick": future["tick"]})
                    else: retained.append(future)
                snapshot["future"] = retained
                snapshot["entities"] = [e for e in snapshot["entities"] if tuple(e["id"]) not in selected]
                for key in selected:
                    released.extend(registry[key]["reservations"]); registry.pop(key)
            self.commit(core, snapshot)
            self.packages, self.registry, self.default = packages, registry, id_; self.revision = revision
            result = {"status": "COMMITTED", "revision": self.revision, "mode": mode,
                      "cancelledEvents": cancelled, "releasedReservations": released}
            self.record("activate", args, result); return result
        except Fault as fault:
            self.record("activate", args, {"status": "REJECTED", "code": fault.code, "details": fault.details})
            raise

    @classmethod
    def restore(cls, snapshot):
        if snapshot.get("format") != "lifecycle-v0.1": raise Fault("SnapshotSchemaMismatch", phase="migration")
        config = snapshot["initial_config"]
        c = cls(snapshot["base"], config["initial"], list(snapshot["packages"].values()), snapshot["default"], **{k: config[k] for k in ["seed", "budgets", "capabilities"]})
        c.runtime = Runtime.restore(c.compose(), snapshot["runtime"], c.options["capabilities"])
        c.runtime.restore_driver(snapshot["driver"])
        c.registry = {}
        for instance in snapshot["registry"]:
            key = entity_key(instance["id"]); definition = instance["definition"]
            if definition not in c.packages or key not in c.runtime.world or "state:"+definition not in c.runtime.world[key]:
                raise Fault("SnapshotSchemaMismatch", {"reason": "missing active package/state"}, "migration")
            metadata = {k: v for k, v in instance.items() if k != "id"}
            metadata = validate_value({"record": {"definition": "string", "reservations": {"list": "string"}, "subscriptions": {"list": "string"}}}, metadata)
            if key in c.registry: raise Fault("SnapshotSchemaMismatch", {"reason": "duplicate instance"}, "migration")
            c.registry[key] = metadata
        c.serial, c.revision = validate_value("int", snapshot["serial"]), validate_value("int", snapshot["revision"])
        if c.serial < 0 or c.revision < 0: raise Fault("SnapshotSchemaMismatch", {"reason": "negative control counter"}, "migration")
        for key, values in c.runtime.world.items():
            states = [id_ for id_ in c.packages if "state:"+id_ in values]
            if states and (len(states) != 1 or key not in c.registry or c.registry[key]["definition"] != states[0]):
                raise Fault("SnapshotSchemaMismatch", {"reason": "namespace ownership"}, "migration")
            if key[2] == "instance-control" and key[3] >= c.serial:
                raise Fault("SnapshotSchemaMismatch", {"reason": "control ID counter"}, "migration")
        c.initial_config = copy.deepcopy(config)
        return c

    @classmethod
    def replay(cls, initial_config, journal):
        c = cls(**copy.deepcopy(initial_config))
        for record in journal:
            if record["op"] not in {"create_instance", "install_global_handlers", "activate", "step"}: raise Fault("ReplayMismatch", {"control": record["op"]}, "migration")
            try: outcome = getattr(c, record["op"])(**copy.deepcopy(record["args"]))
            except Fault as fault: outcome = {"status": "REJECTED", "code": fault.code, "details": fault.details}
            if outcome != record["outcome"] or digest(c.snapshot()) != record["snapshot_digest"]:
                raise Fault("ReplayMismatch", {"control": record["op"]}, "migration")
        return c
