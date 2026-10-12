"""Human-local frozen two-request Create pilot; default preparation is offline.

Codex must not run --user-local-send-pair or provide credentials. Generated
IR/JS is bounded inert text in a quarantine directory, never executed/imported.
"""
import argparse
from datetime import datetime,timezone
import getpass
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
import warnings

from headless_v01.compiler import digest
from .adapter import ACCOUNT_MODEL,EvaluationError,bounded_json,import_response
from .budget import Ledger,verify_chain
from .create_pair import ARMS,OUTPUT_TOKENS,SOURCE_BYTES,arm_budget,load_freeze,prepared_requests,import_contract
from .manual_probe import (_https_once,RESPONSE_BYTES,OBSERVATION_KEYS,SAFE_ERROR_CODES,ERROR_DIAGNOSTICS,
                           safe_error_diagnostic,response_observations,contains_credential_echo)

HALT_FAILURES={'AUTH','FORBIDDEN','QUOTA_OR_RATE_LIMIT','REDIRECT_REJECTED','HTTP_ERROR',
               'TRANSPORT','INVALID_RESPONSE','RESPONSE_TOO_LARGE','SECRET_ECHO_REJECTED',
               'MODEL_MISMATCH','USAGE_EXCEEDS_RESERVATION','CANCELLED','CONTRACT_DRIFT','THINKING_MISMATCH'}
FAILURES=HALT_FAILURES|{'TRUNCATED','EMPTY_RESPONSE','SOURCE_TOO_LARGE',None}
AUDIT_KEYS={'schema','representation','request_digest','status','failure','http_status',
            'request_attempts','reported_model_calls','usage','finish_reason','model_matches_requested',
            'model_identity_digest','candidate_digest','candidate_sha256','candidate_bytes',
            'candidate_file','response_observations','provider_error_code','provider_error_diagnostic',
            'ledger_budget','ledger_charged','ledger_events','execution_status','test_status',
            'generated_source_executions','repairs','tool_calls','timestamp_utc'}


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2);handle.write('\n')


def no_run(rep,request,reason):
    return {'schema':'gir-create-arm-audit@1','representation':rep,'request_digest':request.request_digest,
            'status':'NOTRUN','failure':None,'http_status':None,'request_attempts':0,
            'reported_model_calls':0,'usage':None,'finish_reason':None,'model_matches_requested':None,
            'model_identity_digest':None,'candidate_digest':None,'candidate_sha256':None,
            'candidate_bytes':None,'candidate_file':None,'response_observations':None,
            'provider_error_code':None,'provider_error_diagnostic':reason,
            'ledger_budget':arm_budget().__dict__,'ledger_charged':None,'ledger_events':[],
            'execution_status':'BLOCKED','test_status':'NOTRUN','generated_source_executions':0,
            'repairs':0,'tool_calls':0,'timestamp_utc':datetime.now(timezone.utc).isoformat()}


def attempt(rep,request,credential):
    ledger=Ledger(arm_budget(),requested_model=ACCOUNT_MODEL)
    call_id=ledger.reserve(request)
    audit=no_run(rep,request,'NOT_APPLICABLE')
    audit.update(status='FAIL',request_attempts=1,reported_model_calls=None)
    candidate=None;imported=None
    try:
        status,body=_https_once(request,credential,timeout=120)
        audit['http_status']=status
        if status!=200:
            audit['provider_error_code'],audit['provider_error_diagnostic']=safe_error_diagnostic(body,credential)
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
                encoded=imported.candidate.encode('utf-8')
                audit['candidate_bytes']=len(encoded)
                if not imported.model_matches_requested:audit['failure']='MODEL_MISMATCH'
                elif imported.tokens_out>OUTPUT_TOKENS or imported.tokens_in>request.prompt_units:
                    audit['failure']='USAGE_EXCEEDS_RESERVATION'
                elif (request.body.get('thinking')=={'type':'disabled'} and
                      (audit['response_observations']['reasoning_content_type']=='other' or
                       (audit['response_observations']['reasoning_content_chars'] or 0)>0)):
                    audit['failure']='THINKING_MISMATCH'
                elif len(encoded)>SOURCE_BYTES:audit['failure']='SOURCE_TOO_LARGE'
                elif imported.finish_reason=='length':
                    audit['failure']='TRUNCATED';candidate=imported.candidate if encoded else None
                elif not imported.candidate:audit['failure']='EMPTY_RESPONSE'
                else:audit['status']='CANDIDATE_RECORDED_UNREVIEWED';candidate=imported.candidate
    except (KeyboardInterrupt,EOFError):audit['failure']='CANCELLED'
    except (UnicodeError,EvaluationError,ValueError,TypeError,KeyError):audit['failure']='INVALID_RESPONSE'
    except OSError:audit['failure']='TRANSPORT'
    except http.client.HTTPException:audit['failure']='TRANSPORT'
    if imported is not None:ledger.finish(call_id,response=imported)
    else:
        category='AUTH' if audit['failure'] in {'AUTH','FORBIDDEN'} else 'QUOTA' if audit['failure']=='QUOTA_OR_RATE_LIMIT' else 'TRANSPORT' if audit['failure'] in {'TRANSPORT','CANCELLED'} else 'INVALID_RESPONSE'
        ledger.finish(call_id,failure=category)
    exported=ledger.export()
    # Reuse reservation/hash-chain semantics; the ledger's legacy OFFLINE
    # default call counter is deliberately excluded from these human receipts.
    audit.update(ledger_budget=exported['budget'],ledger_charged=exported['charged'],ledger_events=exported['events'])
    return audit,candidate


