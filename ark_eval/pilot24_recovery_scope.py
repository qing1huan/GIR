"""Exact 82-row recovery authorization; never releases or edits the parent claim."""
import copy
import hashlib
import json
from pathlib import Path

from .adapter import digest
from . import pilot24_execution_scope as parent
from .pilot24_execution_scope import require, read, sha, ROOT
from .pilot24_execution_eval import planned_rows

PARENT_SCOPE = '16b2c76503456df495e9c05272309010c43047d1fc71a1d17d17e0bc8a7951e3'
PARENT_KEY = 'f01e6d022547a420689d311de2e486fcdd93180656e2f15f7dd1c77de389db6c'
OLD_OUTPUT = 'results/pilot24-isolated-score-001'
OLD_CLAIM = 'results/pilot24-execution-claims/' + PARENT_KEY
EVIDENCE = 'results/pilot24-score-blocked-review-20261011-001/evidence-sha256.json'
EVIDENCE_DIGEST = '4afd05c8558ca3b8ae9ad1ef0f94eb86c65ae7d110f79132db46074dccd26e42'
UNKNOWN = ('C2-r2-js_callback', 'N02')
FREEZE = 'ark_eval/pilot24_recovery.freeze.json'
APPROVAL = ROOT / 'results/pilot24-recovery-human-approval.json'
ZERO_START_PERMIT = ROOT / 'results/pilot24-zero-start-permit.json'
FAILED_SCOPE = 'c55641e7ec6299f89baf440eb48bbce5cf9216b1c2e8a99b97804eb4f46fa64f'
RECOVERY_KEY = '0d551c71d6e1d329aa71b73ec37d6e7e0a13c6f9b2c680385f0984b44bfdea8e'
FAILED_OUTPUT = 'results/pilot24-recovery-score-001'
FAILED_CLAIM = 'results/pilot24-recovery-claims/' + RECOVERY_KEY
FAILED_LAUNCH = 'results/pilot24-recovery-launches/' + RECOVERY_KEY
FAILED_EVIDENCE = 'results/pilot24-recovery-execution-review-20261011-001/execution-evidence-sha256.json'
FAILED_DIGEST = 'c59b78c22d7e9a7ba28355121ee4fa95176406cb833a6f935548cd12d8d8a7d4'
FILES = ('ark_eval/pilot24_recovery_scope.py', 'ark_eval/pilot24_recovery.py',
         'ark_eval/pilot24_recovery_control.py', 'ark_eval/tests/test_pilot24_recovery.py',
         'ark_eval/tests/test_pilot24_zero_start.py', 'docs/experiment-v3/PILOT24_RECOVERY.md')


def expected_ids():
    ids = ['N01', 'N02', 'N03', 'N04', 'B01', 'B02', 'B03', 'B04', 'R01', 'R02', 'R03', 'R04']
    slots = ('C2-r2-js_callback', 'C1-r2-js_callback', 'C1-r2-gameir_text',
             'M2-r2-gameir_text', 'M2-r2-js_callback', 'M1-r2-js_callback', 'M1-r2-gameir_text')
    return [(s, c) for s in slots for c in ids if s != slots[0] or c not in ('N01', 'N02')]


def retained(*, historical_manifest=None):
    evidence = read(EVIDENCE)
    bindings = evidence['files']
    require(len(bindings) == 1156 and digest(bindings) == EVIDENCE_DIGEST ==
            evidence['canonical_digest'], 'P24R_PARENT_EVIDENCE_ANCHOR')
    names = {p.relative_to(ROOT).as_posix() for base in (OLD_OUTPUT, OLD_CLAIM)
             for p in (ROOT / base).rglob('*') if p.is_file()}
    require(names == set(bindings), 'P24R_PARENT_EVIDENCE_FILE_SET')
    require(all(n.startswith((OLD_OUTPUT + '/', OLD_CLAIM + '/')) and sha(n) == h
                for n, h in bindings.items()), 'P24R_PARENT_EVIDENCE_DRIFT')
    m = parent.load_manifest() if historical_manifest is None else historical_manifest
    require(digest(m) == PARENT_SCOPE and m['batch_key'] == PARENT_KEY, 'P24R_PARENT_SCOPE')
    claim = read(OLD_CLAIM + '/claim.json')
    require(claim['scope_digest'] == PARENT_SCOPE and claim['batch_key'] == PARENT_KEY and
            claim['retry_or_resume_allowed'] is False, 'P24R_PARENT_CLAIM')
    approval = read(parent.APPROVAL.relative_to(ROOT).as_posix())
    require(digest(approval) == claim['approval_digest'], 'P24R_PARENT_APPROVAL_DRIFT')
    return m, bindings


