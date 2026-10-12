"""Exact unreserved remainder after consumed v3; retained history is immutable."""
import copy
import hashlib
import json

from . import pilot24_recovery_scope as s
from .adapter import digest
from .pilot24_execution_scope import ROOT, read, sha, require

FREEZE = 'ark_eval/pilot24_recovery.v4_2.freeze.json'
RETAINED_V4 = 'ark_eval/pilot24_recovery.v4.freeze.json'
RETAINED_V4_SHA = 'f666946e93546779c2990efd0d4bff2c121c8993d1129512c4ca5cc5b29f37c1'
RETAINED_V4_1 = 'ark_eval/pilot24_recovery.v4_1.freeze.json'
RETAINED_V4_1_SHA = 'f7ffdcfe19813c4dbb361a2c7ef6a4ecb1668cb7c8cbe046276170453acff165'
PERMIT = ROOT / 'results/pilot24-forward60-permit.json'
PREVIOUS_SCOPE = '0b77fe87c0aacecc8640671a53057b6b459ddf4766a44213b0a57c30d059d17f'
PREVIOUS_KEY = 'f1ace9cb55874b3d4170fa916ab430577938c39b94f08469aedbb7fdfe149abd'
PREVIOUS_FREEZE_SHA = 'a9aed311d0be20ece882ad381eac4c262429925cf445ca5db16dfadac115db0b'
PREVIOUS_OUTPUT = 'results/pilot24-recovery-score-forward61-executed-001'
PREVIOUS_CLAIM = 'results/pilot24-zero-start-claims/' + PREVIOUS_KEY
PREVIOUS_LAUNCH = 'results/pilot24-zero-start-launches/' + PREVIOUS_KEY
PREVIOUS_PERMIT = 'results/pilot24-forward61-permit.json'
PREVIOUS_REVIEW = 'results/pilot24-forward61-execution-review-20261011-001'
REGISTRY = PREVIOUS_REVIEW + '/final-execution-evidence-sha256.json'
REGISTRY_SHA = 'd556da53d1fe8c3c465564d7f5b2b8ade9dd854e1d859e7c4d5320444c8e8ef8'
PRODUCTION_DIGEST = '229e6c39044a038e880505dd3296637d83ae16c102cb4c312c8eac8eaa90c061'
NEW_UNKNOWN = ('C1-r2-js_callback', 'R04')
FILES = ('ark_eval/pilot24_forward60_scope.py', 'ark_eval/tests/test_pilot24_forward60.py',
         'ark_eval/tests/test_pilot24_receipt_timing.py', 'docs/experiment-v3/PILOT24_FORWARD60.md',
         'ark_eval/pilot24_public_payload.py', 'ark_eval/tests/test_pilot24_public_payload.py',
         'results/pilot24-frozen-public-runtime-20261012-001.tar')


def retained_forward61():
    require(sha(s.FORWARD_FREEZE) == PREVIOUS_FREEZE_SHA and sha(REGISTRY) == REGISTRY_SHA,
            'P2460_PREVIOUS_ANCHOR')
    old = read(s.FORWARD_FREEZE)
    require(digest(old) == PREVIOUS_SCOPE and old['recovery_key'] == PREVIOUS_KEY,
            'P2460_PREVIOUS_SCOPE')
    registry = read(REGISTRY)
    files = registry['files']
    registry_digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':'),
                                               ensure_ascii=False).encode()).hexdigest()
    require(len(files) == registry['file_count'] == 78 and registry_digest == registry['files_digest'] ==
            '2cddfd790a6a744ceb66f9277d72e7f7d738751faf08496d5dd16b3f9728ca41',
            'P2460_REGISTRY_DRIFT')
    roots = (PREVIOUS_OUTPUT, PREVIOUS_CLAIM, PREVIOUS_LAUNCH)
    production = {n:h for n,h in files.items() if n == PREVIOUS_PERMIT or
                  any(n.startswith(base + '/') for base in roots)}
    actual = {p.relative_to(ROOT).as_posix() for base in roots
              for p in (ROOT / base).rglob('*') if p.is_file()} | {PREVIOUS_PERMIT}
    require(len(production) == 33 and digest(production) == PRODUCTION_DIGEST and
            actual == set(production) and all(sha(n) == h for n,h in production.items()),
            'P2460_PRODUCTION_DRIFT')
    review = {n:h for n,h in files.items() if n.startswith(PREVIOUS_REVIEW + '/')}
    names = {p.relative_to(ROOT).as_posix() for p in (ROOT / PREVIOUS_REVIEW).rglob('*') if p.is_file()}
    require(names == set(review) | {REGISTRY} and all(sha(n) == h for n,h in review.items()),
            'P2460_REVIEW_EVIDENCE_DRIFT')
    return old