def summarize(records,contract):
    known=sum(r['reported_model_calls'] or 0 for r in records)
    total=known if all(r['reported_model_calls'] is not None for r in records) else None
    return {'schema':'gir-create-pair-summary@1','protocol_digest':digest(contract),
            'scope':'ONE_DEVELOPMENT_PAIR_RESPONSES_ONLY_NOT_GAMEPLAY_PASS',
            'planned_arms':2,'request_attempts':sum(r['request_attempts'] for r in records),
            'reported_model_calls':total,'reported_model_calls_lower_bound':known,
            'arms':[{'representation':r['representation'],'status':r['status'],'failure':r['failure'],
                     'http_status':r['http_status'],'finish_reason':r['finish_reason'],'usage':r['usage'],
                     'execution_status':'BLOCKED','test_status':'NOTRUN'} for r in records],
            'maximum_output_tokens_per_arm':OUTPUT_TOKENS,'maximum_pair_output_tokens':2*OUTPUT_TOKENS,
            'repairs':0,'tool_calls':0,'automatic_retries':0,'automatic_budget_growth':False,
            'paid_fallback':False,'generated_source_executions':0,'reset_cards_used':0,
            'benchmark_status':'NOTRUN_PENDING_REVIEW_AND_ISOLATION',
            'timestamp_utc':datetime.now(timezone.utc).isoformat()}


def prepare_directory(output,contract):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write_json(output/'protocol.json',contract)
    for rep,request in prepared_requests(contract):
        path=output/rep;path.mkdir()
        write_json(path/'request.json',request.body)
    return output


def run_pair(output,credential):
    if (not isinstance(credential,str) or not 8<=len(credential)<=4096 or
            any(ord(c)<33 or ord(c)>126 for c in credential)):
        raise EvaluationError('INVALID_LOCAL_CREDENTIAL')
    contract=load_freeze();requests=prepared_requests(contract)
    output=prepare_directory(output,contract);records=[];halt=None
    for rep,request in requests:
        path=output/rep
        if halt is None:
            try:load_freeze()
            except EvaluationError:halt='CONTRACT_DRIFT'
        if halt is not None:audit=no_run(rep,request,'PAIR_STOPPED_'+halt);candidate=None
        else:
            # Immutable write-ahead evidence. An interrupted/missing terminal
            # audit remains UNKNOWN, never automatically resumed or retried.
            write_json(path/'reservation.json',{'schema':'gir-create-reservation@1',
                'representation':rep,'request_digest':request.request_digest,
                'maximum_output_tokens':OUTPUT_TOKENS,'attempts_reserved':1,
                'incomplete_outcome':'UNKNOWN_NOT_RETRYABLE','execution_status':'BLOCKED'})
            audit,candidate=attempt(rep,request,credential)
            if candidate is not None:
                encoded=candidate.encode('utf-8')
                with (path/'candidate.txt').open('xb') as handle:handle.write(encoded)
                audit.update(candidate_file='candidate.txt',candidate_sha256=hashlib.sha256(encoded).hexdigest())
            if audit['failure'] in HALT_FAILURES:halt=audit['failure']
        write_json(path/'audit.json',audit);records.append(audit)
    summary=summarize(records,contract);write_json(output/'summary.json',summary)
    return summary