def reconciliation(m, *, validated_static=None):
    cases = parent.case_sets(parent.static_manifest() if validated_static is None else validated_static)
    rows = read(OLD_OUTPUT + '/notrun-initial.json')
    require(digest(rows) == digest(planned_rows(m, cases)), 'P24R_PARENT_INITIAL_ROWS')
    reservations, results = {}, {}
    for r in rows:
        key = (r['slot_id'], r['case_id'])
        suffix = r['slot_id'] + '-' + r['case_id'] + '.json'
        if (ROOT / OLD_OUTPUT / ('reservation-' + suffix)).is_file():
            reservations[key] = read(OLD_OUTPUT + '/reservation-' + suffix)
        if (ROOT / OLD_OUTPUT / ('result-' + suffix)).is_file():
            results[key] = read(OLD_OUTPUT + '/result-' + suffix)
    require(len(reservations) == 194 and len(results) == 193 and
            set(reservations) - set(results) == {UNKNOWN}, 'P24R_PARENT_RESERVATIONS')
    events = [read(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / OLD_CLAIM).glob('event-*.json'))]
    rs = [(e['slot_id'], e['case_id']) for e in events if e['state'] == 'PRIMARY_RESERVED']
    cs = [(e['slot_id'], e['case_id']) for e in events if e['state'] == 'PRIMARY_COMPLETED']
    require(len(rs) == len(set(rs)) == 194 and set(rs) == set(reservations) and
            len(cs) == len(set(cs)) == 193 and set(cs) == set(results), 'P24R_PARENT_EVENTS')
    preserved, allowed = [], []
    for r0 in rows:
        r = copy.deepcopy(r0)
        key = (r['slot_id'], r['case_id'])
        case = next(c for c in cases[r['task_id']] if c['id'] == r['case_id'])
        if key in results:
            value = results[key]
            require(value['expected_ticks'] == case['expected_ticks'] and value['status'] ==
                    ('PASS' if value['actual_ticks'] == case['expected_ticks'] else 'FAIL') and
                    value['cleanup_verified'] is True and value['replay_match'] is True and
                    type(value['primary_case_executions']) is int and value['primary_case_executions'] == 1,
                    'P24R_PARENT_RESULT')
            r.update(value, phase='PRESERVED_DURABLE_PARENT_RESULT', reason=None)
        elif key == UNKNOWN:
            r.update(status='HARNESS_ERROR', phase='PRESERVED_PARENT_UNKNOWN',
                     reason='PARENT_CONTROLLER_LOST', primary_case_executions='UNKNOWN_0_OR_1',
                     candidate_executions='UNKNOWN_AFTER_RESERVATION', JS_transcript_replays='UNKNOWN_0_OR_1')
        elif r['phase'] != 'STATIC_REJECTED':
            allowed.append({'slot_id':r['slot_id'], 'case_id':r['case_id'],
                            'source_sha256':r['source_sha256'], 'case_digest':digest(case)})
        preserved.append(r)
    require([(r['slot_id'],r['case_id']) for r in allowed] == expected_ids() and len(allowed) == 82,
            'P24R_EXACT_82_ONLY')
    require(sum(r['status'] == 'PASS' for r in preserved) == 191 and
            sum(r['status'] == 'FAIL' for r in preserved) == 2, 'P24R_PARENT_COUNTS')
    return cases, preserved, allowed


