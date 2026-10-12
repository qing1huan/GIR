"""Fixed user-local single repair round. Default is offline preparation only.

Codex must never run --user-local-send-repair or read a real credential. Only
the user confirms SEND_REPAIR in a private local terminal. No source execution.
"""
import argparse
from datetime import datetime,timezone
import getpass
import hashlib
import http.client
import json
from pathlib import Path
import sys
import warnings

from headless_v01.compiler import digest
from .adapter import ACCOUNT_MODEL,EvaluationError,PlanConfig,bounded_json,prepare,import_response
from .contract_assessment import make_assessment
from .local_key_file import read_local_key
from .manual_pair import write_json,summarize
from .manual_probe import (_https_once,RESPONSE_BYTES,safe_error_diagnostic,
                           response_observations,contains_credential_echo)
from .pair003_eval import ROOT,source_bindings
from .pair003_repair_draft import build_draft

FREEZE=ROOT/'ark_eval/create_repair_pair003.freeze.json'
PROTOCOL_DIGEST='5b9b59205c412ba870d7c526db57f870ade3a626dd2664495f31c936ba27c779'
CONFIRMATION='SEND_REPAIR'
ARMS=('gameir_text','js_callback')
OUTPUT_TOKENS=4096
BYTE_UNIT_CAP=24000
SOURCE_BYTES=16384


