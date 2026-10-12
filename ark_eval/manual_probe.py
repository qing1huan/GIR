"""User-local, single Plan connectivity probe. Default preparation is offline.

Codex must not run --user-local-send or supply a credential. Only the human uses
this entry in a private local terminal. No config/env/key files are read.
"""
import argparse
from datetime import datetime, timezone
import getpass
import http.client
import json
from pathlib import Path
import re
import ssl
import sys

from headless_v01.compiler import digest
from .adapter import (ACCOUNT_MODEL, EvaluationError, MAX_BYTES, PLAN_URL,
                      PlanConfig, bounded_json, import_response, prepare)

HOST = 'ark.cn-beijing.volces.com'
PATH = '/api/plan/v3/chat/completions'
MODEL = ACCOUNT_MODEL
RESPONSE_BYTES = 65536
OUTPUT_TOKENS = 128
MAX_OUTPUT_TOKENS = 256
LEGACY_OUTPUT_TOKENS = 16
AUDIT_KEYS = {'schema','mode','endpoint','requested_model','request_digest','prompt_units',
              'max_output_tokens','attempts','retry_count','paid_fallback','redirect_followed',
              'http_status','status','failure','reported_real_model_calls','usage','finish_reason',
              'response_digest','candidate_digest','model_identity_digest','model_matches_requested',
              'generated_source_executions','reset_cards_used','timestamp_utc'}
AUDIT_KEYS_V2 = AUDIT_KEYS | {'provider_error_code','provider_error_diagnostic'}
AUDIT_KEYS_V3 = AUDIT_KEYS_V2 | {'response_observations'}
OBSERVATION_KEYS = {'content_chars','reasoning_content_present','reasoning_content_type','reasoning_content_chars'}
SAFE_ERROR_CODES = frozenset({'AuthenticationError','Unauthorized','invalid_api_key','InvalidApiKey',
                             'InvalidAPIKey','Forbidden','PermissionDenied','AccessDenied','QuotaExceeded',
                             'InsufficientQuota','insufficient_quota','InvalidParameter','InvalidParameterValue',
                             'ModelNotFound','EndpointNotFound'})
ERROR_DIAGNOSTICS = frozenset({'NOT_APPLICABLE','NOT_CAPTURED_IN_V1','CODE_ALLOWLISTED',
                               'OTHER_OR_UNAVAILABLE','INVALID_ERROR_ENVELOPE','ERROR_BODY_TOO_LARGE',
                               'SECRET_ECHO_DROPPED','ERROR_BODY_UNAVAILABLE'})
FAILURES = {None,'AUTH','FORBIDDEN','QUOTA_OR_RATE_LIMIT','REDIRECT_REJECTED','HTTP_ERROR','TRANSPORT',
            'RESPONSE_TOO_LARGE','INVALID_RESPONSE','SECRET_ECHO_REJECTED','MODEL_MISMATCH',
            'USAGE_EXCEEDS_RESERVATION','UNEXPECTED_CONNECTIVITY_RESPONSE'}
FAILURES |= {'OUTPUT_TOKEN_LIMIT','EMPTY_CONNECTIVITY_CONTENT','TRUNCATED'}


def probe_request(max_output_tokens=OUTPUT_TOKENS):
    if type(max_output_tokens)is not int or not 1<=max_output_tokens<=MAX_OUTPUT_TOKENS:
        raise EvaluationError('INVALID_OUTPUT_TOKEN_CAP')
    return prepare([{'role':'user','content':'Connectivity check only. Reply with OK. Do not produce code or use tools.'}],
                   max_output_tokens, PlanConfig(model=MODEL))


def response_observations(data):
    """Closed non-text metadata; characters are not provider token counts."""
    if not isinstance(data,dict):return None
    choices=data.get('choices')
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):return None
    message=choices[0].get('message')
    if not isinstance(message,dict):return None
    reasoning=message.get('reasoning_content')
    kind=('absent' if 'reasoning_content' not in message else 'null' if reasoning is None
          else 'string' if isinstance(reasoning,str) else 'other')
    content=message.get('content')
    return {'content_chars':len(content) if isinstance(content,str) else None,
            'reasoning_content_present':'reasoning_content' in message,
            'reasoning_content_type':kind,
            'reasoning_content_chars':len(reasoning) if isinstance(reasoning,str) else None}