def zero_start_failure():
    registry = read(FAILED_EVIDENCE)
    files = registry['files']
    require(digest(files) == registry['canonical_digest'] == FAILED_DIGEST,
            'P24Z_FAILED_EVIDENCE_ANCHOR')
    require(all(sha(n) == h for n, h in files.items()), 'P24Z_FAILED_EVIDENCE_DRIFT')
    prefixes = (FAILED_OUTPUT + '/', FAILED_CLAIM + '/', FAILED_LAUNCH + '/')
    names = {p.relative_to(ROOT).as_posix() for base in (FAILED_OUTPUT, FAILED_CLAIM, FAILED_LAUNCH)
             for p in (ROOT / base).rglob('*') if p.is_file()}
    require(names == {n for n in files if n.startswith(prefixes)}, 'P24Z_FAILED_FILE_SET')
    require(not any(Path(n).name.startswith(('reservation-', 'admission-reservation-', 'result-',
                                            'admission-result-')) for n in names), 'P24Z_ALREADY_RESERVED')
    scope = read(FAILED_OUTPUT + '/scope.json')
    summary = read(FAILED_OUTPUT + '/summary.json')
    claim = read(FAILED_CLAIM + '/claim.json')
    events = [read(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / FAILED_CLAIM).glob('event-*.json'))]
    require(digest(scope) == FAILED_SCOPE and scope['recovery_key'] == RECOVERY_KEY and
            [(x['slot_id'], x['case_id']) for x in scope['allowed_rows']] == expected_ids(),
            'P24Z_FAILED_SCOPE')
    require(events == [{'state':'PRECHECK_STARTED', 'slot_id':'C2-r2-js_callback'},
                       {'state':'STOPPED_ON_BLOCK', 'halt':'ADMISSION_OR_PRECHECK_INCOMPLETE'}] and
            summary['state'] == 'STOPPED_ON_BLOCK' and
            summary['halt'] == 'ADMISSION_OR_PRECHECK_INCOMPLETE' and
            summary['scope_digest'] == claim['scope_digest'] == FAILED_SCOPE and
            summary['recovery_key'] == claim['recovery_key'] == RECOVERY_KEY and
            summary['remainder']['counts'] == {'PASS':0,'FAIL':0,'NOTRUN':82,'HARNESS_ERROR':0} and
            all(type(v) is int and v == 0 for field in ('known','unknown_records')
                for v in summary['remainder'][field].values()), 'P24Z_NOT_ZERO_START_FAILURE')
    return {'scope_digest':FAILED_SCOPE, 'evidence_digest':FAILED_DIGEST,
            'registry_sha256':sha(FAILED_EVIDENCE), 'output':FAILED_OUTPUT,
            'claim':FAILED_CLAIM, 'launch':FAILED_LAUNCH,
            'source_version':'13a518e21b6f68965db38b6faf8f72b9d142dcca',
            'primary_reserved':0, 'admission_reserved':0, 'candidate_executions':0}


def make_manifest():
    m, bindings = retained()
    _, _, allowed = reconciliation(m)
    identity = {'parent_scope':PARENT_SCOPE, 'parent_batch_key':PARENT_KEY,
                'retained_evidence_digest':EVIDENCE_DIGEST, 'allowed_rows':allowed}
    require(digest(identity) == RECOVERY_KEY, 'P24Z_LOGICAL_BATCH_CHANGED')
    return {'schema':'gir-pilot24-exact-remainder@2', 'state':'SEALED_PENDING_REVIEW_AND_APPROVAL',
            **identity, 'recovery_key':digest(identity), 'maximum_primary_runs':82,
            'preserved_durable_rows':193, 'excluded_unknown_rows':[list(UNKNOWN)],
            'preserved_static_NOTRUN':12, 'original_denominator':288,
            'retained_evidence_files':1156, 'evidence_registry_sha256':sha(EVIDENCE),
            'parent_approval_sha256':sha(parent.APPROVAL.relative_to(ROOT).as_posix()),
            'bindings':{n:sha(n) for n in FILES}, 'limits':m['limits'],
            'model_calls':0, 'repairs':0, 'retry_or_resume_allowed':False,
            'controller':'direct-wsl-detached-session', 'zero_start_failure':zero_start_failure(),
            'continuation':'ONE_EXPLICIT_ZERO_START_PERMIT_NO_AUTOMATIC_RETRY',
            'API_or_secret_operations':False}


