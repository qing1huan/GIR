"""Only the 51 never-reserved rows after the interrupted, consumed v4 run."""
import copy
import hashlib
import json

from . import pilot24_forward60_scope as previous
from . import pilot24_recovery_scope as s
from .adapter import digest
from .pilot24_execution_scope import ROOT, read, sha, require

FREEZE = 'ark_eval/pilot24_recovery.v5_1.freeze.json'
RETAINED_V5 = 'ark_eval/pilot24_recovery.v5.freeze.json'
RETAINED_V5_SHA = '228e5a7a1c4bf02f3eb5821755bbe02f528366c414f9f939014c9bda287bf99a'
PERMIT = ROOT / 'results/pilot24-forward51-permit.json'
PREVIOUS_SCOPE = 'a2ded8a57312b807df5ead04a495e4c4ec918d22663c3d775e8aeb36b89bed4d'
PREVIOUS_KEY = 'bcfc6e4381556e81c607eabca0a2543a3488e8b2aff661d4f9f7df11ed945f13'
PREVIOUS_FREEZE = previous.FREEZE
PREVIOUS_FREEZE_SHA = 'd70d0297924215630a53ae8f3ea1b3be5972c33de96afcc62b02dd0891301a94'
PREVIOUS_OUTPUT = 'results/pilot24-recovery-score-forward60-executed-20261012-001'
PREVIOUS_CLAIM = 'results/pilot24-zero-start-claims/' + PREVIOUS_KEY
PREVIOUS_LAUNCH = 'results/pilot24-zero-start-launches/' + PREVIOUS_KEY
PREVIOUS_PERMIT = previous.PERMIT.relative_to(ROOT).as_posix()
REVIEW = 'results/pilot24-forward60-interruption-review-20261012-001'
REGISTRY = REVIEW + '/production-evidence-sha256.json'
PRODUCTION_DIGEST = 'c3e9e0a96c64241d1021a92d945b153ecc802113f8d360208a183c5149713ef6'
ADMIN_ANCHORS = {
    REGISTRY: 'a3275ba22243a4d26a61e8fa7e97f5a447afc237b145be62eb19e93fe070c0de',
    REVIEW+'/combined-cases-admin.json': '5b5db20842dc2983152dc408cd70182be7065c602fe1a8aee18587d8e20e88dd',
    REVIEW+'/interruption-outcome.json': 'a7741ea305efaec502417e51996bd0778af491aaca7c941c3c34050f18ffe86e',
    REVIEW+'/runtime-observation.json': '0b23ea75230d857dea00c059ea12ea63121ddd0032881779c0c1d7698280684c',
}
FILES = ('ark_eval/pilot24_forward51_scope.py', 'ark_eval/tests/test_pilot24_forward51.py',
         'docs/experiment-v3/PILOT24_FORWARD51.md')


def retained_forward60():
    require(sha(previous.RETAINED_V4) == previous.RETAINED_V4_SHA and
            sha(previous.RETAINED_V4_1) == previous.RETAINED_V4_1_SHA and
            sha(RETAINED_V5) == RETAINED_V5_SHA and sha(PREVIOUS_FREEZE) == PREVIOUS_FREEZE_SHA and
            all(sha(n) == h for n,h in ADMIN_ANCHORS.items()), 'P2451_RETAINED_ANCHOR')
    old = read(PREVIOUS_FREEZE)
    require(digest(old) == PREVIOUS_SCOPE and old['recovery_key'] == PREVIOUS_KEY,
            'P2451_PREVIOUS_SCOPE')
    registry = read(REGISTRY)
    files = registry['files']
    roots = (PREVIOUS_OUTPUT, PREVIOUS_CLAIM, PREVIOUS_LAUNCH)
    actual = {p.relative_to(ROOT).as_posix() for base in roots
              for p in (ROOT/base).rglob('*') if p.is_file()} | {PREVIOUS_PERMIT, PREVIOUS_FREEZE}
    require(len(files) == registry['file_count'] == 127 and
            digest(files) == registry['files_digest'] == PRODUCTION_DIGEST and
            actual == set(files) and all(sha(n) == h for n,h in files.items()),
            'P2451_PRODUCTION_DRIFT')
    observation = read(REVIEW+'/runtime-observation.json')
    require(observation['previous_controller_identity'] == {'pid':291176,'start_ticks':'8144959'} and
            observation['previous_pid_present'] is False and
            observation['matching_python_processes'] == observation['reviewed_worker_containers'] == [] and
            observation['controller_written'] is False and observation['stop_trigger'] == 'UNKNOWN',
            'P2451_ADMIN_OBSERVATION')
    return old