def outcome_axes(http_status,status,failure):
    if http_status==200: transport='HTTP200'
    elif http_status==401:transport='AUTH_REJECTED'
    elif http_status==403:transport='FORBIDDEN'
    elif http_status is not None:transport='HTTP_ERROR'
    else:transport='TRANSPORT_ERROR' if failure=='TRANSPORT' else 'NOT_OBSERVED'
    if http_status!=200:application='NOTRUN'
    elif status=='RESPONSE_IMPORTED':application='EXPECTED_REPLY'
    elif failure in {'TRUNCATED','OUTPUT_TOKEN_LIMIT'}:application='TRUNCATED'
    elif failure=='EMPTY_CONNECTIVITY_CONTENT':application='EMPTY_RESPONSE'
    elif failure=='UNEXPECTED_CONNECTIVITY_RESPONSE':application='UNEXPECTED_REPLY'
    else:application=failure or 'INVALID_RESPONSE'
    return {'transport_status':transport,'application_status':application}


def _https_once(request, credential, timeout=30):
    # http.client ignores proxy environment settings and does not follow redirects.
    # Fixed host/path and normal TLS verification; no SDK retries or paid route.
    if type(timeout)is not int or not 1<=timeout<=120:raise EvaluationError('INVALID_TRANSPORT_TIMEOUT')
    connection = http.client.HTTPSConnection(HOST,443,timeout=timeout,context=ssl.create_default_context())
    connection.set_debuglevel(0)
    try:
        connection.request('POST',PATH,body=request.body_json.encode('utf-8'),
                           headers={'Authorization':'Bearer '+credential,'Content-Type':'application/json'})
        response = connection.getresponse()
        if 300 <= response.status < 400: return response.status, b''
        if response.status != 200:
            try: body=response.read(RESPONSE_BYTES+1)
            except (OSError,http.client.HTTPException): body=b''
            # Keep the observed HTTP status even if reading its error body fails.
            return response.status,body
        return response.status, response.read(RESPONSE_BYTES+1)
    finally: connection.close()


def safe_error_diagnostic(body, credential):
    """Export a fixed allowlisted token only; never messages, headers or raw JSON."""
    if len(body)>RESPONSE_BYTES:return None,'ERROR_BODY_TOO_LARGE'
    if not body:return None,'ERROR_BODY_UNAVAILABLE'
    if credential.encode('ascii') in body:return None,'SECRET_ECHO_DROPPED'
    try:
        data=bounded_json(body.decode('utf-8',errors='strict'))
        # Also catch JSON-escaped echoes before selecting any diagnostic field.
        if contains_credential_echo(data,credential):return None,'SECRET_ECHO_DROPPED'
        error=data.get('error') if isinstance(data,dict) else None
        if not isinstance(error,dict):return None,'INVALID_ERROR_ENVELOPE'
        code=error.get('code')
        if isinstance(code,str) and code in SAFE_ERROR_CODES:return code,'CODE_ALLOWLISTED'
        return None,'OTHER_OR_UNAVAILABLE'
    except (UnicodeError,EvaluationError,ValueError,TypeError,RecursionError):
        return None,'INVALID_ERROR_ENVELOPE'


def contains_credential_echo(data,credential):
    """Check decoded JSON strings/keys, including quoted/backslash credentials."""
    pending=[data]
    while pending:
        value=pending.pop()
        if isinstance(value,str):
            if credential in value:return True
        elif isinstance(value,dict):
            pending.extend(value.keys());pending.extend(value.values())
        elif isinstance(value,list):pending.extend(value)
    return False


