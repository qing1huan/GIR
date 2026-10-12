"""Frozen collection-only entry. Human private CMD confirmation precedes key read.

Agents must never invoke --user-local-send-pilot24 or read a real key file.
No candidate parser, compiler, worker, or evaluator is called here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys

from headless_v01.compiler import digest
from .adapter import ACCOUNT_MODEL, EvaluationError, bounded_json, import_response
from .local_key_file import read_local_key
from .manual_probe import _https_once, RESPONSE_BYTES, contains_credential_echo
from .pilot24_materials import ROOT as MATERIAL_ROOT, preview, request, collection_policy

ROOT = MATERIAL_ROOT.parents[1]
MATERIAL_DIGEST = '5db1fbc7855755e0cae1c033294bee8a46c43192c669419da781350d4e2aefa1'
APPROVAL_ID = 'Sentinel_fd07ce9accfc8191af13a367c14ce6ff'
CONFIRMATION = 'SEND_PILOT24'
ENTRY_FREEZE = ROOT/'ark_eval/pilot24_send_entry.freeze.json'
REVIEW = ROOT/'ark_eval/pilot24_send_review.json'
CLAIM_ROOT = ROOT/'results/pilot24-send-claims'
DEFAULT_OUTPUT = ROOT/'results/pilot24-user-local-001'
ENTRY_FILES = ('ark_eval/manual_pilot24.py', 'ark_eval/tests/test_manual_pilot24.py',
               'docs/pilot24/USER_LOCAL.md')
SAFE_LOCAL_CODES = frozenset({
    'PILOT24_MATERIAL_SEAL_DRIFT','PILOT24_FROZEN_DEPENDENCY_DRIFT','PILOT24_VALIDATION_DRIFT',
    'PILOT24_ENTRY_DRIFT','PILOT24_FOCUSED_SEND_REVIEW_REQUIRED','PILOT24_FROZEN_REQUEST_DRIFT',
    'PILOT24_SLOT_DRIFT','PILOT24_OUTPUT_PATH_REJECTED','PILOT24_APPROVAL_ALREADY_CONSUMED_NO_RETRY',
    'PILOT24_FRESH_OUTPUT_REQUIRED_NO_RESUME','KEY_FILE_REQUIRES_USER_LOCAL_SEND',
    'USER_PLAN_STATUS_REQUIRED','EXPLICIT_USER_KEY_FILE_REQUIRED','PRIVATE_INTERACTIVE_CMD_REQUIRED',
    'USER_SEND_CANCELLED','INVALID_LOCAL_CREDENTIAL','KEY_FILE_UNSAFE_LOCATION',
    'KEY_FILE_GIT_LOCATION_REJECTED','KEY_FILE_INVALID_FORMAT','KEY_FILE_NOT_FOUND',
    'KEY_FILE_UNREADABLE_OR_INVALID'})


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return bounded_json(Path(path).read_text(encoding='utf-8'))


def write_new(path, data):
    # Exclusive, flushed before the next request. Old evidence is never replaced.
    with Path(path).open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
        handle.flush()
        os.fsync(handle.fileno())


def entry_manifest():
    return {'schema':'gir-pilot24-collection-entry@1', 'material_digest':MATERIAL_DIGEST,
            'material_freeze_sha256':sha(MATERIAL_ROOT/'freeze.json'),
            'approval_id':APPROVAL_ID, 'confirmation':CONFIRMATION,
            'artifact_sha256':{n:sha(ROOT/n) for n in ENTRY_FILES},
            'agent_send_authorized':False, 'candidate_execution_authorized':False,
            'maximum_requests':24, 'maximum_output_tokens':98304,
            'claim_key':'material_digest_and_approval_id', 'automatic_resume':False}


def scope_digest():
    # Same approval cannot become another batch by changing output or entry code.
    return digest({'material_digest':MATERIAL_DIGEST, 'approval_id':APPROVAL_ID})


def load_requests(require_review=False):
    seal=read(MATERIAL_ROOT/'freeze.json');payload=dict(seal)
    stored=payload.pop('freeze_digest', None)
    if stored!=MATERIAL_DIGEST or digest(payload)!=MATERIAL_DIGEST:
        raise EvaluationError('PILOT24_MATERIAL_SEAL_DRIFT')
    for key in ('bindings', 'runtime_bindings', 'validation_evidence_sha256'):
        if any(sha(ROOT/n)!=h for n,h in seal[key].items()):
            raise EvaluationError('PILOT24_FROZEN_DEPENDENCY_DRIFT')
    if sha(ROOT/seal['trusted_validation_path'])!=seal['trusted_validation_sha256']:
        raise EvaluationError('PILOT24_VALIDATION_DRIFT')
    entry=entry_manifest()
    if read(ENTRY_FREEZE)!=entry:
        raise EvaluationError('PILOT24_ENTRY_DRIFT')
    if require_review:
        if read(REVIEW)!={'schema':'gir-pilot24-send-review@1', 'decision':'PASS',
                          'entry_digest':digest(entry), 'material_digest':MATERIAL_DIGEST,
                          'scope_digest':scope_digest(), 'candidate_execution_authorized':False}:
            raise EvaluationError('PILOT24_FOCUSED_SEND_REVIEW_REQUIRED')
    protocol=read(MATERIAL_ROOT/'protocol.draft.json')
    if protocol!=preview():raise EvaluationError('PILOT24_FROZEN_REQUEST_DRIFT')
    sequence=[(s,request(s['task_id'],s['representation'])) for s in protocol['slots']]
    if len(sequence)!=24 or len({s['slot_id'] for s,_ in sequence})!=24:
        raise EvaluationError('PILOT24_SLOT_DRIFT')
    return protocol, sequence, entry


def notrun(slot, req, reason):
    return {'schema':'gir-pilot24-slot-audit@1', 'slot':slot,
            'request_digest':req.request_digest, 'generation_status':'NOTRUN',
            'notrun_reason':reason, 'request_attempts':0, 'model_calls_lower':0,
            'model_calls_upper':0, 'usage':None, 'http_status':None,
            'continue_fixed_schedule':False, 'candidate_file':None,
            'execution_status':'BLOCKED', 'case_statuses':['NOTRUN']*12,
            'contract_status':'NOT_CHECKED', 'generated_source_executions':0,
            'retry':False, 'repairs':0, 'tool_calls':0,
            'input_units_are_tokens':False, 'input_token_upper_bound_claimed':False,
            'prompt_units_estimate':req.prompt_units, 'max_output_tokens':4096}


def reserved(slot, req):
    row=notrun(slot,req,'REQUEST_OUTCOME_NOT_YET_KNOWN')
    row.update(generation_status='OUTCOME_UNKNOWN', request_attempts=1,
               model_calls_upper=1, reservation_retained=True)
    return row


def attempt(slot, req, credential):
    row=reserved(slot,req);candidate=None
    try:
        status, body=_https_once(req,credential,timeout=120)
        row['http_status']=status
        if len(body)>RESPONSE_BYTES:raise EvaluationError('RESPONSE_SIZE')
        if credential.encode('ascii') in body:raise EvaluationError('SECRET_ECHO')
        # Provider text/headers/error bodies are not persisted or printed.
        if status!=200:
            row['generation_status']='ERROR_PROVIDER'
            return row,None
        raw=body.decode('utf-8',errors='strict');data=bounded_json(raw)
        if contains_credential_echo(data,credential):raise EvaluationError('SECRET_ECHO')
        policy=collection_policy(status,raw)
        row.update(policy)
        row['kind']='USER_LOCAL_COLLECTION_ONLY'
        if policy['generation_status']=='INVALID_PROVIDER_PROTOCOL':return row,None
        imported=import_response(raw,requested_model=ACCOUNT_MODEL)
        row.update(model_calls_lower=1,model_calls_upper=1,
                   usage={'prompt_tokens':imported.tokens_in,'completion_tokens':imported.tokens_out,
                          'total_tokens':imported.tokens_in+imported.tokens_out},
                   finish_reason=imported.finish_reason,
                   model_identity_digest=imported.model_identity_digest,
                   candidate_bytes=len(imported.candidate.encode('utf-8')))
        if 0<len(imported.candidate.encode('utf-8'))<=16384:
            candidate=imported.candidate
    except (KeyboardInterrupt, EOFError):
        row.update(generation_status='OUTCOME_UNKNOWN',continue_fixed_schedule=False,
                   stop_code='CANCELLED_UNKNOWN_NO_RETRY',reservation_retained=True)
        return row,None
    except (OSError, http.client.HTTPException):
        row.update(generation_status='OUTCOME_UNKNOWN',continue_fixed_schedule=False,
                   stop_code='TRANSPORT_UNKNOWN_NO_RETRY',reservation_retained=True)
        return row,None
    except (EvaluationError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        row.update(generation_status='INVALID_PROVIDER_PROTOCOL',continue_fixed_schedule=False,
                   stop_code='UNTRUSTED_RESPONSE_NO_RETRY',reservation_retained=True)
        return row,None
    except Exception:
        row.update(generation_status='INVALID_PROVIDER_PROTOCOL',continue_fixed_schedule=False,
                   stop_code='LOCAL_RESPONSE_PROCESSING_NO_RETRY',reservation_retained=True)
        return row,None
    return row,candidate


def summary(rows, state):
    return {'schema':'gir-pilot24-collection-summary@1','state':state,
            'material_digest':MATERIAL_DIGEST,'scope_digest':scope_digest(),
            'slots':rows, 'request_attempts_reserved':sum(r['request_attempts'] for r in rows),
            'model_calls_lower':sum(r['model_calls_lower'] for r in rows),
            'model_calls_upper':sum(r['model_calls_upper'] for r in rows),
            'known_usage':{k:sum((r['usage'] or {}).get(k,0) for r in rows)
                           for k in ('prompt_tokens','completion_tokens','total_tokens')},
            'maximum_requests':24,'maximum_output_tokens':98304,'automatic_retries':0,
            'repairs':0,'tool_calls':0,'paid_fallback':False,'reset_cards_used':0,
            'generated_source_executions':0,'candidate_execution':'BLOCKED',
            'primary_case_definitions':72,'planned_primary_case_evaluations':288,
            'case_counts':{'PASS':0,'FAIL':0,'NOTRUN':288},'contract_status':'NOT_CHECKED',
            'input_units_are_tokens':False,'input_token_upper_bound_claimed':False}


def prepare_directory(output, protocol, sequence, entry):
    output=Path(output).absolute()
    # Fixed project results only; check resolved target before creating anything.
    results=(ROOT/'results').resolve()
    if output.is_symlink() or not output.resolve().is_relative_to(results) or output.resolve()==results:
        raise EvaluationError('PILOT24_OUTPUT_PATH_REJECTED')
    output.mkdir(parents=True,exist_ok=False)
    write_new(output/'protocol.json',protocol);write_new(output/'entry.json',entry)
    rows=[]
    for slot,req in sequence:
        folder=output/slot['slot_id'];folder.mkdir()
        write_new(folder/'request.json',req.body)
        row=notrun(slot,req,'BATCH_NOT_STARTED');rows.append(row)
        write_new(folder/'notrun-initial.json',row)
    write_new(output/'prepared-summary.json',summary(rows,'PREPARED_NOT_SENT'))
    return output,rows


def prepare_only(output):
    protocol,sequence,entry=load_requests()
    _,rows=prepare_directory(output,protocol,sequence,entry)
    return summary(rows,'PREPARED_NOT_SENT')


def run_batch(output, credential):
    """Human-only after confirmation; tests use mock transport/public fixtures."""
    if (not isinstance(credential,str) or not 8<=len(credential)<=4096
            or any(ord(c)<33 or ord(c)>126 for c in credential)):
        raise EvaluationError('INVALID_LOCAL_CREDENTIAL')
    protocol,sequence,entry=load_requests(require_review=True)
    # Atomic fixed scope claim BEFORE any transport. Never removed, even on failure.
    CLAIM_ROOT.mkdir(parents=True,exist_ok=True)
    claim=CLAIM_ROOT/scope_digest()
    try:claim.mkdir(exist_ok=False)
    except FileExistsError:raise EvaluationError('PILOT24_APPROVAL_ALREADY_CONSUMED_NO_RETRY') from None
    write_new(claim/'start.json',{'scope_digest':scope_digest(),'entry_digest':digest(entry),
              'output':str(Path(output).absolute()),'started_utc':datetime.now(timezone.utc).isoformat(),
              'maximum_requests':24,'reservation_retained':True,'resume_allowed':False,
              'initial_model_calls_lower':0,'initial_model_calls_upper':0,
              'initial_slots':'ALL_24_NOTRUN; CHECK_OUTPUT_RESERVATIONS_AND_CHECKPOINTS_FOR_LATER_ATTEMPTS'})
    output,rows=prepare_directory(output,protocol,sequence,entry)
    halt=None
    for index,(slot,req) in enumerate(sequence):
        folder=output/slot['slot_id']
        if halt is None:
            try:
                if load_requests(require_review=True)!=(protocol,sequence,entry):
                    raise EvaluationError('PILOT24_BATCH_DRIFT')
                if read(folder/'request.json')!=req.body:raise EvaluationError('PILOT24_REQUEST_FILE_DRIFT')
            except Exception:halt='FROZEN_BINDING_DRIFT'
        if halt is not None:row=notrun(slot,req,'BATCH_STOPPED_'+halt)
        else:
            rows[index]=reserved(slot,req)
            write_new(folder/'reservation.json',rows[index])
            # Append-only checkpoint survives abrupt termination; all remaining slots NOTRUN.
            write_new(output/f'checkpoint-{index+1:02}.json',summary(rows,'REQUEST_OUTCOME_UNKNOWN'))
            row,candidate=attempt(slot,req,credential)
            # Append-only validated outcome before source/audit persistence. A crash
            # here keeps the earlier 0..1 checkpoint; later crashes retain known usage.
            write_new(folder/'response-receipt.json',
                      {'collection_phase':'RESPONSE_RECEIVED_PERSISTENCE_PENDING','audit':row})
            if candidate is not None:
                raw=candidate.encode('utf-8')
                with (folder/'candidate.txt').open('xb') as handle:
                    handle.write(raw);handle.flush()
                    os.fsync(handle.fileno())
                row.update(candidate_file='candidate.txt',candidate_sha256=hashlib.sha256(raw).hexdigest())
            if not row['continue_fixed_schedule']:halt=row['generation_status']
        rows[index]=row;write_new(folder/'audit.json',row)
    state='STOPPED_NO_RETRY' if halt else 'FIXED_SCHEDULE_COLLECTED_UNREVIEWED'
    report=summary(rows,state);write_new(output/'summary.json',report)
    write_new(claim/'completion.json',{'state':state,'summary_sha256':sha(output/'summary.json'),
                                     'request_attempts_reserved':report['request_attempts_reserved']})
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-local-send-pilot24',action='store_true')
    parser.add_argument('--plan-status-confirmed',action='store_true')
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--key-file',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.output.exists():raise EvaluationError('PILOT24_FRESH_OUTPUT_REQUIRED_NO_RESUME')
        if not args.user_local_send_pilot24:
            if args.key_file is not None:raise EvaluationError('KEY_FILE_REQUIRES_USER_LOCAL_SEND')
            report=prepare_only(args.output)
        else:
            load_requests(require_review=True)
            if (CLAIM_ROOT/scope_digest()).exists():raise EvaluationError('PILOT24_APPROVAL_ALREADY_CONSUMED_NO_RETRY')
            if not args.plan_status_confirmed:raise EvaluationError('USER_PLAN_STATUS_REQUIRED')
            if args.key_file is None:raise EvaluationError('EXPLICIT_USER_KEY_FILE_REQUIRED')
            if not sys.stdin.isatty() or not sys.stdout.isatty():raise EvaluationError('PRIVATE_INTERACTIVE_CMD_REQUIRED')
            print('Existing Plan only; six tasks x two representations x two fresh sessions = 24 fixed slots.')
            print('4096 output tokens/request; total cap98304; no repairs/tools/retries/resume/paid fallback.')
            print('Input24000 byte+overhead units are NOT verified tokens. Candidates saved inert; 288 cases NOTRUN.')
            print('Model: '+ACCOUNT_MODEL+'; material: '+MATERIAL_DIGEST+'; output: '+str(args.output))
            if input('Type '+CONFIRMATION+' to send this exact batch: ').strip()!=CONFIRMATION:
                raise EvaluationError('USER_SEND_CANCELLED')
            credential=None
            try:
                credential=read_local_key(args.key_file,project_root=ROOT)
                report=run_batch(args.output,credential)
            finally:credential=None
        print(json.dumps({k:report[k] for k in ('state','request_attempts_reserved','model_calls_lower',
                         'model_calls_upper','generated_source_executions')},ensure_ascii=False))
        return int(report['state']=='STOPPED_NO_RETRY')
    except EvaluationError as exc:
        code=str(exc)
        print(json.dumps({'state':'NOTRUN','code':code if code in SAFE_LOCAL_CODES
                          else 'LOCAL_PROTOCOL_OR_IO_ERROR_NO_RETRY'}));return 2
    except (KeyboardInterrupt,EOFError):
        print(json.dumps({'state':'CANCELLED','code':'PRIVATE_INPUT_CANCELLED'}));return 2
    except Exception:
        print(json.dumps({'state':'BLOCKED','code':'LOCAL_ERROR_KEEP_CLAIM_CHECKPOINTS_NO_RETRY'}));return 2


if __name__=='__main__':raise SystemExit(main())