def load_manifest():
    frozen = read(FREEZE)
    require(digest(frozen) == digest(make_manifest()), 'P24R_FREEZE_DRIFT')
    return frozen


def approval_fields(m):
    return {'schema':'gir-pilot24-remainder-explicit-approval@1', 'decision':'APPROVE_EXACT_82_REMAINDER',
            'human_explicit_approval':True, 'scope_digest':digest(m), 'recovery_key':m['recovery_key'],
            'parent_scope':PARENT_SCOPE, 'parent_batch_key':PARENT_KEY,
            'retained_evidence_digest':EVIDENCE_DIGEST, 'allowed_rows_digest':digest(m['allowed_rows']),
            'maximum_primary_runs':82, 'excluded_unknown_rows':[list(UNKNOWN)], 'model_calls':0}


class ApprovedRecovery:
    def __init__(self, manifest, approval):
        statement = approval.get('human_statement') if isinstance(approval,dict) else None
        require(isinstance(statement,str) and 0 < len(statement.strip()) <= 1000, 'P24R_APPROVAL_REQUIRED')
        require(digest(approval) == digest({**approval_fields(manifest),'human_statement':statement}) and
                digest(manifest) == digest(load_manifest()), 'P24R_INEXACT_APPROVAL')
        self._bind(manifest, digest(approval))

    def _bind(self, manifest, approval_digest):
        self.manifest = copy.deepcopy(manifest)
        self.snapshot = digest(manifest)
        self.approval_digest = approval_digest
        if manifest['schema'] in ('gir-pilot24-exact-remainder@3','gir-pilot24-exact-remainder@4','gir-pilot24-exact-remainder@5'):
            provider = forward_provider(manifest)
            self.base = parent.ApprovedPilot24Scope(provider.forward_execution_manifest(manifest),
                self._forward_permit, forward_manifest=manifest)
            self.cases, self.preserved, _ = provider.forward_reconciliation()
        else:
            m = parent.load_manifest()
            self.base = parent.ApprovedPilot24Scope(m, read(parent.APPROVAL.relative_to(ROOT).as_posix()))
            self.cases, self.preserved, _ = reconciliation(m)
        self.preserved_digest = digest(self.preserved)

    def require(self):
        require(type(self.base) is parent.ApprovedPilot24Scope and
                digest(self.manifest) == self.snapshot == digest(
                    forward_provider(self.manifest).load_forward_manifest()
                    if self.manifest['schema'] in ('gir-pilot24-exact-remainder@3','gir-pilot24-exact-remainder@4','gir-pilot24-exact-remainder@5')
                    else load_manifest()) and
                digest(self.preserved) == self.preserved_digest, 'P24R_LIVE_DRIFT')
        self.base.require()

    def slot(self, sid):
        self.require()
        require(sid in {r['slot_id'] for r in self.manifest['allowed_rows']}, 'P24R_EXCLUDED_SLOT')
        return self.base.slot(sid)

    def case(self, sid, case):
        self.slot(sid)
        require(any(r['slot_id'] == sid and r['case_id'] == case['id'] and r['case_digest'] == digest(case)
                    for r in self.manifest['allowed_rows']), 'P24R_EXCLUDED_OR_CHANGED_CASE')
        self.base.case(sid,case)


def zero_start_fields(m):
    return {'schema':'gir-pilot24-zero-start-explicit-permit@1',
            'decision':'APPROVE_SINGLE_ZERO_START_CONTINUATION', 'human_explicit_approval':True,
            'scope_digest':digest(m), 'recovery_key':m['recovery_key'],
            'parent_scope':PARENT_SCOPE, 'parent_batch_key':PARENT_KEY,
            'failed_scope_digest':FAILED_SCOPE, 'failed_evidence_digest':FAILED_DIGEST,
            'allowed_rows_digest':digest(m['allowed_rows']), 'maximum_primary_runs':82,
            'excluded_unknown_rows':[list(UNKNOWN)], 'route':'direct-wsl', 'model_calls':0}


