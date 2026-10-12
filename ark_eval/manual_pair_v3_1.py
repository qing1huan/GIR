"""User-local pair003: pinned review requests, hidden input and explicit send consent.

Default preparation is offline. Codex must not run --user-local-send-pair or
provide real credentials. Responses are inert quarantined text, never executed.
The historical v1/v2 entry and the review freeze are not changed or activated.
"""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import sys
import warnings

from headless_v01.compiler import digest
from .adapter import ACCOUNT_MODEL,EvaluationError,PlanConfig,bounded_json,prepare
from .contract_assessment import make_assessment
from .contract_v3 import ROOT,load_review
from .create_pair import OUTPUT_TOKENS
from .local_key_file import read_local_key
from .manual_pair import attempt,no_run,summarize,write_json

PROTOCOL_DIGEST='cfbea3fe7322e4923882141ab6d2bba0282fa2f0439b2c1cfba7a79fd76158bb'
PACKAGE=ROOT/'results/v3_1-contract-review-20261011/package'
CONFIRMATION='SEND_PAIR003'


def load_review_requests():
    """Only the reviewed package, with no prompt/config override or fallback."""
    contract=load_review()
    if digest(contract)!=PROTOCOL_DIGEST:raise EvaluationError('PAIR003_REVIEW_DIGEST_MISMATCH')
    def public_json(path):
        if (path.is_symlink() or not path.is_file() or path.stat().st_size>1048576
                or not path.resolve().is_relative_to(PACKAGE.resolve())):
            raise EvaluationError('PAIR003_PACKAGE_INVALID')
        return bounded_json(path.read_text(encoding='utf-8'))
    if public_json(PACKAGE/'protocol.json')!=contract:raise EvaluationError('PAIR003_PACKAGE_DRIFT')
    requests=[]
    for arm in contract['public_arms']:
        request=prepare(arm['messages'],OUTPUT_TOKENS,PlanConfig(model=ACCOUNT_MODEL),thinking={'type':'disabled'})
        if (request.body!=arm['request_body'] or request.request_digest!=arm['request_digest']
                or public_json(PACKAGE/arm['representation']/'request.json')!=request.body):
            raise EvaluationError('PAIR003_REQUEST_DRIFT')
        requests.append((arm['representation'],request))
    return contract,requests


def assessment(rep,source_sha256=None):
    return make_assessment(representation=rep,source_sha256=source_sha256,protocol_digest=PROTOCOL_DIGEST)


def prepare_directory(output,contract,requests):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',contract)
    write_json(output/'manual-entry.json',{
        'schema':'gir-pair003-manual-entry@1','mode':'USER_LOCAL_EXPLICIT_REVIEW_DEVELOPMENT_PAIR',
        'protocol_digest':PROTOCOL_DIGEST,'review_freeze_send_enabled':contract['send_enabled'],
        'entry_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'key_file_reader_source_sha256':hashlib.sha256(Path(__file__).with_name('local_key_file.py').read_bytes()).hexdigest(),
        'review_package':'results/v3_1-contract-review-20261011/package',
        'scope':'POST_OBSERVATION_CONTRACT_CLARIFICATION_NOT_INDEPENDENT_BENCHMARK',
        'credential_storage':'NOT_PERSISTED_BY_ENTRY_USER_LOCAL_INPUT_ONLY',
        'candidate_execution':'BLOCKED','automatic_retries':0,'paid_fallback':False,'reset_cards_used':0})
    for rep,request in requests:
        path=output/rep;path.mkdir()
        write_json(path/'request.json',request.body)
        write_json(path/'contract-initial.json',assessment(rep))
    return output


def finish_bundle(output,records,contract,prepared=False):
    summary=summarize(records,contract)
    summary.update(schema='gir-pair003-summary@1',
        scope='POST_OBSERVATION_CONTRACT_CLARIFICATION_DEVELOPMENT_PAIR_NOT_BENCHMARK',
        state='PREPARED_NOT_SENT' if prepared else 'STOPPED_ON_FAILURE' if any(r['failure'] is not None for r in records) else 'RESPONSES_SAVED_UNREVIEWED',
        contract_checker='REPORT_SCHEMA_ONLY_EVALUATOR_EVIDENCE_REQUIRED')
    for audit,row in zip(records,summary['arms']):
        rep=audit['representation']
        write_json(output/rep/'contract-assessment.json',assessment(rep,audit['candidate_sha256']))
        row.update(gameplay_status='NOTRUN',contract_status='NOT_CHECKED',contract_assessment='contract-assessment.json')
    write_json(output/'summary.json',summary)
    return summary


def prepare_only(output):
    contract,requests=load_review_requests()
    output=prepare_directory(output,contract,requests)
    records=[no_run(rep,request,'PREPARED_ONLY') for rep,request in requests]
    for audit in records:write_json(output/audit['representation']/'audit.json',audit)
    return finish_bundle(output,records,contract,prepared=True)