def forward_reconciliation():
    old = retained_forward60()
    cases, preserved, allowed60 = previous.forward_reconciliation()
    require(old['allowed_rows'] == allowed60, 'P2451_PARENT_RANGE')
    claim = read(PREVIOUS_CLAIM+'/claim.json')
    require(claim['scope_digest'] == PREVIOUS_SCOPE and claim['recovery_key'] == PREVIOUS_KEY and
            claim['permit_digest'] == digest(read(PREVIOUS_PERMIT)) and
            claim['controller_identity'] == {'pid':291176,'start_ticks':'8144959'} and
            type(claim['maximum_primary_runs']) is int and claim['maximum_primary_runs'] == 60 and
            claim['retry_or_resume_allowed'] is False and not (ROOT/PREVIOUS_OUTPUT/'summary.json').exists(),
            'P2451_INTERRUPTED_CLAIM')
    finished = allowed60[:9]
    suffixes = {x['slot_id']+'-'+x['case_id']+'.json' for x in finished}
    for base,prefix in ((PREVIOUS_CLAIM,'case-'),(PREVIOUS_OUTPUT,'reservation-'),(PREVIOUS_OUTPUT,'result-')):
        require({p.name for p in (ROOT/base).glob(prefix+'*.json')} ==
                {prefix+x for x in suffixes}, 'P2451_RESERVATION_RANGE')
    events = [read(p.relative_to(ROOT).as_posix())
              for p in sorted((ROOT/PREVIOUS_CLAIM).glob('event-*.json'))]
    ids = [(x['slot_id'],x['case_id']) for x in finished]
    for state in ('PRIMARY_RESERVED','PRIMARY_COMPLETED'):
        require([(e['slot_id'],e['case_id']) for e in events if e['state'] == state] == ids,
                'P2451_EVENT_RECONCILIATION')
    from .pilot24_recovery import validate_result, accounting
    by_id = {(row['slot_id'],row['case_id']):row for row in preserved}
    for expected in finished:
        sid,cid = expected['slot_id'],expected['case_id']
        suffix = sid+'-'+cid+'.json'
        case = next(c for c in cases[sid[:2]] if c['id'] == cid)
        require(expected['case_digest'] == digest(case), 'P2451_CASE_BINDING')
        for base,prefix in ((PREVIOUS_CLAIM,'case-'),(PREVIOUS_OUTPUT,'reservation-')):
            value = read(base+'/'+prefix+suffix)
            require(all(value[k] == expected[k] for k in ('slot_id','case_id','source_sha256')) and
                    type(value['maximum_primary_runs']) is int and value['maximum_primary_runs'] == 1 and
                    value['unknown_outcome'] == 'DO_NOT_RETRY', 'P2451_RESERVATION_BINDING')
        value = read(PREVIOUS_OUTPUT+'/result-'+suffix)
        validate_result('gameir_text',case,value)
        require(value['status'] == 'PASS' and
                value['isolated_result']['source_sha256'] == expected['source_sha256'], 'P2451_RESULT_BINDING')
        by_id[(sid,cid)].update(copy.deepcopy(value),phase='ADMIN_PRESERVED_INTERRUPTED_DURABLE_RESULT',reason=None)
    allowed = allowed60[9:]
    admin = read(REVIEW+'/interruption-outcome.json')
    # The independently sealed administrative file was written on Windows.
    combined = (json.dumps(preserved,ensure_ascii=False,indent=2)+'\n').replace('\n','\r\n').encode('utf-8')
    require(hashlib.sha256(combined).hexdigest() == ADMIN_ANCHORS[REVIEW+'/combined-cases-admin.json'] and
            admin['unreserved'] == allowed and admin['combined_fixed_288'] == accounting(preserved) and
            admin['new_reserved_unknown'] == [] and admin['controller_written_summary_present'] is False and
            admin['derived_admin_evidence_not_forged_controller_summary'] is True and
            accounting(preserved)['counts'] == {'PASS':221,'FAIL':2,'NOTRUN':63,'HARNESS_ERROR':2},
            'P2451_ADMIN_RECONCILIATION')
    require(len(allowed) == 51 and [(x['slot_id'],x['case_id']) for x in allowed] == s.expected_ids()[31:],
            'P2451_EXACT_UNRESERVED_51')
    return cases,preserved,allowed