class ApprovedZeroStartRecovery(ApprovedRecovery):
    def __init__(self, manifest, permit):
        statement = permit.get('human_statement') if isinstance(permit,dict) else None
        require(isinstance(statement,str) and 0 < len(statement.strip()) <= 1000, 'P24Z_PERMIT_REQUIRED')
        if manifest['schema'] in ('gir-pilot24-exact-remainder@3','gir-pilot24-exact-remainder@4','gir-pilot24-exact-remainder@5'):
            forward_provider(manifest).validate_forward_permit(manifest, permit)
            self._forward_permit = copy.deepcopy(permit)
        else:
            require(digest(permit) == digest({**zero_start_fields(manifest),'human_statement':statement}) and
                    digest(manifest) == digest(load_manifest()), 'P24Z_INEXACT_PERMIT')
        self._bind(manifest, digest(permit))


# Forward revision is an explicit, finite source change, never a skip-validation flag.
FORWARD_FREEZE = 'ark_eval/pilot24_recovery.v3.freeze.json'
FORWARD_PERMIT = ROOT / 'results/pilot24-forward61-permit.json'
CONTINUED_OUTPUT = 'results/pilot24-recovery-score-direct-001'
CONTINUED_CLAIM = 'results/pilot24-zero-start-claims/' + RECOVERY_KEY
CONTINUED_LAUNCH = 'results/pilot24-zero-start-launches/' + RECOVERY_KEY
CONTINUED_REGISTRY = ('results/pilot24-zero-start-execution-review-20261011-001/'
                      'terminal-production-evidence-before-cleanup.json')
CONTINUED_DIGEST = '433f4e410ddf5d3cc12dc089d3e50ae812a4b49d287f95e259d43113a8602f0f'
CONTINUED_SCOPE = '6cd5dd7ae5b1125ab4d79c126a4384e3c4a4d5de73d43cbab63900797e265275'
CONSUMED_PERMIT_DIGEST = '540d04d33b28ce8f05e9c0ea544c00c7c43d6828dad419be615c4f5b57995357'
REVIEWED_NODE = 'd4186cf55b5348835a8bf446faf05afb622629acd87dd3d11d2749951dfbf833'
BASE_COMMIT = '3ca2b9a1b020d81f6c72b115d91a4fe840c0b9de'
INTEGRATION_BASE = {
    'ark_eval/pilot24_execution_backend.py':'76fbd49a4540f8d8e6008d4fba7249c9b3a96d2a2bb436f8d65fdec1e878342c',
    'ark_eval/pilot24_execution_scope.py':'ff1dfd651006478e4923a12dd6a0dcb21d9a7d0767d1479e43a10c155f1c4afe',
    'ark_eval/pilot24_execution_eval.py':'ff783f080cef1aa33d4968866e4d4076570700b993ef91a5f8e65c250857b8f8',
    'ark_eval/pilot24_recovery_scope.py':'ea5ca200f7067b81a6249ffe58ea7022d5af2155800fd7cb0e7577eae8014318',
    'ark_eval/pilot24_recovery.py':'792ed5568f48181e2d0a785286966c3c94d5ff444ba0d068b69130da71ca9a8d',
    'ark_eval/candidate_eval.py':'99b5eaaee9a60fe00d69ead32871da91e521049a0286c62a6f7b53734ee03f8b',
    'ark_eval/js_runner.py':'6587765c8d2799a8694fdaa5d064b7ded8a048cb2bcbfac9271b7b56e0909143',
    'ark_eval/js_verify.py':'69a865183e1a5a5abc8e502195c23ff100638f2c05b046b001d358cbd93ca6ef'}