def run_pair(output,credential):
    """Human entry only; tests use an explicitly non-secret fixture and mock HTTP."""
    if (not isinstance(credential,str) or not 8<=len(credential)<=4096
            or any(ord(c)<33 or ord(c)>126 for c in credential)):
        raise EvaluationError('INVALID_LOCAL_CREDENTIAL')
    contract,requests=load_review_requests()
    output=prepare_directory(output,contract,requests);records=[];halt=None
    for rep,request in requests:
        path=output/rep
        if halt is None:
            try:
                if load_review_requests()!=(contract,requests):raise EvaluationError('PAIR003_CONTRACT_DRIFT')
            except (EvaluationError,OSError,UnicodeError,ValueError,TypeError,KeyError):halt='CONTRACT_DRIFT'
        if halt is not None:audit=no_run(rep,request,'PAIR_STOPPED_'+halt);candidate=None
        else:
            write_json(path/'reservation.json',{'schema':'gir-create-reservation@1',
                'representation':rep,'request_digest':request.request_digest,
                'maximum_output_tokens':OUTPUT_TOKENS,'attempts_reserved':1,
                'incomplete_outcome':'UNKNOWN_NOT_RETRYABLE','execution_status':'BLOCKED'})
            audit,candidate=attempt(rep,request,credential)
            # Every failure, including truncation/empty content, halts the pair.
            # A second attempt, alternate route or token-budget increase is absent.
            if audit['failure'] is not None:halt=audit['failure']
            if candidate is not None:
                encoded=candidate.encode('utf-8')
                with (path/'candidate.txt').open('xb') as handle:handle.write(encoded)
                audit.update(candidate_file='candidate.txt',candidate_sha256=hashlib.sha256(encoded).hexdigest())
        write_json(path/'audit.json',audit);records.append(audit)
    return finish_bundle(output,records,contract)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-local-send-pair',action='store_true')
    parser.add_argument('--plan-status-confirmed',action='store_true',
        help='Human verified existing Plan permits this script, quota is sufficient and postpaid is off')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--key-file',type=Path,
        help='Optional user-created single-line key file outside the project and Git checkouts; read only after send confirmation')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; choose a fresh directory. Never resume or retry automatically.')
    try:
        if not args.user_local_send_pair:
            if args.key_file is not None:raise EvaluationError('KEY_FILE_REQUIRES_USER_LOCAL_SEND')
            summary=prepare_only(args.output)
            print(json.dumps({'state':summary['state'],'protocol_digest':PROTOCOL_DIGEST,
                'request_attempts':0,'authenticated_requests':0,'candidate_execution':'BLOCKED'},ensure_ascii=False));return 0
        load_review_requests()  # Reject drift before hidden input or consent.
        if not args.plan_status_confirmed:raise EvaluationError('USER_PLAN_STATUS_REQUIRED')
        if not sys.stdin.isatty() or not sys.stdout.isatty():raise EvaluationError('PRIVATE_INTERACTIVE_TERMINAL_REQUIRED')
        print('pair003 开发小试：仅现有 Plan；两臂各 1 次、4096 tokens、thinking disabled、0 修复。')
        print('模型：'+ACCOUNT_MODEL+'；协议：'+PROTOCOL_DIGEST+'；输出：'+str(args.output))
        if input('输入 '+CONFIRMATION+' 确认发送；其它输入取消: ').strip()!=CONFIRMATION:
            raise EvaluationError('USER_SEND_CANCELLED')
        credential=None
        try:
            if args.key_file is not None:
                credential=read_local_key(args.key_file,project_root=ROOT)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter('error',getpass.GetPassWarning)
                    credential=getpass.getpass('Plan 专用密钥（隐藏输入，仅当前进程使用）: ')
            summary=run_pair(args.output,credential)
        finally:credential=None  # Reduced lifetime, not a memory-zeroization claim.
        print(json.dumps(summary,ensure_ascii=False))
        return int(any(a['status']!='CANDIDATE_RECORDED_UNREVIEWED' for a in summary['arms']))
    except (EvaluationError,getpass.GetPassWarning) as error:
        print(json.dumps({'state':'NOTRUN','code':str(error) if isinstance(error,EvaluationError) else 'HIDDEN_INPUT_UNAVAILABLE'}));return 2
    except (KeyboardInterrupt,EOFError):
        print(json.dumps({'state':'CANCELLED','code':'PRIVATE_INPUT_CANCELLED'}));return 2
    except (OSError,UnicodeError,ValueError,TypeError,KeyError):
        print(json.dumps({'state':'FAIL','code':'LOCAL_IO_OR_BUNDLE_ERROR_NO_RETRY'}));return 2


if __name__=='__main__':raise SystemExit(main())
