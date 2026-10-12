"""Offline diagnosis of closed user-local audits; no credential or network API."""
import argparse
import json
from pathlib import Path

from headless_v01.compiler import digest
from .adapter import EvaluationError
from .manual_probe import MODEL, import_bundle


def diagnose_bundle(directory):
    evidence=import_bundle(directory)
    usage=evidence['usage']
    valid_usage=(isinstance(usage,dict) and set(usage)=={'prompt_tokens','completion_tokens','total_tokens'}
                 and all(type(v)is int and 0<=v<=10**9 for v in usage.values())
                 and usage['total_tokens']==usage['prompt_tokens']+usage['completion_tokens'])
    envelope=(evidence['http_status']==200 and evidence['reported_real_model_calls']==1
              and evidence['model_matches_requested'] is True
              and evidence['model_identity_digest']==digest(MODEL) and valid_usage
              and isinstance(evidence['candidate_digest'],str)
              and evidence['finish_reason'] in {'stop','length'})
    empty=(evidence['candidate_digest']==digest('')) if envelope else None
    length=(evidence['finish_reason']=='length') if envelope else None
    exhausted=(usage['completion_tokens']==evidence['max_output_tokens']) if envelope else None
    within=(usage['completion_tokens']<=evidence['max_output_tokens'] and
            usage['prompt_tokens']<=evidence['prompt_units']) if envelope else None
    exact=('PASS' if envelope and within and evidence['status']=='RESPONSE_IMPORTED'
           and evidence['finish_reason']=='stop' else 'FAIL' if envelope else 'NOTRUN')
    if envelope and length and empty: cause='OUTPUT_LENGTH_LIMIT_WITH_EMPTY_CONTENT_REPORTED'
    elif envelope and length: cause='OUTPUT_LENGTH_LIMIT_REPORTED'
    elif envelope and empty: cause='EMPTY_CONTENT_REPORTED'
    elif exact=='PASS': cause='COMPLETE_OK_RESPONSE_IMPORTED'
    elif envelope: cause='EXACT_REPLY_OR_BUDGET_REQUIREMENT_NOT_MET'
    else: cause='NO_VALID_EXACT_MODEL_USAGE_ENVELOPE_REPORTED'
    return {'schema':'gir-offline-probe-diagnosis@1',
            'provenance':'CLOSED_USER_AUDIT_NOT_INDEPENDENT_PROVIDER_ATTESTATION',
            'original_status':evidence['status'],'original_failure':evidence['failure'],
            'http_status':evidence['http_status'],
            'exact_model_usage_envelope':'OBSERVED_IN_USER_AUDIT' if envelope else 'NOT_ESTABLISHED',
            'exact_OK_check':exact,'diagnosis':cause,
            'content_is_exact_empty_string_by_digest':empty,'finish_reason':evidence['finish_reason'],
            'max_output_tokens':evidence['max_output_tokens'],'usage':usage,
            'transport_status':evidence['transport_status'],
            'application_status':'TRUNCATED' if envelope and length else evidence['application_status'],
            'response_observations':evidence['response_observations'],
            'completion_tokens_equal_output_cap':exhausted,'within_reserved_budget':within,
            'reasoning_allocation':('UNKNOWN_NOT_SAVED' if evidence['response_observations'] is None else 'UNKNOWN_TOKEN_ATTRIBUTION'),
            'raw_response_reparse':('NORMALIZED_PROJECTION_REPARSED_NOT_RAW_HTTP' if evidence['status']=='RESPONSE_IMPORTED'
                                    else 'NOTRUN_RESPONSE_NOT_SAVED_FOR_FAILURE'),
            'reported_request_attempts':evidence['reported_request_attempts'],
            'reported_real_model_calls':evidence['reported_real_model_calls'],
            'diagnoser_authenticated_requests':0,'model_generated_source_executions':0,
            'benchmark_status':'NOTRUN','original_evidence_modified':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists():parser.error('Preserve prior evidence; choose a new output directory')
    try:
        report=diagnose_bundle(args.bundle)
        args.output.mkdir(parents=True,exist_ok=False)
        (args.output/'diagnosis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
        # This exit only means offline diagnosis completed, never a model PASS.
        return 0
    except (EvaluationError,OSError,ValueError,TypeError,KeyError):
        print(json.dumps({'status':'NOTRUN','code':'INVALID_OR_UNAVAILABLE_PUBLIC_BUNDLE'}));return 2


if __name__=='__main__':raise SystemExit(main())