FORWARD_FILES = ('ark_eval/tests/test_pilot24_forward61.py',
                 'docs/experiment-v3/PILOT24_FORWARD61.md')


def forward_provider(manifest):
    if manifest.get('schema') == 'gir-pilot24-exact-remainder@5':
        from . import pilot24_forward51_scope
        return pilot24_forward51_scope
    if manifest.get('schema') == 'gir-pilot24-exact-remainder@4':
        from . import pilot24_forward60_scope
        return pilot24_forward60_scope
    import sys
    return sys.modules[__name__]


def forward_static_manifest():
    require(sha(parent.STATIC) == parent.STATIC_SHA, 'P24F_STATIC_ANCHOR')
    m = read(parent.STATIC)
    require(m['counts']['generation_slots'] == 24 and m['counts']['unique_sources'] == 14 and
            m['counts']['planned_primary_case_evaluations'] == 288, 'P24F_STATIC_COUNTS')
    for group in ('anchor_sha256', 'material_bindings', 'runtime_bindings',
                  'material_validation_evidence', 'sender_artifact_bindings',
                  'historical_engineering_evidence', 'additional_maintained_source_bindings'):
        for n, h in m[group].items():
            if n == 'ark_eval/node_backend.py':
                require(h == '706d45d5d0ce8f630e18381525eefe5e9db08f9ac713fe4008674deacf0a474a'
                        and sha(n) == REVIEWED_NODE, 'P24F_REVIEWED_NODE_DRIFT')
            elif n in INTEGRATION_BASE:
                require(h == INTEGRATION_BASE[n], 'P24F_UNREVIEWED_BASE_DELTA')
            else:
                require(sha(n) == h, 'P24F_UNDECLARED_STATIC_DRIFT')
    names = {p.relative_to(ROOT / parent.BUNDLE).as_posix()
             for p in (ROOT / parent.BUNDLE).rglob('*') if p.is_file()}
    require(names == set(m['bundle_files_sha256']) and len(names) == 172, 'P24F_BUNDLE_FILE_SET')
    require({n:sha(parent.BUNDLE + '/' + n) for n in sorted(names)} == m['bundle_files_sha256'] and
            digest(m['bundle_files_sha256']) == m['bundle_posix_manifest_digest'], 'P24F_BUNDLE_DRIFT')
    return m


def historical_parent():
    require(sha(parent.FREEZE.relative_to(ROOT).as_posix()) ==
            '1e785939518905e10544969fc456a9977e641c2f543cb438f9b3d6d8e0ee7182', 'P24F_PARENT_FREEZE_DRIFT')
    m = read(parent.FREEZE.relative_to(ROOT).as_posix())
    retained(historical_manifest=m)
    for n, h in m['bindings'].items():
        if n in INTEGRATION_BASE:
            require(h == INTEGRATION_BASE[n], 'P24F_PARENT_BASE_DELTA')
        else: require(sha(n) == h, 'P24F_PARENT_UNDECLARED_DRIFT')
    return m