def send_once(credential,max_output_tokens=OUTPUT_TOKENS):
    """Exactly one attempt; tested only with a fixed non-secret mock credential."""
    if (not isinstance(credential,str) or not 8 <= len(credential) <= 4096 or
            any(ord(c) < 33 or ord(c) > 126 for c in credential)):
        raise EvaluationError('INVALID_LOCAL_CREDENTIAL')
    request = probe_request(max_output_tokens)
    audit = {'schema':'gir-user-local-probe@3','mode':'USER_LOCAL_SINGLE_CONNECTIVITY_NOT_BENCHMARK',
             'endpoint':PLAN_URL+ '/chat/completions','requested_model':MODEL,
             'request_digest':request.request_digest,'prompt_units':request.prompt_units,
             'max_output_tokens':max_output_tokens,'attempts':1,'retry_count':0,'paid_fallback':False,
             'redirect_followed':False,'http_status':None,'status':'FAIL','failure':None,
             'reported_real_model_calls':None,'usage':None,'finish_reason':None,'response_digest':None,
             'candidate_digest':None,'model_identity_digest':None,'model_matches_requested':None,
             'provider_error_code':None,'provider_error_diagnostic':'NOT_APPLICABLE',
             'response_observations':None,
             'generated_source_executions':0,'reset_cards_used':0,
             'timestamp_utc':datetime.now(timezone.utc).isoformat()}
    normalized = None
    try:
        status, body = _https_once(request,credential)
        audit['http_status'] = status
        if status!=200:
            audit['provider_error_code'],audit['provider_error_diagnostic']=safe_error_diagnostic(body,credential)
        if status == 401: audit['failure']='AUTH'
        elif status == 403: audit['failure']='FORBIDDEN'
        elif status == 429: audit['failure']='QUOTA_OR_RATE_LIMIT'
        elif 300 <= status < 400: audit['failure']='REDIRECT_REJECTED'
        elif status != 200: audit['failure']='HTTP_ERROR'
        elif len(body) > RESPONSE_BYTES: audit['failure']='RESPONSE_TOO_LARGE'
        elif credential.encode('ascii') in body: audit['failure']='SECRET_ECHO_REJECTED'
        else:
            raw = body.decode('utf-8',errors='strict')
            data=bounded_json(raw)
            # Detect JSON-escaped credential echoes in any field, including reasoning.
            if contains_credential_echo(data,credential):
                audit['failure']='SECRET_ECHO_REJECTED'
                return audit,None
            audit['response_observations']=response_observations(data)
            imported = import_response(raw, requested_model=MODEL)
            if credential in imported.candidate:
                audit['failure']='SECRET_ECHO_REJECTED'
                return audit, None
            audit.update(candidate_digest=imported.candidate_digest,
                         reported_real_model_calls=1,
                         model_identity_digest=imported.model_identity_digest,
                         model_matches_requested=imported.model_matches_requested,
                         usage={'prompt_tokens':imported.tokens_in,'completion_tokens':imported.tokens_out,
                                'total_tokens':imported.tokens_in+imported.tokens_out},
                         finish_reason=imported.finish_reason)
            if not imported.model_matches_requested: audit['failure']='MODEL_MISMATCH'
            elif imported.tokens_out > max_output_tokens or imported.tokens_in > request.prompt_units:
                audit['failure']='USAGE_EXCEEDS_RESERVATION'
            elif imported.finish_reason=='length': audit['failure']='TRUNCATED'
            elif not imported.candidate: audit['failure']='EMPTY_CONNECTIVITY_CONTENT'
            elif len(imported.candidate)>64 or imported.candidate.strip()!='OK':
                audit['failure']='UNEXPECTED_CONNECTIVITY_RESPONSE'
            else:
                # A closed projection, not a raw HTTP/body/header dump. Candidate
                # is untrusted inert text; never printed to the terminal or executed.
                normalized={'model':MODEL,'choices':[{'message':{'role':'assistant','content':imported.candidate},
                                                      'finish_reason':imported.finish_reason}], 'usage':audit['usage']}
                audit.update(status='RESPONSE_IMPORTED',response_digest=digest(normalized))
    except (UnicodeError,EvaluationError,ValueError,TypeError,KeyError): audit['failure']='INVALID_RESPONSE'
    except (OSError,http.client.HTTPException): audit['failure']='TRANSPORT'
    return audit, normalized