def make_forward_manifest():
    _,preserved,allowed = forward_reconciliation()
    identity = {'parent_scope':s.PARENT_SCOPE,'parent_batch_key':s.PARENT_KEY,
                'retained_evidence_digest':s.EVIDENCE_DIGEST,'consumed_scope':PREVIOUS_SCOPE,
                'consumed_evidence_digest':PRODUCTION_DIGEST,'consumed_recovery_key':PREVIOUS_KEY,
                'allowed_rows':allowed}
    paths = set(s.FILES+s.parent.FILES+tuple(s.INTEGRATION_BASE)+s.FORWARD_FILES+previous.FILES+FILES+
                ('ark_eval/node_backend.py',))
    return {'schema':'gir-pilot24-exact-remainder@5','state':'SEALED_PENDING_REVIEW_AND_APPROVAL',
            **identity,'recovery_key':digest(identity),'maximum_primary_runs':51,
            'preserved_durable_rows':223,'preserved_rows_digest':digest(preserved),
            'excluded_unknown_rows':[list(s.UNKNOWN),list(previous.NEW_UNKNOWN)],'preserved_static_NOTRUN':12,
            'original_denominator':288,'bindings':{n:sha(n) for n in sorted(paths)},
            'consumed_freeze_sha256':PREVIOUS_FREEZE_SHA,'administrative_anchors':ADMIN_ANCHORS,
            'retained_unconsumed_v5_sha256':RETAINED_V5_SHA,
            'integration_base_commit':s.BASE_COMMIT,'integration_base_sha256':s.INTEGRATION_BASE,
            'reviewed_node_sha256':s.REVIEWED_NODE,'limits':s.historical_parent()['limits'],
            'prior_claim':PREVIOUS_CLAIM,'prior_evidence_digest':PRODUCTION_DIGEST,
            'old_permits_accepted':False,'model_calls':0,'repairs':0,'retry_or_resume_allowed':False,
            'controller':'direct-wsl-detached-session','API_or_secret_operations':False}


def load_forward_manifest():
    frozen = read(FREEZE)
    require(digest(frozen) == digest(make_forward_manifest()), 'P2451_FREEZE_DRIFT')
    return frozen


def forward_execution_manifest(manifest):
    return s.forward_execution_manifest(manifest)


def forward_static_manifest():
    return s.forward_static_manifest()


def forward_fields(m):
    return {'schema':'gir-pilot24-forward51-explicit-permit@1','decision':'APPROVE_SINGLE_UNRESERVED_51',
            'human_explicit_approval':True,'scope_digest':digest(m),'recovery_key':m['recovery_key'],
            'parent_scope':s.PARENT_SCOPE,'consumed_scope':PREVIOUS_SCOPE,
            'consumed_evidence_digest':PRODUCTION_DIGEST,'allowed_rows_digest':digest(m['allowed_rows']),
            'maximum_primary_runs':51,'excluded_unknown_rows':[list(s.UNKNOWN),list(previous.NEW_UNKNOWN)],
            'route':'direct-wsl','model_calls':0}


def validate_forward_permit(m,permit):
    statement = permit.get('human_statement') if type(permit) is dict else None
    require(isinstance(statement,str) and 0 < len(statement.strip()) <= 1000, 'P2451_PERMIT_REQUIRED')
    require(digest(permit) == digest({**forward_fields(m),'human_statement':statement}) and
            digest(m) == digest(load_forward_manifest()), 'P2451_INEXACT_PERMIT')