def import_pair(directory):
    """Read only fixed filenames and bounded inert text for digests, never execute."""
    directory=Path(directory)
    def load(path):
        if not path.is_file() or path.stat().st_size>1048576:raise EvaluationError('PAIR_BUNDLE_INVALID')
        return bounded_json(path.read_text(encoding='utf-8'))
    contract=import_contract(load(directory/'protocol.json'))
    records=[]
    for rep,request in prepared_requests(contract):
        path=directory/rep
        if load(path/'request.json')!=request.body:raise EvaluationError('PAIR_REQUEST_TAMPERED')
        audit=load(path/'audit.json')
        if (not isinstance(audit,dict) or set(audit)!=AUDIT_KEYS or audit['schema']!='gir-create-arm-audit@1'
                or audit['representation']!=rep or audit['request_digest']!=request.request_digest
                or audit['execution_status']!='BLOCKED' or audit['test_status']!='NOTRUN'
                or any(type(audit[k])is not int or audit[k]!=0 for k in ('generated_source_executions','repairs','tool_calls'))
                or not isinstance(audit['status'],str) or audit['status'] not in {'NOTRUN','FAIL','CANDIDATE_RECORDED_UNREVIEWED'}
                or audit['failure'] is not None and (not isinstance(audit['failure'],str) or audit['failure'] not in FAILURES)
                or type(audit['request_attempts'])is not int or audit['request_attempts'] not in (0,1)
                or audit['reported_model_calls'] is not None and (type(audit['reported_model_calls'])is not int or audit['reported_model_calls'] not in (0,1))
                or audit['ledger_budget']!=arm_budget().__dict__
                or not isinstance(audit['ledger_events'],list) or any(not isinstance(e,dict) for e in audit['ledger_events'])
                or not verify_chain(audit['ledger_events'])):
            raise EvaluationError('PAIR_AUDIT_INVALID')
        if not isinstance(audit['timestamp_utc'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00',audit['timestamp_utc']):
            raise EvaluationError('PAIR_AUDIT_INVALID')
        if (audit['http_status'] is not None and (type(audit['http_status'])is not int or not 100<=audit['http_status']<=599)
                or audit['finish_reason'] not in (None,'stop','length')
                or audit['model_matches_requested'] is not None and type(audit['model_matches_requested'])is not bool
                or audit['provider_error_code'] is not None and (not isinstance(audit['provider_error_code'],str) or audit['provider_error_code'] not in SAFE_ERROR_CODES)
                or not isinstance(audit['provider_error_diagnostic'],str)
                or audit['provider_error_diagnostic'] not in ERROR_DIAGNOSTICS|{'PREPARED_ONLY'}|{'PAIR_STOPPED_'+x for x in HALT_FAILURES}):
            raise EvaluationError('PAIR_AUDIT_INVALID')
        for field in ('candidate_digest','candidate_sha256','model_identity_digest'):
            value=audit[field]
            if value is not None and (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)):
                raise EvaluationError('PAIR_AUDIT_INVALID')
        if audit['candidate_bytes'] is not None and (type(audit['candidate_bytes'])is not int or not 0<=audit['candidate_bytes']<=RESPONSE_BYTES):
            raise EvaluationError('PAIR_AUDIT_INVALID')
        usage=audit['usage']
        if usage is not None and (not isinstance(usage,dict) or set(usage)!={'prompt_tokens','completion_tokens','total_tokens'}
                or any(type(v)is not int or not 0<=v<=10**9 for v in usage.values())
                or usage['total_tokens']!=usage['prompt_tokens']+usage['completion_tokens']):
            raise EvaluationError('PAIR_AUDIT_INVALID')
        observed=audit['response_observations']
        if observed is not None and (not isinstance(observed,dict) or set(observed)!=OBSERVATION_KEYS
                or type(observed['reasoning_content_present'])is not bool
                or observed['reasoning_content_type'] not in ('absent','null','string','other')
                or observed['reasoning_content_present']!=(observed['reasoning_content_type']!='absent')
                or (observed['reasoning_content_type']=='string')!=(observed['reasoning_content_chars'] is not None)
                or any(v is not None and (type(v)is not int or not 0<=v<=RESPONSE_BYTES)
                       for v in (observed['content_chars'],observed['reasoning_content_chars']))):
            raise EvaluationError('PAIR_AUDIT_INVALID')
        if audit['reported_model_calls']==1:
            if (usage is None or audit['http_status']!=200 or audit['request_attempts']!=1
                    or audit['finish_reason'] not in ('stop','length') or audit['candidate_digest'] is None
                    or audit['model_matches_requested']!=(audit['model_identity_digest']==digest(ACCOUNT_MODEL))):
                raise EvaluationError('PAIR_AUDIT_INVALID')
        elif usage is not None or audit['candidate_file'] is not None:raise EvaluationError('PAIR_AUDIT_INVALID')
        if audit['request_attempts']==0:
            if audit['status']!='NOTRUN' or audit['reported_model_calls']!=0 or audit['ledger_events'] or audit['ledger_charged'] is not None:
                raise EvaluationError('PAIR_AUDIT_INVALID')
        else:
            if audit['reported_model_calls']==0 or len(audit['ledger_events'])!=2:raise EvaluationError('PAIR_AUDIT_INVALID')
            reserved=audit['ledger_events'][0]
            base_keys={'sequence','previous','timestamp_utc','event','hash'}
            if (set(reserved)!=base_keys|{'call_id','request_digest','repair','charges'}
                    or reserved.get('event')!='RESERVED' or reserved.get('call_id')!=1 or reserved.get('repair') is not False
                    or reserved.get('request_digest')!=request.request_digest
                    or reserved.get('charges')!={'prompt_units':request.prompt_units,'output_tokens':OUTPUT_TOKENS,'attempts':1,'repairs':0,'tool_calls':0}):
                raise EvaluationError('PAIR_AUDIT_INVALID')
            finished=audit['ledger_events'][1]
            allowed=base_keys|{'call_id','status','tokens_in','tokens_out'}
            if audit['reported_model_calls']==1:allowed|={'candidate_digest','model_identity_digest','finish_reason'}
            if finished.get('status')=='FAIL':allowed|={'failure'}
            if set(finished)!=allowed or finished.get('event')!='FINISHED' or finished.get('call_id')!=1 or finished.get('status') not in {'FAIL','RESPONSE_IMPORTED'}:
                raise EvaluationError('PAIR_AUDIT_INVALID')
            charged={'prompt_units':request.prompt_units,'output_tokens':OUTPUT_TOKENS,'attempts':1,'repairs':0,'tool_calls':0}
            if usage is not None:
                exceeded=usage['prompt_tokens']>request.prompt_units or usage['completion_tokens']>OUTPUT_TOKENS
                if exceeded:
                    charged['prompt_units']=max(request.prompt_units,usage['prompt_tokens'])
                    charged['output_tokens']=max(OUTPUT_TOKENS,usage['completion_tokens'])
                elif audit['model_matches_requested'] is True:
                    charged['prompt_units']=usage['prompt_tokens'];charged['output_tokens']=usage['completion_tokens']
            if audit['ledger_charged']!=charged:raise EvaluationError('PAIR_AUDIT_INVALID')
        if audit['candidate_file'] is not None:
            if audit['candidate_file']!='candidate.txt':raise EvaluationError('PAIR_CANDIDATE_PATH_REJECTED')
            file=path/'candidate.txt'
            if not file.is_file() or file.stat().st_size>SOURCE_BYTES:raise EvaluationError('PAIR_CANDIDATE_SIZE')
            encoded=file.read_bytes();text=encoded.decode('utf-8',errors='strict')
            if len(encoded)!=audit['candidate_bytes'] or hashlib.sha256(encoded).hexdigest()!=audit['candidate_sha256'] or digest(text)!=audit['candidate_digest']:
                raise EvaluationError('PAIR_CANDIDATE_TAMPERED')
            if observed is None or observed['content_chars']!=len(text):raise EvaluationError('PAIR_AUDIT_INVALID')
        elif audit['candidate_sha256'] is not None or (path/'candidate.txt').exists():raise EvaluationError('PAIR_CANDIDATE_TAMPERED')
        if audit['status']=='CANDIDATE_RECORDED_UNREVIEWED':
            if (audit['failure'] is not None or audit['http_status']!=200 or audit['finish_reason']!='stop'
                    or audit['model_matches_requested'] is not True or audit['candidate_file'] is None):
                raise EvaluationError('PAIR_AUDIT_INVALID')
        if contract['schema']=='gir-create-pair-freeze@2' and observed is not None:
            mismatch=(observed['reasoning_content_type']=='other' or
                      (observed['reasoning_content_chars'] or 0)>0)
            if (mismatch and audit['status']=='CANDIDATE_RECORDED_UNREVIEWED' or
                    audit['failure']=='THINKING_MISMATCH' and (not mismatch or audit['candidate_file'] is not None)):
                raise EvaluationError('PAIR_AUDIT_INVALID')
        elif audit['failure']=='THINKING_MISMATCH':raise EvaluationError('PAIR_AUDIT_INVALID')
        if audit['status']=='FAIL' and audit['failure'] is None:raise EvaluationError('PAIR_AUDIT_INVALID')
        if audit['failure']=='TRUNCATED' and audit['finish_reason']!='length':raise EvaluationError('PAIR_AUDIT_INVALID')
        records.append(audit)
    saved=load(directory/'summary.json');summary=summarize(records,contract)
    timestamp=saved.get('timestamp_utc')
    if not isinstance(timestamp,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00',timestamp):
        raise EvaluationError('PAIR_SUMMARY_TAMPERED')
    summary['timestamp_utc']=timestamp
    if saved!=summary:raise EvaluationError('PAIR_SUMMARY_TAMPERED')
    return {'kind':'USER_REPORTED_CREATE_PAIR_NOT_PROVIDER_ATTESTATION','summary':summary,
            'importer_authenticated_requests':0,'candidate_execution':'BLOCKED','gameplay_tests':'NOTRUN'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--user-local-send-pair',action='store_true')
    modes.add_argument('--import-pair',type=Path)
    parser.add_argument('--plan-status-confirmed',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve evidence; choose a fresh directory')
    try:
        if args.import_pair:
            report=import_pair(args.import_pair);args.output.mkdir(parents=True,exist_ok=False)
            write_json(args.output/'import.json',report);print(json.dumps(report,ensure_ascii=False));return 0
        contract=load_freeze()
        if not args.user_local_send_pair:
            output=prepare_directory(args.output,contract)
            records=[no_run(rep,request,'PREPARED_ONLY') for rep,request in prepared_requests(contract)]
            for audit in records:write_json(output/audit['representation']/'audit.json',audit)
            summary=summarize(records,contract);write_json(output/'summary.json',summary)
            print(json.dumps({'status':'PREPARED_NOT_SENT','protocol_digest':digest(contract),
                              'requests':2,'max_output_tokens_per_arm':OUTPUT_TOKENS,'authenticated_requests':0,
                              'candidate_execution':'BLOCKED'},ensure_ascii=False));return 0
        if not args.plan_status_confirmed:raise EvaluationError('USER_PLAN_STATUS_REQUIRED')
        if not sys.stdin.isatty() or not sys.stdout.isatty():raise EvaluationError('PRIVATE_INTERACTIVE_TERMINAL_REQUIRED')
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            credential=getpass.getpass('Plan 专用密钥（隐藏输入，授权此固定两请求小试）: ')
        try:summary=run_pair(args.output,credential)
        finally:credential=None
        print(json.dumps(summary,ensure_ascii=False))
        return int(any(a['status']!='CANDIDATE_RECORDED_UNREVIEWED' for a in summary['arms']))
    except (EvaluationError,getpass.GetPassWarning) as error:
        print(json.dumps({'status':'NOTRUN','code':str(error) if isinstance(error,EvaluationError) else 'HIDDEN_INPUT_UNAVAILABLE'}));return 2
    except (KeyboardInterrupt,EOFError):print(json.dumps({'status':'CANCELLED','code':'PRIVATE_INPUT_CANCELLED'}));return 2
    except (OSError,UnicodeError,ValueError,TypeError,KeyError):print(json.dumps({'status':'FAIL','code':'LOCAL_IO_OR_BUNDLE_ERROR_NO_RETRY'}));return 2


if __name__=='__main__':raise SystemExit(main())
