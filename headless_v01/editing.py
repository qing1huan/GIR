"""Draft/commit semantic editing with typed event ports and revision-guarded activation."""
import copy

from .compiler import compile_program, digest, make_plan
from .runtime import Runtime
from .types import Fault, checked_numeric


class SemanticEditor:
    def __init__(self, document, initial, seed=1, budgets=None, capabilities=None, test_catalog=None):
        self.document = copy.deepcopy(document); self.runtime = Runtime(compile_program(document), initial, seed, budgets, capabilities)
        self.revision = 0; self.pending = []; self.published = {0: copy.deepcopy(document)}; self.catalog = copy.deepcopy(test_catalog or {})
        self.in_tick = False; self.journal = []
        self.initial_config = {"document": copy.deepcopy(document), "initial": copy.deepcopy(initial), "seed": seed,
                               "budgets": budgets, "capabilities": capabilities, "test_catalog": test_catalog}

    def snapshot(self):
        return copy.deepcopy({"document": self.document, "revision": self.revision, "published": self.published,
                              "runtime": self.runtime.snapshot(), "driver": {"halted": self.runtime.halted, "failed_request": self.runtime.failed_request}})

    def record(self, op, args, outcome):
        self.journal.append({"op": op, "args": copy.deepcopy(args), "outcome": copy.deepcopy(outcome), "snapshot_digest": digest(self.snapshot())})

    def create(self, node):
        self.pending.append({"op": "create", "node": copy.deepcopy(node)}); self.record("create", {"node": node}, None)

    def replace(self, node_id, node):
        self.pending.append({"op": "replace", "id": node_id, "node": copy.deepcopy(node)}); self.record("replace", {"node_id": node_id, "node": node}, None)

    def delete(self, node_id):
        self.pending.append({"op": "delete", "id": node_id}); self.record("delete", {"node_id": node_id}, None)

    def connect(self, from_id, port, to_id, input_port):
        self.pending.append({"op": "connect", "connection": {"from": from_id, "port": port, "to": to_id, "input": input_port}})
        self.record("connect", {"from_id": from_id, "port": port, "to_id": to_id, "input_port": input_port}, None)

    def query_nodes(self, **predicate):
        return [copy.deepcopy(n) for n in self.document["nodes"] if all(n.get(k) == v for k, v in predicate.items())]

    def get_dependencies(self, node_id, document=None):
        doc = document or self.document
        node = next((n for n in doc["nodes"] if n["id"] == node_id), None)
        if node is None: raise Fault("UnknownNode", {"node": node_id}, "type")
        operations = []
        def visit(x):
            if isinstance(x, list):
                for child in x: visit(child)
            elif isinstance(x, dict):
                if "op" in x: operations.append(x)
                for key, child in x.items():
                    if key != "_origin" and not (x.get("op") == "literal" and key == "value"): visit(child)
        core = compile_program(doc)
        visit(next(h["body"] for h in core["handlers"] if h["id"] == node_id))
        precision = "unknown" if any(n["op"] in {"query", "service_query", "extension"} for n in operations) else "conservative"
        return {"nodeID": node_id, "precision": precision,
                "fields": sorted({n["field"] for n in operations if n["op"] in {"read", "observe", "write", "add"}}),
                "nodes": sorted({c["from"] for c in doc.get("connections", []) if c["to"] == node_id})}

    def get_dependents(self, node_id):
        self.get_dependencies(node_id)
        return {"nodeID": node_id, "precision": "conservative", "nodes": sorted({c["to"] for c in self.document.get("connections", []) if c["from"] == node_id})}

    def draft(self):
        doc = copy.deepcopy(self.document); affected = set()
        for action in self.pending:
            op = action["op"]
            if op == "restore": doc = copy.deepcopy(action["document"]); affected |= {n["id"] for n in doc["nodes"]}; continue
            if op == "connect":
                doc.setdefault("connections", []).append(copy.deepcopy(action["connection"])); affected |= {action["connection"]["from"], action["connection"]["to"]}; continue
            node_id = action["node"]["id"] if op == "create" else action["id"]
            index = next((i for i, n in enumerate(doc["nodes"]) if n["id"] == node_id), None)
            if op == "create":
                if index is not None: raise Fault("NodeConflict", {"node": node_id}, "type")
                doc["nodes"].append(copy.deepcopy(action["node"]))
            elif index is None: raise Fault("UnknownNode", {"node": node_id}, "type")
            elif op == "replace":
                if action["node"]["id"] != node_id: raise Fault("TypeError", {"reason": "stable node ID"}, "type")
                doc["nodes"][index] = copy.deepcopy(action["node"])
            elif op == "delete":
                doc["nodes"].pop(index)
                doc["connections"] = [c for c in doc.get("connections", []) if node_id not in {c["from"], c["to"]}]
            affected.add(node_id)
        if len(doc["nodes"]) > 1024 or len(doc.get("connections", [])) > 1024: raise Fault("BudgetExceeded", {"resource": "semantic nodes/ports"}, "type")
        return doc, affected

    def impact_tests(self, affected, doc):
        ids = {n["id"] for n in doc["nodes"]}; affected = set(affected)
        for _ in range(len(ids)):
            affected |= {c["to"] for c in doc.get("connections", []) if c["from"] in affected}
        # Shared fields and dynamic EntityIDs may couple handlers without a port.
        # Until an exact alias proof exists, conservatively select the full catalog.
        affected |= ids | {n["id"] for n in self.document["nodes"]}
        deps = [self.get_dependencies(id_, doc) for id_ in ids]
        precision = "unknown" if any(d["precision"] == "unknown" for d in deps) else "conservative"
        tests = {t for registered in self.catalog.values() for t in registered}
        return {"affectedNodes": sorted(affected), "dependencyPrecision": precision,
                "requiredTests": sorted(tests) if tests else ["UNKNOWN_IMPACT_TEST_CATALOG"],
                "testAvailability": "known" if tests else "unknown"}

    def evaluate(self):
        result = {"status": "REJECTED", "revision": self.revision, "errors": [], "affectedNodes": [],
                  "dependencyPrecision": "unknown", "requiredTests": ["UNKNOWN_IMPACT_TEST_CATALOG"], "loweringDelta": None, "migrationRequirement": "unknown"}
        try:
            doc, affected = self.draft(); core = compile_program(doc)
            result.update(self.impact_tests(affected, doc))
            compatible = all(core[k] == self.runtime.core[k] for k in ["fields", "events", "services"])
            migration = "none" if compatible and not self.runtime.state["future"] else "explicit_state_event_migration_required"
            result.update({"status": "VALID", "migrationRequirement": migration,
                           "loweringDelta": {"old": self.runtime.core["_digest"], "new": core["_digest"]}})
            return result, doc, core
        except Fault as fault:
            result["errors"] = [{"code": fault.code, "phase": fault.phase, "details": fault.details}]
            return result, None, None

    def validate_patch(self):
        result, _, _ = self.evaluate(); self.record("validate_patch", {}, result); return result

    def commit_patch(self, expectedRevision):
        result, doc, core = self.evaluate()
        if expectedRevision != self.revision: result.update(status="REJECTED", errors=[{"code": "RevisionConflict", "phase": "migration", "details": {"expected": expectedRevision, "actual": self.revision}}])
        elif self.in_tick or self.runtime.halted: result.update(status="REJECTED", errors=[{"code": "PatchBoundary", "phase": "migration", "details": {}}])
        elif result["status"] == "VALID" and result["migrationRequirement"] != "none": result.update(status="REJECTED", errors=[{"code": "MigrationRequired", "phase": "migration", "details": {"requirement": result["migrationRequirement"]}}])
        if result["status"] == "VALID":
            try:
                revision = checked_numeric("int", self.revision+1)
                snapshot = self.runtime.snapshot(); snapshot["program_digest"] = core["_digest"]
                candidate = Runtime.restore(core, snapshot, self.runtime.capabilities)
                candidate.history = copy.deepcopy(self.runtime.history)
            except Fault as fault:
                result.update(status="REJECTED", errors=[{"code": fault.code, "phase": fault.phase, "details": fault.details}])
            else:
                self.runtime, self.document, self.revision = candidate, doc, revision
                self.published[revision] = copy.deepcopy(doc); self.pending = []
                result.update(status="COMMITTED", revision=revision)
        self.record("commit_patch", {"expectedRevision": expectedRevision}, result); return result

    def rollback_patch(self):
        self.pending = []; result = {"status": "ROLLED_BACK_DRAFT", "revision": self.revision}
        self.record("rollback_patch", {}, result); return result

    def restore_published(self, revision, expectedRevision):
        if revision not in self.published: raise Fault("UnknownRevision", {"revision": revision}, "migration")
        self.pending = [{"op": "restore", "document": copy.deepcopy(self.published[revision])}]
        # One replayable operation, rather than recording an invisible draft restore.
        prior_len = len(self.journal); result = self.commit_patch(expectedRevision); del self.journal[prior_len:]
        self.record("restore_published", {"revision": revision, "expectedRevision": expectedRevision}, result); return result

    def step(self, **request):
        self.in_tick = True
        try: result = self.runtime.step(**request)
        finally: self.in_tick = False
        self.record("step", request, result); return result

    @classmethod
    def replay(cls, initial_config, journal):
        editor = cls(**copy.deepcopy(initial_config))
        allowed = {"create", "replace", "delete", "connect", "validate_patch", "commit_patch", "rollback_patch", "restore_published", "step"}
        for record in journal:
            if record["op"] not in allowed: raise Fault("ReplayMismatch", {"operation": record["op"]})
            outcome = getattr(editor, record["op"])(**copy.deepcopy(record["args"]))
            if outcome != record["outcome"] or digest(editor.snapshot()) != record["snapshot_digest"]: raise Fault("ReplayMismatch", {"operation": record["op"]})
        return editor