def save_bundle(output, audit, response):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=False)
    (output/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if response is not None:
        (output/'response.json').write_text(json.dumps(response,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def import_bundle(directory):
    directory = Path(directory)
    def load(name):
        path=directory/name
        if not path.is_file() or path.stat().st_size > MAX_BYTES: raise EvaluationError('INVALID_LOCAL_BUNDLE')
        return bounded_json(path.read_text(encoding='utf-8'))
    audit = load('audit.json')
    if not isinstance(audit,dict):raise EvaluationError('INVALID_LOCAL_BUNDLE')
    schema_ok=((audit.get('schema')=='gir-user-local-probe@1' and set(audit)==AUDIT_KEYS) or
               (audit.get('schema')=='gir-user-local-probe@2' and set(audit)==AUDIT_KEYS_V2) or
               (audit.get('schema')=='gir-user-local-probe@3' and set(audit)==AUDIT_KEYS_V3))
    if not schema_ok:raise EvaluationError('INVALID_LOCAL_BUNDLE')
    cap=audit['max_output_tokens']
    if type(cap)is not int or not 1<=cap<=MAX_OUTPUT_TOKENS or audit['schema']!='gir-user-local-probe@3' and cap!=LEGACY_OUTPUT_TOKENS:
        raise EvaluationError('INVALID_LOCAL_BUNDLE')
    request=probe_request(cap)
    if (audit['mode']!='USER_LOCAL_SINGLE_CONNECTIVITY_NOT_BENCHMARK'
            or audit['endpoint']!=PLAN_URL+'/chat/completions' or audit['requested_model']!=MODEL
            or type(audit['attempts']) is not int or audit['attempts']!=1
            or audit['retry_count']!=0 or audit['paid_fallback'] is not False or audit['redirect_followed'] is not False
            or audit['generated_source_executions']!=0 or audit['reset_cards_used']!=0
            or not (audit['reported_real_model_calls'] is None or type(audit['reported_real_model_calls']) is int and audit['reported_real_model_calls']==1)
            or audit['failure'] is not None and (not isinstance(audit['failure'],str) or audit['failure'] not in FAILURES)
            or audit['request_digest']!=request.request_digest
            or audit['prompt_units']!=request.prompt_units):
        raise EvaluationError('INVALID_LOCAL_BUNDLE')
    observations=audit.get('response_observations')
    if observations is not None:
        if (not isinstance(observations,dict) or set(observations)!=OBSERVATION_KEYS
                or type(observations['reasoning_content_present'])is not bool
                or observations['reasoning_content_type'] not in ('absent','null','string','other')
                or any(v is not None and (type(v)is not int or not 0<=v<=RESPONSE_BYTES)
                       for v in (observations['content_chars'],observations['reasoning_content_chars']))
                or observations['reasoning_content_present']!=(observations['reasoning_content_type']!='absent')
                or (observations['reasoning_content_type']=='string')!=(observations['reasoning_content_chars'] is not None)
                or audit['http_status']!=200):
            raise EvaluationError('INVALID_LOCAL_BUNDLE')
    provider_code=audit.get('provider_error_code')
    provider_diagnostic=audit.get('provider_error_diagnostic','NOT_CAPTURED_IN_V1')
    if (provider_code is not None and (not isinstance(provider_code,str) or provider_code not in SAFE_ERROR_CODES) or
            not isinstance(provider_diagnostic,str) or provider_diagnostic not in ERROR_DIAGNOSTICS):
        raise EvaluationError('INVALID_LOCAL_BUNDLE')
    for field in ('response_digest','candidate_digest','model_identity_digest'):
        value=audit[field]
        if value is not None and (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)):
            raise EvaluationError('INVALID_LOCAL_BUNDLE')
    if (audit['finish_reason'] not in (None,'stop','length') or
            audit['model_matches_requested'] is not None and type(audit['model_matches_requested']) is not bool or
            audit['http_status'] is not None and (type(audit['http_status']) is not int or not 100 <= audit['http_status'] <= 599)):
        raise EvaluationError('INVALID_LOCAL_BUNDLE')
    if audit['usage'] is not None:
        u=audit['usage']
        if (not isinstance(u,dict) or set(u)!={'prompt_tokens','completion_tokens','total_tokens'} or
                any(type(v) is not int or not 0<=v<=10**9 for v in u.values()) or
                u['total_tokens']!=u['prompt_tokens']+u['completion_tokens']):
            raise EvaluationError('INVALID_LOCAL_BUNDLE')
    if audit['status']=='RESPONSE_IMPORTED':
        response=load('response.json')
        imported=import_response(json.dumps(response,ensure_ascii=False),requested_model=MODEL)
        if (set(response)!={'model','choices','usage'} or audit['failure'] is not None
                or audit['http_status']!=200 or audit['reported_real_model_calls']!=1 or not imported.model_matches_requested
                or digest(response)!=audit['response_digest'] or imported.candidate_digest!=audit['candidate_digest']
                or imported.model_identity_digest!=audit['model_identity_digest']
                or audit['model_matches_requested'] is not True or response['usage']!=audit['usage']
                or imported.finish_reason!=audit['finish_reason'] or imported.tokens_out>cap
                or imported.tokens_in>request.prompt_units or len(imported.candidate)>64 or imported.candidate.strip()!='OK'
                or audit['schema']=='gir-user-local-probe@3' and (imported.finish_reason!='stop' or observations is None
                    or observations['content_chars']!=len(imported.candidate))):
            raise EvaluationError('LOCAL_BUNDLE_TAMPERED')
    elif audit['status']!='FAIL' or audit['failure'] is None or audit['response_digest'] is not None:
        raise EvaluationError('INVALID_LOCAL_BUNDLE')
    return {'kind':'USER_REPORTED_CONNECTIVITY_EVIDENCE_NOT_SERVER_ATTESTATION',
            'status':audit['status'],'failure':audit['failure'],'endpoint':audit['endpoint'],
            'http_status':audit['http_status'],'provider_error_code':provider_code,
            'provider_error_diagnostic':provider_diagnostic,
            'finish_reason':audit['finish_reason'],'max_output_tokens':audit['max_output_tokens'],
            'prompt_units':audit['prompt_units'],'model_matches_requested':audit['model_matches_requested'],
            'response_observations':observations,
            **outcome_axes(audit['http_status'],audit['status'],audit['failure']),
            'requested_model':MODEL,'model_identity_digest':audit['model_identity_digest'],
            'candidate_digest':audit['candidate_digest'],'usage':audit['usage'],
            'reported_request_attempts':1,'reported_real_model_calls':audit['reported_real_model_calls'],'importer_real_model_calls':0,
            'generated_source_executions':0,'reset_cards_used':0,'benchmark_status':'NOTRUN'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--user-local-send',action='store_true')
    modes.add_argument('--import-bundle',type=Path)
    parser.add_argument('--plan-status-confirmed',action='store_true',
                        help='Human has verified this custom probe is permitted and existing Plan/postpaid/model/revocation status')
    parser.add_argument('--output',type=Path,required=True)
    def token_cap(raw):
        try:value=int(raw)
        except ValueError:raise argparse.ArgumentTypeError('Output token cap must be an integer') from None
        if not 1<=value<=MAX_OUTPUT_TOKENS:raise argparse.ArgumentTypeError('Output token cap must be within 1..256')
        return value
    parser.add_argument('--max-output-tokens',type=token_cap,default=OUTPUT_TOKENS,
                        help='Explicit 1..256 output token cap, default 128; never increases automatically')
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve prior evidence; choose a new output directory')
    try:
        if args.import_bundle:
            data=import_bundle(args.import_bundle)
            args.output.mkdir(parents=True)
            (args.output/'import.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(data,ensure_ascii=False));return int(data['status']!='RESPONSE_IMPORTED')
        if not args.user_local_send:
            request=probe_request(args.max_output_tokens);args.output.mkdir(parents=True)
            (args.output/'request.json').write_text(json.dumps(request.body,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'status':'PREPARED_NOT_SENT','endpoint':PLAN_URL+'/chat/completions',
                              'model':MODEL,'max_output_tokens':request.max_output_tokens,
                              'request_digest':request.request_digest,'real_model_calls':0}))
            return 0
        if not args.plan_status_confirmed:raise EvaluationError('USER_PLAN_STATUS_REQUIRED')
        if not sys.stdin.isatty() or not sys.stdout.isatty():raise EvaluationError('PRIVATE_INTERACTIVE_TERMINAL_REQUIRED')
        # Reject getpass fallback: a credential must never be read with visible echo.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            credential=getpass.getpass('Plan 专用密钥（隐藏输入，仅在当前进程内使用）: ')
        try: audit,response=send_once(credential,args.max_output_tokens)
        finally: credential=None  # Lifetime reduced, not a claim of memory zeroization.
        save_bundle(args.output,audit,response)
        print(json.dumps({'status':audit['status'],'failure':audit['failure'],
                          'http_status':audit['http_status'],'provider_error_code':audit['provider_error_code'],
                          **outcome_axes(audit['http_status'],audit['status'],audit['failure']),
                          'finish_reason':audit['finish_reason'],'usage':audit['usage'],
                          'max_output_tokens':audit['max_output_tokens'],
                          'response_observations':audit['response_observations'],
                          'reported_request_attempts':1,'reported_real_model_calls':audit['reported_real_model_calls'],
                          'output':str(args.output)},ensure_ascii=False))
        return int(audit['status']!='RESPONSE_IMPORTED')
    except (EvaluationError,getpass.GetPassWarning) as error:
        print(json.dumps({'status':'NOTRUN','code':str(error) if isinstance(error,EvaluationError) else 'HIDDEN_INPUT_UNAVAILABLE'}))
        return 2
    except (OSError,UnicodeError,ValueError,TypeError,KeyError):
        print(json.dumps({'status':'FAIL','code':'LOCAL_IO_OR_BUNDLE_ERROR'}));return 2
    except (EOFError,KeyboardInterrupt):
        print(json.dumps({'status':'CANCELLED','code':'LOCAL_INPUT_CANCELLED'}));return 2


if __name__=='__main__':raise SystemExit(main())
