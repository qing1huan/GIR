"""User-local frozen four-session batch; default is credential-free preparation.

Codex must not invoke --user-local-send-v4 or read a real key. Only the human
confirms SEND_V4 in a private terminal. Candidates remain inert and blocked.
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
from headless_v01.types import Fault
from .adapter import ACCOUNT_MODEL,EvaluationError,PlanConfig,bounded_json,prepare,import_response
from .contract_assessment import make_assessment
from .door_v4 import ROOT,load_freeze
from .local_key_file import read_local_key
from .manual_pair import write_json
from .manual_probe import (_https_once,RESPONSE_BYTES,safe_error_diagnostic,
                           response_observations,contains_credential_echo)

PROTOCOL_DIGEST='4ddc8d41cac2e8571c2048366c6dd1e5f23881d7e8f00fdaf27d16340e2e9709'
CONFIRMATION='SEND_V4'
DEFAULT_OUTPUT=ROOT/'results/user-local-v4-modify-debug-001'
ENTRY_FREEZE=ROOT/'ark_eval/door_v4_manual_entry.freeze.json'
ENTRY_FILES=('ark_eval/manual_door_v4.py','ark_eval/tests/test_manual_door_v4.py',
    'docs/ark-door-v4-manual-batch.md','ark_eval/local_key_file.py','ark_eval/manual_probe.py',
    'ark_eval/manual_pair.py','ark_eval/adapter.py','ark_eval/contract_assessment.py')
ORDER=(('P-C02-modify','gameir_text'),('P-C02-modify','js_callback'),
       ('P-C02-debug','js_callback'),('P-C02-debug','gameir_text'))


def entry_bindings():
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ENTRY_FILES}


def load_requests():
    contract=load_freeze()
    if digest(contract)!=PROTOCOL_DIGEST:raise EvaluationError('V4_FIXED_PROTOCOL_DIGEST_MISMATCH')
    bound=bounded_json(ENTRY_FREEZE.read_text(encoding='utf-8'))
    if (bound!={'schema':'gir-v4-user-local-entry-bindings@1','protocol_digest':PROTOCOL_DIGEST,
            'artifact_sha256':entry_bindings(),'human_private_confirmation':CONFIRMATION,
            'agent_send_authorized':False,'candidate_execution_authorized':False}):
        raise EvaluationError('V4_ENTRY_BINDING_DRIFT')
    sequence=tuple((row['task_id'],rep) for row in contract['order'] for rep in row['arms'])
    if sequence!=ORDER or len(contract['public_arms'])!=4:raise EvaluationError('V4_DISPATCH_ORDER_DRIFT')
    by_key={(a['task_id'],a['representation']):a for a in contract['public_arms']}
    if set(by_key)!=set(ORDER):raise EvaluationError('V4_REQUEST_SET_DRIFT')
    requests=[]
    for task,rep in sequence:
        arm=by_key[task,rep];body=arm['request_body']
        request=prepare(body['messages'],4096,PlanConfig(model=ACCOUNT_MODEL),thinking={'type':'disabled'})
        if (body!=request.body or request.request_digest!=arm['request_digest']
                or request.prompt_units!=arm['prompt_units_estimate'] or request.prompt_units>24000):
            raise EvaluationError('V4_FIXED_REQUEST_DRIFT')
        requests.append((task,rep,request))
    return contract,requests,bound


def no_run(task,rep,request,reason):
    return {'schema':'gir-v4-arm-audit@1','task_id':task,'representation':rep,
        'request_digest':request.request_digest,'status':'NOTRUN','failure':None,'notrun_reason':reason,
        'http_status':None,'request_attempts':0,'reported_model_calls':0,'usage':None,'finish_reason':None,
        'model_matches_requested':None,'model_identity_digest':None,'candidate_digest':None,
        'candidate_sha256':None,'candidate_bytes':None,'candidate_file':None,
        'response_observations':None,'provider_error_code':None,'provider_error_diagnostic':'NOT_APPLICABLE',
        'prompt_byte_units_estimate':request.prompt_units,'prompt_byte_units_ceiling':24000,
        'input_units_are_tokens':False,'input_token_upper_bound_claimed':False,'input_padding_added':False,
        'maximum_output_tokens':4096,'reservation_retained':False,'ledger_events':[],
        'repairs':0,'tool_calls':0,'execution_status':'BLOCKED','gameplay_status':'NOTRUN',
        'contract_status':'NOT_CHECKED','generated_source_executions':0,
        'timestamp_utc':datetime.now(timezone.utc).isoformat()}


def attempt(task,rep,request,credential):
    audit=no_run(task,rep,request,'NOT_APPLICABLE');candidate=None;imported=None;events=[]
    audit.update(status='FAIL',request_attempts=1,reported_model_calls=None,reservation_retained=True)
    def append(event):
        row={'sequence':len(events),'previous':events[-1]['hash'] if events else None,**event}
        row['hash']=digest(row);events.append(row)
    append({'event':'RESERVED','request_digest':request.request_digest,'requests':1,'repairs':0,
        'reserved_output_tokens':4096,'input_byte_unit_ceiling':24000,'input_units_are_tokens':False})
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
                if not imported.model_matches_requested:audit['failure']='MODEL_MISMATCH'
                elif imported.tokens_out>4096:audit['failure']='OUTPUT_TOKEN_LIMIT'
                elif (audit['response_observations']['reasoning_content_type']=='other'
                      or (audit['response_observations']['reasoning_content_chars'] or 0)>0):audit['failure']='THINKING_MISMATCH'
                elif len(encoded)>16384:audit['failure']='SOURCE_TOO_LARGE'
                elif imported.finish_reason=='length':audit['failure']='TRUNCATED';candidate=imported.candidate if encoded else None
                elif not imported.candidate:audit['failure']='EMPTY_RESPONSE'
                else:audit['status']='CANDIDATE_RECORDED_UNREVIEWED';candidate=imported.candidate
    except (KeyboardInterrupt,EOFError):audit['failure']='CANCELLED'
    except (UnicodeError,EvaluationError,ValueError,TypeError,KeyError):audit['failure']='INVALID_RESPONSE'
    except (OSError,http.client.HTTPException):audit['failure']='TRANSPORT'
    audit['reservation_retained']=imported is None or audit['failure'] is not None
    append({'event':'FINISHED','status':audit['status'],'failure':audit['failure'],
        'usage':audit['usage'],'reservation_retained':audit['reservation_retained']})
    audit['ledger_events']=events;return audit,candidate


def assessment(rep,source_sha256=None):
    return make_assessment(representation=rep,source_sha256=source_sha256,protocol_digest=PROTOCOL_DIGEST)


def prepare_directory(output,contract,requests,bound):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',contract)
    write_json(output/'manual-entry.json',{'schema':'gir-v4-user-local-entry@1','protocol_digest':PROTOCOL_DIGEST,
        'entry_bindings':bound,'dispatch_order':[[t,r] for t,r,_ in requests],
        'candidate_execution':'BLOCKED','credential_storage':'NOT_PERSISTED_PRIVATE_USER_INPUT_ONLY',
        'maximum_batch_attempts':4,'maximum_batch_output_tokens':16384,'automatic_retries':0,
        'paid_fallback':False,'reset_cards_used':0,'unknown_outcome':'KEEP_RESERVATION_NO_AUTOMATIC_RESUME'})
    for task,rep,request in requests:
        folder=output/task/rep;folder.mkdir(parents=True)
        write_json(folder/'request.json',request.body)
        write_json(folder/'contract-initial.json',assessment(rep))
        write_json(folder/'notrun-initial.json',no_run(task,rep,request,'BATCH_NOT_STARTED'))
    return output


def finish_bundle(output,records,prepared=False):
    rows=[]
    for audit in records:
        task,rep=audit['task_id'],audit['representation'];folder=output/task/rep
        write_json(folder/'contract-assessment.json',assessment(rep,audit['candidate_sha256']))
        rows.append({k:audit[k] for k in ('task_id','representation','request_digest','status','failure',
            'request_attempts','reported_model_calls','usage','finish_reason','candidate_sha256','candidate_bytes',
            'execution_status','gameplay_status','contract_status','repairs','tool_calls')})
    summary={'schema':'gir-v4-user-local-batch-summary@1','protocol_digest':PROTOCOL_DIGEST,
        'state':'PREPARED_NOT_SENT' if prepared else 'RESPONSES_SAVED_UNREVIEWED' if all(r['status']=='CANDIDATE_RECORDED_UNREVIEWED' for r in records) else 'STOPPED_ON_FAILURE',
        'dispatch_order':[[r['task_id'],r['representation']] for r in records],'arms':rows,
        'request_attempts':sum(r['request_attempts'] for r in records),
        'reported_model_calls':sum(r['reported_model_calls'] or 0 for r in records),
        'unknown_model_call_outcomes':sum(r['reported_model_calls'] is None for r in records),
        'known_usage':{k:sum((r['usage'] or {}).get(k,0) for r in records) for k in ('prompt_tokens','completion_tokens','total_tokens')},
        'maximum_batch_attempts':4,'maximum_batch_output_tokens':16384,'repairs':0,'tool_calls':0,
        'automatic_retries':0,'paid_fallback':False,'input_units_are_tokens':False,'input_padding_added':False,
        'generated_source_executions':0,'reset_cards_used':0,'push_performed':False,
        'gameplay':{t:{r:{'legacy26':{'PASS':0,'FAIL':0,'NOTRUN':26},'diagnostic8':{'PASS':0,'FAIL':0,'NOTRUN':8}}
            for r in ('gameir_text','js_callback')} for t in ('P-C02-modify','P-C02-debug')},
        'scope':'FOUR_INDEPENDENT_DEVELOPMENT_SESSIONS_NOT_HOLDOUT_OR_AI_ADVANTAGE'}
    write_json(output/'summary.json',summary);return summary


def prepare_only(output):
    contract,requests,bound=load_requests();output=prepare_directory(output,contract,requests,bound)
    records=[no_run(t,r,q,'PREPARED_ONLY') for t,r,q in requests]
    for audit in records:write_json(output/audit['task_id']/audit['representation']/'audit.json',audit)
    return finish_bundle(output,records,prepared=True)


def run_batch(output,credential):
    """Human-only entry; tests pass public fixtures with mocked transport."""
    if (not isinstance(credential,str) or not 8<=len(credential)<=4096
            or any(ord(c)<33 or ord(c)>126 for c in credential)):raise EvaluationError('INVALID_LOCAL_CREDENTIAL')
    contract,requests,bound=load_requests();output=prepare_directory(output,contract,requests,bound)
    records=[];halt=None
    for index,(task,rep,request) in enumerate(requests,1):
        path=output/task/rep
        if halt is None:
            try:
                if load_requests()!=(contract,requests,bound):raise EvaluationError('V4_BATCH_BINDING_DRIFT')
            except (EvaluationError,Fault,OSError,UnicodeError,ValueError,TypeError,KeyError):halt='CONTRACT_OR_ENTRY_DRIFT'
        if halt is not None:audit=no_run(task,rep,request,'BATCH_STOPPED_'+halt);candidate=None
        else:
            write_json(path/'reservation.json',{'schema':'gir-v4-reservation@1','task_id':task,'representation':rep,
                'dispatch_index':index,'request_digest':request.request_digest,'requests_reserved':1,'maximum_output_tokens':4096,
                'prompt_byte_units_estimate':request.prompt_units,'common_byte_unit_ceiling':24000,
                'input_units_are_tokens':False,'repairs':0,'unknown_outcome':'UNKNOWN_NOT_RETRYABLE','execution_status':'BLOCKED'})
            audit,candidate=attempt(task,rep,request,credential)
            if audit['failure'] is not None:halt=audit['failure']
            if candidate is not None:
                encoded=candidate.encode('utf-8')
                with (path/'candidate.txt').open('xb') as f:f.write(encoded)
                audit.update(candidate_file='candidate.txt',candidate_sha256=hashlib.sha256(encoded).hexdigest())
        audit['dispatch_index']=index;write_json(path/'audit.json',audit);records.append(audit)
    return finish_bundle(output,records)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-local-send-v4',action='store_true')
    parser.add_argument('--plan-status-confirmed',action='store_true',help='User confirms existing Plan allows script, quota sufficient, postpaid off')
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--key-file',type=Path,help='Explicit user-created key file outside project/Git; read only after SEND_V4')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; fresh output required. No automatic resume or retry.')
    try:
        if not args.user_local_send_v4:
            if args.key_file is not None:raise EvaluationError('KEY_FILE_REQUIRES_USER_LOCAL_SEND')
            summary=prepare_only(args.output)
            print(json.dumps({'state':summary['state'],'protocol_digest':PROTOCOL_DIGEST,
                'request_attempts':0,'authenticated_requests':0,'candidate_execution':'BLOCKED'}));return 0
        load_requests()
        if not args.plan_status_confirmed:raise EvaluationError('USER_PLAN_STATUS_REQUIRED')
        if not sys.stdin.isatty() or not sys.stdout.isatty():raise EvaluationError('PRIVATE_INTERACTIVE_TERMINAL_REQUIRED')
        print('V4 existing Plan batch only: Modify IR -> JS; Debug JS -> IR; four independent sessions.')
        print('Each one request, max_tokens4096, thinking disabled; batch maximum output16384 tokens. No repairs/tools/retries/fallback.')
        print('Each input cap24000 byte+overhead estimate units, NOT tokens; actual usage may differ; no padding.')
        print('Model: '+ACCOUNT_MODEL+'; protocol: '+PROTOCOL_DIGEST+'; output: '+str(args.output))
        if input('Type '+CONFIRMATION+' to send this batch; anything else cancels: ').strip()!=CONFIRMATION:
            raise EvaluationError('USER_SEND_CANCELLED')
        credential=None
        try:
            if args.key_file is not None:credential=read_local_key(args.key_file,project_root=ROOT)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter('error',getpass.GetPassWarning)
                    credential=getpass.getpass('Plan key (hidden, current process only): ')
            summary=run_batch(args.output,credential)
        finally:credential=None
        print(json.dumps(summary,ensure_ascii=False));return int(summary['state']!='RESPONSES_SAVED_UNREVIEWED')
    except (EvaluationError,getpass.GetPassWarning) as e:
        print(json.dumps({'state':'NOTRUN','code':str(e) if isinstance(e,EvaluationError) else 'HIDDEN_INPUT_UNAVAILABLE'}));return 2
    except (KeyboardInterrupt,EOFError):
        print(json.dumps({'state':'CANCELLED','code':'PRIVATE_INPUT_CANCELLED'}));return 2
    except (Fault,OSError,UnicodeError,ValueError,TypeError,KeyError):
        print(json.dumps({'state':'FAIL','code':'LOCAL_PROTOCOL_OR_IO_ERROR_NO_RETRY'}));return 2


if __name__=='__main__':raise SystemExit(main())