def forward_reconciliation():
    old = retained_forward61()
    cases, preserved, allowed61 = s.forward_reconciliation()
    require(old['allowed_rows'] == allowed61, 'P2460_PARENT_RANGE')
    claim = read(PREVIOUS_CLAIM + '/claim.json')
    summary = read(PREVIOUS_OUTPUT + '/summary.json')
    require(claim['scope_digest'] == PREVIOUS_SCOPE and claim['recovery_key'] == PREVIOUS_KEY and
            claim['permit_digest'] == digest(read(PREVIOUS_PERMIT)) and
            claim['maximum_primary_runs'] == 61 and claim['retry_or_resume_allowed'] is False and
            summary['scope_digest'] == PREVIOUS_SCOPE and summary['recovery_key'] == PREVIOUS_KEY and
            summary['state'] == 'STOPPED_ON_BLOCK' and summary['halt'] == 'PRIMARY_OR_PERSISTENCE_INCOMPLETE' and
            summary['remaining_profile_containers'] == [] and summary['claim_consumed'] is True and
            summary['retry_or_resume_allowed'] is False and summary['model_calls'] == 0,
            'P2460_TERMINAL_RECEIPT')
    ledger = sorted((ROOT / PREVIOUS_CLAIM).glob('case-*.json'))
    reservations = sorted((ROOT / PREVIOUS_OUTPUT).glob('reservation-*.json'))
    results = sorted((ROOT / PREVIOUS_OUTPUT).glob('result-*.json'))
    suffix = NEW_UNKNOWN[0] + '-' + NEW_UNKNOWN[1] + '.json'
    require([p.name for p in ledger] == ['case-' + suffix] and
            [p.name for p in reservations] == ['reservation-' + suffix] and not results,
            'P2460_RESERVATION_RANGE')
    expected = next(x for x in allowed61 if (x['slot_id'], x['case_id']) == NEW_UNKNOWN)
    case = next(c for c in cases['C1'] if c['id'] == NEW_UNKNOWN[1])
    require(expected['case_digest'] == digest(case), 'P2460_RESERVED_CASE_BINDING')
    for p in ledger + reservations:
        receipt = read(p.relative_to(ROOT).as_posix())
        require(all(receipt[k] == expected[k] for k in ('slot_id','case_id','source_sha256')) and
                type(receipt['maximum_primary_runs']) is int and receipt['maximum_primary_runs'] == 1 and
                receipt['unknown_outcome'] == 'DO_NOT_RETRY', 'P2460_RESERVATION_BINDING')
    events = [read(p.relative_to(ROOT).as_posix())
              for p in sorted((ROOT / PREVIOUS_CLAIM).glob('event-*.json'))]
    require([(e['slot_id'], e['case_id']) for e in events if e['state'] == 'PRIMARY_RESERVED'] == [NEW_UNKNOWN]
            and not any(e['state'] == 'PRIMARY_COMPLETED' for e in events) and
            events[-1]['state'] == 'STOPPED_ON_BLOCK', 'P2460_EVENT_RECONCILIATION')
    rows = read(PREVIOUS_OUTPUT + '/cases.json')
    require(len(rows) == 61 and [(r['slot_id'],r['case_id']) for r in rows] ==
            [(r['slot_id'],r['case_id']) for r in allowed61], 'P2460_CASE_ROWS')
    by_id = {(r['slot_id'],r['case_id']):r for r in rows}
    for row in preserved:
        key = row['slot_id'],row['case_id']
        if key not in by_id: continue
        update = by_id[key]
        require(all(update[k] == row[k] for k in ('slot_id','case_id','source_sha256','task_id','representation')),
                'P2460_ROW_BINDING')
        if key == NEW_UNKNOWN:
            require(update['status'] == 'HARNESS_ERROR' and update['phase'] == 'RESERVED_EXECUTION_UNCONFIRMED' and
                    update['primary_case_executions'] == 'UNKNOWN_0_OR_1' and
                    update['candidate_executions'] == 'UNKNOWN_AFTER_RESERVATION' and
                    update['JS_transcript_replays'] == 'UNKNOWN_0_OR_1' and update['IR_case_replays'] == 0,
                    'P2460_UNKNOWN_MUST_REMAIN')
        else:
            require(update['status'] == 'NOTRUN' and update['phase'] == 'NOT_STARTED' and
                    all(type(update[k]) is int and update[k] == 0 for k in
                        ('primary_case_executions','candidate_executions','JS_transcript_replays','IR_case_replays')),
                    'P2460_UNRESERVED_MUST_REMAIN')
        row.clear(); row.update(copy.deepcopy(update))
    combined = (json.dumps(preserved,ensure_ascii=False,indent=2) + '\n').encode('utf-8')
    require(hashlib.sha256(combined).hexdigest() == sha(PREVIOUS_OUTPUT + '/combined-cases.json'),
            'P2460_COMBINED_HISTORY')
    from .pilot24_recovery import accounting
    require(summary['combined_fixed_288'] == accounting(preserved) and
            accounting(preserved)['counts'] == {'PASS':212,'FAIL':2,'NOTRUN':72,'HARNESS_ERROR':2} and
            summary['remainder']['counts'] == {'PASS':0,'FAIL':0,'NOTRUN':60,'HARNESS_ERROR':1},
            'P2460_COUNTS')
    allowed = [r for r in allowed61 if (r['slot_id'],r['case_id']) != NEW_UNKNOWN]
    require(len(allowed) == 60 and [(r['slot_id'],r['case_id']) for r in allowed] == s.expected_ids()[22:],
            'P2460_EXACT_UNRESERVED_60')
    return cases,preserved,allowed