def load_repair_requests():
    if FREEZE.is_symlink() or not FREEZE.is_file() or FREEZE.stat().st_size>1048576:
        raise EvaluationError('REPAIR_FREEZE_INVALID')
    contract=bounded_json(FREEZE.read_text(encoding='utf-8'))
    if digest(contract)!=PROTOCOL_DIGEST:raise EvaluationError('REPAIR_FREEZE_DIGEST_MISMATCH')
    if (contract['schema']!='gir-create-repair-freeze@1' or not contract['human_send_enabled']
            or contract['agent_send_authorized'] or contract['source_evaluation_authorized']
            or contract['proposal']!=build_draft()):raise EvaluationError('REPAIR_PROTOCOL_DRIFT')
    bindings=source_bindings()
    for name in ('ark_eval/pair003_repair_draft.py','ark_eval/tests/test_pair003_repair_draft.py'):
        bindings[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    if bindings!=contract['artifact_hashes']:raise EvaluationError('REPAIR_SOURCE_BINDING_DRIFT')
    requests=[]
    if tuple(a['representation'] for a in contract['proposal']['arms'])!=ARMS:
        raise EvaluationError('REPAIR_ARMS_REJECTED')
    for arm in contract['proposal']['arms']:
        body=arm['request_body']
        request=prepare(body['messages'],OUTPUT_TOKENS,PlanConfig(model=ACCOUNT_MODEL),thinking={'type':'disabled'})
        if (body!=request.body or arm['request_digest']!=request.request_digest
                or request.prompt_units!=arm['prompt_byte_units'] or request.prompt_units>BYTE_UNIT_CAP):
            raise EvaluationError('REPAIR_REQUEST_DRIFT')
        requests.append((arm['representation'],request))
    return contract,requests


def no_run(rep,request,reason):
    return {'schema':'gir-repair-arm-audit@1','representation':rep,'request_digest':request.request_digest,
        'status':'NOTRUN','failure':None,'http_status':None,'request_attempts':0,'reported_model_calls':0,
        'usage':None,'finish_reason':None,'model_matches_requested':None,'model_identity_digest':None,
        'candidate_digest':None,'candidate_sha256':None,'candidate_bytes':None,'candidate_file':None,
        'response_observations':None,'provider_error_code':None,'provider_error_diagnostic':reason,
        'prompt_byte_units_estimate':request.prompt_units,'prompt_byte_units_ceiling':BYTE_UNIT_CAP,
        'input_units_are_tokens':False,'actual_token_usage_may_differ':True,'input_padding_added':False,
        'input_token_upper_bound_claimed':False,'maximum_output_tokens':OUTPUT_TOKENS,
        'reservation_retained':False,'ledger_events':[],'execution_status':'BLOCKED','test_status':'NOTRUN',
        'generated_source_executions':0,'repairs':0,'tool_calls':0,'timestamp_utc':datetime.now(timezone.utc).isoformat()}


def attempt(rep,request,credential):
    """Reuse existing Plan-only HTTP/sanitizers; keep bytes and token usage apart."""
    audit=no_run(rep,request,'NOT_APPLICABLE');candidate=None;imported=None
    audit.update(status='FAIL',request_attempts=1,reported_model_calls=None,repairs=1,reservation_retained=True)
    events=[]
    def append(event):
        row={'sequence':len(events),'previous':events[-1]['hash'] if events else None,**event}
        row['hash']=digest(row);events.append(row)
    append({'event':'RESERVED','request_digest':request.request_digest,'repair_requests':1,
        'prompt_byte_units_estimate':request.prompt_units,'common_byte_unit_ceiling':BYTE_UNIT_CAP,
        'reserved_output_tokens':OUTPUT_TOKENS,'input_units_are_tokens':False})
    try:
        status,body=_https_once(request,credential,timeout=120);audit['http_status']=status
        if status!=200:audit['provider_error_code'],audit['provider_error_diagnostic']=safe_error_diagnostic(body,credential)
        if status==401:audit['failure']='AUTH'
        elif status==403:audit['failure']='FORBIDDEN'
        elif status==429:audit['failure']='QUOTA_OR_RATE_LIMIT'
        elif 300<=status<400:audit['failure']='REDIRECT_REJECTED'
        elif status!=200:audit['failure']='HTTP_ERROR'
        elif len(body)>RESPONSE_BYTES:audit['failure']='RESPONSE_TOO_LARGE'
        elif credential.encode('ascii') in body:audit['failure']='SECRET_ECHO_REJECTED'
        else:
            raw=body.decode('utf-8',errors='strict');data=bounded_json(raw)
            if contains_credential_echo(data,credential):audit['failure']='SECRET_ECHO_REJECTED'
            else:
                audit['response_observations']=response_observations(data)
                imported=import_response(raw,requested_model=ACCOUNT_MODEL)
                audit.update(reported_model_calls=1,usage={'prompt_tokens':imported.tokens_in,
                    'completion_tokens':imported.tokens_out,'total_tokens':imported.tokens_in+imported.tokens_out},
                    finish_reason=imported.finish_reason,model_matches_requested=imported.model_matches_requested,
                    model_identity_digest=imported.model_identity_digest,candidate_digest=imported.candidate_digest)
                encoded=imported.candidate.encode('utf-8');audit['candidate_bytes']=len(encoded)
                # No input-token-vs-byte comparison: these are different units.
                if not imported.model_matches_requested:audit['failure']='MODEL_MISMATCH'
                elif imported.tokens_out>OUTPUT_TOKENS:audit['failure']='OUTPUT_TOKEN_LIMIT'
                elif (audit['response_observations']['reasoning_content_type']=='other'
                      or (audit['response_observations']['reasoning_content_chars'] or 0)>0):audit['failure']='THINKING_MISMATCH'
                elif len(encoded)>SOURCE_BYTES:audit['failure']='SOURCE_TOO_LARGE'
                elif imported.finish_reason=='length':audit['failure']='TRUNCATED';candidate=imported.candidate if encoded else None
                elif not imported.candidate:audit['failure']='EMPTY_RESPONSE'
                else:audit['status']='CANDIDATE_RECORDED_UNREVIEWED';candidate=imported.candidate
    except (KeyboardInterrupt,EOFError):audit['failure']='CANCELLED'
    except (UnicodeError,EvaluationError,ValueError,TypeError,KeyError):audit['failure']='INVALID_RESPONSE'
    except (OSError,http.client.HTTPException):audit['failure']='TRANSPORT'
    audit['reservation_retained']=imported is None or audit['failure'] is not None
    append({'event':'FINISHED','status':audit['status'],'failure':audit['failure'],
        'usage':audit['usage'],'reservation_retained':audit['reservation_retained']})
    audit['ledger_events']=events
    return audit,candidate


def assessment(rep,source_sha256=None):
    return make_assessment(representation=rep,source_sha256=source_sha256,protocol_digest=PROTOCOL_DIGEST)


def prepare_directory(output,contract,requests):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',contract)
    write_json(output/'manual-entry.json',{'schema':'gir-repair-manual-entry@1','protocol_digest':PROTOCOL_DIGEST,
        'mode':'USER_LOCAL_CONFIRMED_SINGLE_REPAIR_ROUND','candidate_execution':'BLOCKED',
        'entry_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'key_file_reader_source_sha256':hashlib.sha256(Path(__file__).with_name('local_key_file.py').read_bytes()).hexdigest(),
        'credential_storage':'NOT_PERSISTED_USER_LOCAL_INPUT_ONLY','confirmation':CONFIRMATION,
        'automatic_retries':0,'paid_fallback':False,'reset_cards_used':0})
    for rep,request in requests:
        folder=output/rep;folder.mkdir();write_json(folder/'request.json',request.body)
        write_json(folder/'contract-initial.json',assessment(rep))
    return output


def finish_bundle(output,records,contract,prepared=False):
    summary=summarize(records,contract)
    summary.update(schema='gir-repair-pair-summary@1',scope='PAIR003_SINGLE_REPAIR_DEVELOPMENT_TRIAL_NOT_BENCHMARK',
        state='PREPARED_NOT_SENT' if prepared else 'STOPPED_ON_FAILURE' if any(r['status']!='CANDIDATE_RECORDED_UNREVIEWED' for r in records) else 'RESPONSES_SAVED_UNREVIEWED',
        repairs=sum(r['repairs'] for r in records),prompt_byte_units_ceiling_per_arm=BYTE_UNIT_CAP,
        input_units_are_tokens=False,actual_usage_may_differ=True,input_padding_added=False,
        original_generation_protocol_digest=contract['proposal']['parent_generation_protocol_digest'],
        original_candidate_sha256=contract['proposal']['original_candidate_sha256'],
        contract_checker='REPORT_SCHEMA_ONLY_EVALUATOR_EVIDENCE_REQUIRED')
    for audit,row in zip(records,summary['arms']):
        rep=audit['representation'];write_json(output/rep/'contract-assessment.json',assessment(rep,audit['candidate_sha256']))
        row.update(gameplay_status='NOTRUN',contract_status='NOT_CHECKED',
            prompt_byte_units_estimate=audit['prompt_byte_units_estimate'],repairs=audit['repairs'])
    write_json(output/'summary.json',summary);return summary


def prepare_only(output):
    contract,requests=load_repair_requests();output=prepare_directory(output,contract,requests)
    records=[no_run(rep,request,'PREPARED_ONLY') for rep,request in requests]
    for audit in records:write_json(output/audit['representation']/'audit.json',audit)
    return finish_bundle(output,records,contract,prepared=True)


def run_repair(output,credential):
    """Human entry only; all engineering tests use public fixtures and mock HTTP."""
    if (not isinstance(credential,str) or not 8<=len(credential)<=4096
            or any(ord(c)<33 or ord(c)>126 for c in credential)):raise EvaluationError('INVALID_LOCAL_CREDENTIAL')
    contract,requests=load_repair_requests();output=prepare_directory(output,contract,requests)
    records=[];halt=None
    for rep,request in requests:
        path=output/rep
        if halt is None:
            try:
                if load_repair_requests()!=(contract,requests):raise EvaluationError('REPAIR_PROTOCOL_DRIFT')
            except (EvaluationError,OSError,UnicodeError,ValueError,TypeError,KeyError):halt='CONTRACT_DRIFT'
        if halt is not None:audit=no_run(rep,request,'PAIR_STOPPED_'+halt);candidate=None
        else:
            write_json(path/'reservation.json',{'schema':'gir-repair-reservation@1','representation':rep,
                'request_digest':request.request_digest,'repair_requests_reserved':1,'maximum_output_tokens':OUTPUT_TOKENS,
                'prompt_byte_units_estimate':request.prompt_units,'common_byte_unit_ceiling':BYTE_UNIT_CAP,
                'input_units_are_tokens':False,'incomplete_outcome':'UNKNOWN_NOT_RETRYABLE','execution_status':'BLOCKED'})
            audit,candidate=attempt(rep,request,credential)
            if audit['failure'] is not None:halt=audit['failure']
            if candidate is not None:
                encoded=candidate.encode('utf-8')
                with (path/'candidate.txt').open('xb') as handle:handle.write(encoded)
                audit.update(candidate_file='candidate.txt',candidate_sha256=hashlib.sha256(encoded).hexdigest())
        write_json(path/'audit.json',audit);records.append(audit)
    return finish_bundle(output,records,contract)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-local-send-repair',action='store_true')
    parser.add_argument('--plan-status-confirmed',action='store_true',help='User confirmed existing Plan permits this script, quota sufficient and postpaid off')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--key-file',type=Path,help='Optional user-created key file outside project/Git; read only after SEND_REPAIR')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; choose a fresh directory. No automatic resume/retry.')
    try:
        if not args.user_local_send_repair:
            if args.key_file is not None:raise EvaluationError('KEY_FILE_REQUIRES_USER_LOCAL_SEND')
            summary=prepare_only(args.output)
            print(json.dumps({'state':summary['state'],'protocol_digest':PROTOCOL_DIGEST,'request_attempts':0,
                'authenticated_requests':0,'candidate_execution':'BLOCKED'}));return 0
        load_repair_requests()
        if not args.plan_status_confirmed:raise EvaluationError('USER_PLAN_STATUS_REQUIRED')
        if not sys.stdin.isatty() or not sys.stdout.isatty():raise EvaluationError('PRIVATE_INTERACTIVE_TERMINAL_REQUIRED')
        print('Pair003 repair: existing Plan only; each arm one request, max_tokens=4096, thinking disabled, no retries/tools.')
        print('Common input cap: 24000 byte+overhead estimate units, NOT tokens. Actual token usage may differ; no padding.')
        print('Model: '+ACCOUNT_MODEL+'; protocol: '+PROTOCOL_DIGEST+'; output: '+str(args.output))
        if input('Type '+CONFIRMATION+' to send; anything else cancels: ').strip()!=CONFIRMATION:
            raise EvaluationError('USER_SEND_CANCELLED')
        credential=None
        try:
            if args.key_file is not None:credential=read_local_key(args.key_file,project_root=ROOT)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter('error',getpass.GetPassWarning)
                    credential=getpass.getpass('Plan key (hidden, current process only): ')
            summary=run_repair(args.output,credential)
        finally:credential=None
        print(json.dumps(summary,ensure_ascii=False))
        return int(any(a['status']!='CANDIDATE_RECORDED_UNREVIEWED' for a in summary['arms']))
    except (EvaluationError,getpass.GetPassWarning) as error:
        print(json.dumps({'state':'NOTRUN','code':str(error) if isinstance(error,EvaluationError) else 'HIDDEN_INPUT_UNAVAILABLE'}));return 2
    except (KeyboardInterrupt,EOFError):
        print(json.dumps({'state':'CANCELLED','code':'PRIVATE_INPUT_CANCELLED'}));return 2
    except (OSError,UnicodeError,ValueError,TypeError,KeyError):
        print(json.dumps({'state':'FAIL','code':'LOCAL_IO_OR_BUNDLE_ERROR_NO_RETRY'}));return 2


if __name__=='__main__':raise SystemExit(main())
