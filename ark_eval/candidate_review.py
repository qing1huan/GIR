"""Only the two observed original pair002 hashes have a human source review.

No generic text scan grants execution permission. The IR is statically rejected;
the exact JS needs actual parse evidence and a fresh in-process isolation receipt.
"""
import hashlib
from pathlib import Path

from headless_v01.compiler import compile_program,digest
from headless_v01.types import Fault
from .adapter import EvaluationError,bounded_json
from .create_pair import load_freeze
from .manual_pair import import_pair

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'results/user-local-create-pair-002'
PROTOCOL='a4d8a8ca3702954a170765597f84671a5fc2b45eb3e79d5754d9b5861f76bed8'
HASHES={'gameir_text':'de928b51c6d1308b3eebd66d83709fdf2d5725ed156eda06219ff25510acda6b',
        'js_callback':'0c2fac168775f2b9b21d518ad726c10c5afd5d155ff79746af139b77fd60835b'}
VERSION_FILES=('ark_eval/candidate_review.py','ark_eval/candidate_runner.py','ark_eval/candidate_eval.py',
               'ark_eval/candidate_parser.mjs','ark_eval/candidate_parse_backend.py',
               'ark_eval/callback_verify.py','ark_eval/js_verify.py','ark_eval/reviewed_js.py')


def version_bindings():
    contract=load_freeze()
    names=set(VERSION_FILES)|set(contract['artifact_hashes'])|{'ark_eval/create_pair.freeze.json'}
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(names)}


def load_candidates():
    for rep in HASHES:
        directory=BUNDLE/rep
        for name in ('candidate.txt','audit.json','request.json'):
            file=directory/name
            if file.is_symlink() or not file.resolve().is_relative_to(BUNDLE.resolve()):
                raise EvaluationError('CANDIDATE_PATH_REJECTED')
    record=import_pair(BUNDLE)
    if record['summary']['protocol_digest']!=PROTOCOL or digest(load_freeze())!=PROTOCOL:
        raise EvaluationError('CANDIDATE_GENERATION_PROTOCOL_DRIFT')
    sources={}
    for rep,sha in HASHES.items():
        file=BUNDLE/rep/'candidate.txt'
        if not file.is_file() or not 0<file.stat().st_size<=16384:raise EvaluationError('CANDIDATE_SIZE_REJECTED')
        raw=file.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise EvaluationError('CANDIDATE_SOURCE_CHANGED_OR_UNKNOWN')
        sources[rep]=raw.decode('utf-8',errors='strict')
    return sources,record


def static_review():
    sources,record=load_candidates()
    ir=sources['gameir_text']
    try:
        compile_program(bounded_json(ir))
    except Fault as error:
        rejection={'code':error.code,'phase':error.phase,'details':error.details}
    else:raise EvaluationError('EXPECTED_IR_STATIC_REJECTION_DRIFT')
    return {'schema':'gir-candidate-static-review@1','generation_protocol_digest':PROTOCOL,
            'candidate_sha256':dict(HASHES),'bindings':version_bindings(),
            'gameir_text':{'review':'REJECTED','compiler_fault':rejection,'execution':'NOTRUN_STATIC_REJECTION'},
            'js_callback':{'review':'MANUALLY_REVIEWED_EXACT_SOURCE_PENDING_PARSE_AND_ISOLATION',
                'finite_control_flow':True,'network_file_process_dynamic_load':'ABSENT_IN_EXACT_TEXT',
                'calls':['resolve','read','require','sub','write','emit'],
                'execution':'BLOCKED_PENDING_MANIFEST_PARSE_AND_FRESH_ISOLATION'},
            'original_model_summary':record['summary'],'candidate_executions':0}


def approval_manifest(parse_evidence):
    if not isinstance(parse_evidence,dict) or set(parse_evidence)!={'result','worker_audit','candidate_executions'}:
        raise EvaluationError('CANDIDATE_SYNTAX_APPROVAL_REJECTED')
    review=static_review();parsed=parse_evidence.get('result',{})
    audit=parse_evidence['worker_audit']
    parser_hash=hashlib.sha256((ROOT/'ark_eval/candidate_parser.mjs').read_bytes()+
        (ROOT/'ark_eval/node_supervisor.sh').read_bytes()+
        bounded_json((ROOT/'ark_eval/node_image_lock.json').read_text(encoding='utf-8'))['repository_digest'].encode()).hexdigest()
    if (parsed!={'status':'PARSED','exports':['onOpen'],'static_imports':[],
                 'source_sha256':HASHES['js_callback'],'top_level_evaluated':False,'callback_invoked':False} or
            type(parse_evidence['candidate_executions'])is not int or parse_evidence['candidate_executions']!=0 or
            not isinstance(audit,dict) or audit.get('worker_source_sha256')!=parser_hash or
            audit.get('cleanup_errors')!=[] or audit.get('surviving_owned_processes')!=0 or
            audit.get('network_mode')!='none' or audit.get('readonly_rootfs') is not True or
            audit.get('user_mounts')!=[] or audit.get('generated_source_executions')!=0 or
            audit.get('kernel_security')!={'CapEff':'0000000000000000','NoNewPrivs':'1','Seccomp':'2'} or
            audit.get('kernel_limits')!={'cpu.max':'25000 100000','memory.max':'67108864','memory.swap.max':'0','pids.max':'32'}):
        raise EvaluationError('CANDIDATE_SYNTAX_APPROVAL_REJECTED')
    return {'schema':'gir-candidate-approval@1','generation_protocol_digest':PROTOCOL,
            'candidate_sha256':dict(HASHES),'bindings':review['bindings'],
            'review':{'gameir_text':'REJECTED_STATIC_COMPILATION','js_callback':'APPROVED_EXACT_ORIGINAL_SOURCE'},
            'source_edited':False,'repairs':0,'tools_or_model_calls':0,
            'syntax_evidence':parse_evidence,'arbitrary_source_execution':'DENIED',
            'execution_requires':'FRESH_IN_PROCESS_ROOTLESS_PROFILE_AND_ALL_FAULT_CHECKS'}


def validate_approval(manifest):
    if not isinstance(manifest,dict) or set(manifest)!={'schema','generation_protocol_digest','candidate_sha256','bindings',
        'review','source_edited','repairs','tools_or_model_calls','syntax_evidence','arbitrary_source_execution','execution_requires'}:
        raise EvaluationError('CANDIDATE_APPROVAL_REJECTED')
    expected=approval_manifest(manifest['syntax_evidence'])
    if manifest!=expected:raise EvaluationError('CANDIDATE_APPROVAL_CHANGED_OR_STALE')
    return load_candidates()[0]['js_callback']