def make_forward_manifest():
    require(sha(RETAINED_V4) == RETAINED_V4_SHA, 'P2460_UNCONSUMED_V4_DRIFT')
    require(sha(RETAINED_V4_1) == RETAINED_V4_1_SHA, 'P2460_UNCONSUMED_V4_1_DRIFT')
    _, preserved, allowed = forward_reconciliation()
    identity = {'parent_scope':s.PARENT_SCOPE,'parent_batch_key':s.PARENT_KEY,
                'retained_evidence_digest':s.EVIDENCE_DIGEST, 'consumed_scope':PREVIOUS_SCOPE,
                'consumed_evidence_digest':PRODUCTION_DIGEST, 'consumed_recovery_key':PREVIOUS_KEY,
                'allowed_rows':allowed}
    paths = set(s.FILES + s.parent.FILES + tuple(s.INTEGRATION_BASE) + s.FORWARD_FILES + FILES +
                ('ark_eval/node_backend.py',))
    return {'schema':'gir-pilot24-exact-remainder@4','state':'SEALED_PENDING_REVIEW_AND_APPROVAL',
            'facility_revision':'IR_ADMISSION_RECEIPT_RENEWAL@1',
            'retained_unconsumed_v4_sha256':RETAINED_V4_SHA,
            'public_payload_revision':'FROZEN_PUBLIC_RUNTIME_RECOVERY@1',
            'retained_unconsumed_v4_1_sha256':RETAINED_V4_1_SHA,
            **identity, 'recovery_key':digest(identity), 'maximum_primary_runs':60,
            'preserved_durable_rows':214, 'preserved_rows_digest':digest(preserved),
            'excluded_unknown_rows':[list(s.UNKNOWN),list(NEW_UNKNOWN)], 'preserved_static_NOTRUN':12,
            'original_denominator':288, 'bindings':{n:sha(n) for n in sorted(paths)},
            'integration_base_commit':s.BASE_COMMIT,'integration_base_sha256':s.INTEGRATION_BASE,
            'reviewed_node_sha256':s.REVIEWED_NODE,'limits':s.historical_parent()['limits'],
            'prior_claim':PREVIOUS_CLAIM,'prior_evidence_digest':PRODUCTION_DIGEST,
            'old_permits_accepted':False,'model_calls':0,'repairs':0,'retry_or_resume_allowed':False,
            'controller':'direct-wsl-detached-session','API_or_secret_operations':False}


def load_forward_manifest():
    frozen = read(FREEZE)
    require(digest(frozen) == digest(make_forward_manifest()), 'P2460_FREEZE_DRIFT')
    return frozen


def forward_execution_manifest(manifest):
    return s.forward_execution_manifest(manifest)


def forward_static_manifest():
    return s.forward_static_manifest()


def forward_fields(m):
    return {'schema':'gir-pilot24-forward60-explicit-permit@1','decision':'APPROVE_SINGLE_UNRESERVED_60',
            'human_explicit_approval':True,'scope_digest':digest(m),'recovery_key':m['recovery_key'],
            'parent_scope':s.PARENT_SCOPE,'consumed_scope':PREVIOUS_SCOPE,
            'consumed_evidence_digest':PRODUCTION_DIGEST,'allowed_rows_digest':digest(m['allowed_rows']),
            'maximum_primary_runs':60,'excluded_unknown_rows':[list(s.UNKNOWN),list(NEW_UNKNOWN)],
            'route':'direct-wsl','model_calls':0}


def validate_forward_permit(m, permit):
    statement = permit.get('human_statement') if type(permit) is dict else None
    require(isinstance(statement,str) and 0 < len(statement.strip()) <= 1000, 'P2460_PERMIT_REQUIRED')
    require(digest(permit) == digest({**forward_fields(m),'human_statement':statement}) and
            digest(m) == digest(load_forward_manifest()), 'P2460_INEXACT_PERMIT')