def forward_reconciliation():
    """Verify immutable 82-row continuation, then subtract every reserved row."""
    m = historical_parent()
    cases, preserved, allowed82 = reconciliation(m, validated_static=forward_static_manifest())
    require(sha(FREEZE) == '3268eb3e417c88b4f7cd8b8218dd6954b46779f2d6e68726bce5f858ac46fd68'
            and digest(read(FREEZE)) == CONTINUED_SCOPE, 'P24F_V2_FREEZE_DRIFT')
    zero_start_failure()
    registry = read(CONTINUED_REGISTRY)
    files = registry['production_files']
    require(len(files) == 188 and digest(files) == CONTINUED_DIGEST == registry['production_registry_digest'],
            'P24F_CONTINUED_ANCHOR')
    roots = (CONTINUED_OUTPUT, CONTINUED_CLAIM, CONTINUED_LAUNCH)
    names = {p.relative_to(ROOT).as_posix() for base in roots
             for p in (ROOT / base).rglob('*') if p.is_file()}
    names.add(ZERO_START_PERMIT.relative_to(ROOT).as_posix())
    require(names == set(files) and all(sha(n) == h for n, h in files.items()),
            'P24F_CONTINUED_EVIDENCE_DRIFT')
    require(digest(read(CONTINUED_OUTPUT + '/scope.json')) == CONTINUED_SCOPE,
            'P24F_CONSUMED_SCOPE')
    claim = read(CONTINUED_CLAIM + '/claim.json')
    require(claim['scope_digest'] == CONTINUED_SCOPE and claim['recovery_key'] == RECOVERY_KEY and
            claim['permit_digest'] == CONSUMED_PERMIT_DIGEST and claim['maximum_primary_runs'] == 82 and
            claim['retry_or_resume_allowed'] is False and
            digest(read(ZERO_START_PERMIT.relative_to(ROOT).as_posix())) == CONSUMED_PERMIT_DIGEST,
            'P24F_CONSUMED_CLAIM')
    reservations, ledger, results = {}, {}, {}
    for row in allowed82:
        key = (row['slot_id'], row['case_id'])
        suffix = row['slot_id'] + '-' + row['case_id'] + '.json'
        for dest, base, prefix in ((reservations,CONTINUED_OUTPUT,'reservation-'),
                                   (results,CONTINUED_OUTPUT,'result-'), (ledger,CONTINUED_CLAIM,'case-')):
            n = base + '/' + prefix + suffix
            if (ROOT / n).is_file(): dest[key] = read(n)
    expected21 = set(expected_ids()[:21])
    require(set(reservations) == set(results) == set(ledger) == expected21 and len(results) == 21,
            'P24F_RESERVATION_RECONCILIATION')
    events = [read(p.relative_to(ROOT).as_posix()) for p in sorted((ROOT/CONTINUED_CLAIM).glob('event-*.json'))]
    for state in ('PRIMARY_RESERVED','PRIMARY_COMPLETED'):
        keys = [(e['slot_id'],e['case_id']) for e in events if e['state'] == state]
        require(len(keys) == len(set(keys)) == 21 and set(keys) == expected21, 'P24F_EVENT_RECONCILIATION')
    from .pilot24_recovery import validate_result, accounting
    summary = read(CONTINUED_OUTPUT + '/summary.json')
    require(summary['state'] == 'STOPPED_ON_BLOCK' and
            summary['halt'] == 'PRIMARY_OR_PERSISTENCE_INCOMPLETE', 'P24F_CONTINUED_HALT')
    for row in preserved:
        key = (row['slot_id'],row['case_id'])
        if key not in results:
            if key in {(r['slot_id'],r['case_id']) for r in allowed82}:
                row['reason'] = summary['halt']
            continue
        case = next(c for c in cases[row['task_id']] if c['id'] == row['case_id'])
        for receipt in (reservations[key], ledger[key]):
            require(receipt['slot_id'] == key[0] and receipt['case_id'] == key[1] and
                    receipt['source_sha256'] == row['source_sha256'] and
                    type(receipt['maximum_primary_runs']) is int and receipt['maximum_primary_runs'] == 1 and
                    receipt['unknown_outcome'] == 'DO_NOT_RETRY', 'P24F_CASE_LEDGER_BINDING')
        validate_result(row['representation'],case,results[key])
        require(results[key]['status'] == 'PASS', 'P24F_CONTINUED_RESULT_CHANGED')
        row.update(results[key], phase='COMPLETED', reason=None)
    # This sealed aggregate is larger than the 1 MiB wire parser bound. Reconstruct
    # its exact existing writer bytes rather than relax that parser or load arbitrary JSON.
    expected_combined = (json.dumps(preserved,ensure_ascii=False,indent=2) + '\n').encode('utf-8')
    require(hashlib.sha256(expected_combined).hexdigest() == sha(CONTINUED_OUTPUT + '/combined-cases.json'),
            'P24F_COMBINED_HISTORY')
    counts = accounting(preserved)
    require(counts['counts'] == {'PASS':212,'FAIL':2,'NOTRUN':73,'HARNESS_ERROR':1} and
            counts['primary_runs_lower'] == 214 and counts['primary_runs_upper'] == 215,
            'P24F_HISTORICAL_COUNTS')
    require(summary['combined_fixed_288'] == counts and summary['remainder']['counts'] ==
            {'PASS':21,'FAIL':0,'NOTRUN':61,'HARNESS_ERROR':0} and summary['model_calls'] == 0,
            'P24F_CONTINUED_SUMMARY')
    allowed = [r for r in allowed82 if (r['slot_id'],r['case_id']) not in expected21]
    require(len(allowed) == 61 and [(r['slot_id'],r['case_id']) for r in allowed] == expected_ids()[21:]
            and UNKNOWN not in {(r['slot_id'],r['case_id']) for r in allowed}, 'P24F_EXACT_61')
    return cases,preserved,allowed


