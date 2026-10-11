"""Locate observable differences; mutant Core programs are not approved Plans."""
from .runtime import Runtime


def observables(runtime, result):
    # Program hashes and source identities differ across valid representations.
    # They must not manufacture a semantic difference when world/effects agree.
    def remove_metadata(value):
        if isinstance(value, list): return [remove_metadata(x) for x in value]
        if isinstance(value, dict):
            return {k: remove_metadata(v) for k, v in value.items()
                    if k not in {"source", "sourceNode", "loweringTrace", "state_digest", "program_digest"}}
        return value
    return remove_metadata({"state": runtime.snapshot(), "result": result})


def compare_execution(reference, candidate, initial, requests, **options):
    a, b = Runtime(reference, initial, **options), Runtime(candidate, initial, **options)
    for index, request in enumerate(requests):
        ar, br = a.step(**request), b.step(**request)
        if observables(a, ar) != observables(b, br):
            chains = [e["source"]["originChain"] for r in [ar, br] for e in r["effects"]]
            return {"status": "DIFFERENT", "request_index": index, "originChains": chains,
                    "reference": ar, "candidate": br, "reference_world": a.snapshot()["entities"], "candidate_world": b.snapshot()["entities"]}
    return {"status": "EQUAL", "originChains": []}