def forward_execution_manifest(manifest):
    m = copy.deepcopy(historical_parent())
    m['bindings'] = {n:sha(n) for n in parent.FILES}
    m['forward_scope_digest'] = digest(manifest)
    return m


def make_forward_manifest():
    _, preserved, allowed = forward_reconciliation()
    identity = {'parent_scope':PARENT_SCOPE, 'parent_batch_key':PARENT_KEY,
                'retained_evidence_digest':EVIDENCE_DIGEST, 'consumed_scope':CONTINUED_SCOPE,
                'consumed_evidence_digest':CONTINUED_DIGEST, 'consumed_recovery_key':RECOVERY_KEY,
                'allowed_rows':allowed}
    paths = set(FILES + parent.FILES + tuple(INTEGRATION_BASE) + FORWARD_FILES + ('ark_eval/node_backend.py',))
    return {'schema':'gir-pilot24-exact-remainder@3', 'state':'SEALED_PENDING_REVIEW_AND_APPROVAL',
            **identity, 'recovery_key':digest(identity), 'maximum_primary_runs':61,
            'preserved_durable_rows':214, 'preserved_rows_digest':digest(preserved),
            'excluded_unknown_rows':[list(UNKNOWN)], 'preserved_static_NOTRUN':12, 'original_denominator':288,
            'bindings':{n:sha(n) for n in sorted(paths)},
            'integration_base_commit':BASE_COMMIT, 'integration_base_sha256':INTEGRATION_BASE,
            'reviewed_node_sha256':REVIEWED_NODE, 'limits':historical_parent()['limits'],
            'prior_claim':CONTINUED_CLAIM, 'prior_evidence_digest':CONTINUED_DIGEST,
            'old_permits_accepted':False, 'model_calls':0, 'repairs':0, 'retry_or_resume_allowed':False,
            'controller':'direct-wsl-detached-session', 'API_or_secret_operations':False}


def load_forward_manifest():
    frozen = read(FORWARD_FREEZE)
    require(digest(frozen) == digest(make_forward_manifest()), 'P24F_FREEZE_DRIFT')
    return frozen


def forward_fields(m):
    return {'schema':'gir-pilot24-forward61-explicit-permit@1',
            'decision':'APPROVE_SINGLE_FORWARD_61', 'human_explicit_approval':True,
            'scope_digest':digest(m), 'recovery_key':m['recovery_key'], 'parent_scope':PARENT_SCOPE,
            'consumed_scope':CONTINUED_SCOPE, 'consumed_evidence_digest':CONTINUED_DIGEST,
            'allowed_rows_digest':digest(m['allowed_rows']), 'maximum_primary_runs':61,
            'excluded_unknown_rows':[list(UNKNOWN)], 'route':'direct-wsl', 'model_calls':0}


def validate_forward_permit(m, permit):
    statement = permit.get('human_statement') if type(permit) is dict else None
    require(isinstance(statement,str) and 0 < len(statement.strip()) <= 1000, 'P24F_PERMIT_REQUIRED')
    require(digest(permit) == digest({**forward_fields(m),'human_statement':statement}) and
            digest(m) == digest(load_forward_manifest()), 'P24F_INEXACT_PERMIT')
