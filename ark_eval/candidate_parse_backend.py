"""Separate trusted parser image; reuse the actual rootless worker transport/profile."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from .adapter import EvaluationError,bounded_json
from .node_backend import NodeBackend


class CandidateParserBackend(NodeBackend):
    def _build(self):
        root=Path(__file__).parent
        files={'node_worker.mjs':(root/'candidate_parser.mjs').read_bytes(),
               'node_supervisor.sh':(root/'node_supervisor.sh').read_bytes()}
        fingerprint=hashlib.sha256(files['node_worker.mjs']+files['node_supervisor.sh']+
                                  self.lock['repository_digest'].encode()).hexdigest()
        self.worker_hash=fingerprint
        tag='localhost/gir-node-worker:'+fingerprint[:24]
        found=subprocess.run(['/usr/bin/podman','image','exists',tag],capture_output=True,
                             env=self.environment,cwd='/',timeout=20)
        if found.returncode not in (0,1):raise EvaluationError('IMAGE_QUERY_FAILED')
        if found.returncode==1:
            definition=('FROM '+self.lock['repository_digest']+'\nCOPY node_worker.mjs /gir-worker.mjs\n'
                        'COPY node_supervisor.sh /gir-supervisor.sh\nLABEL gir.worker.source="'+fingerprint+'"\n'
                        'ENTRYPOINT ["/bin/sh", "/gir-supervisor.sh"]\n')
            files['Containerfile']=definition.encode();archive=io.BytesIO()
            with tarfile.open(fileobj=archive,mode='w') as tar:
                for name,content in files.items():
                    member=tarfile.TarInfo(name);member.size=len(content);member.mode=0o444
                    tar.addfile(member,io.BytesIO(content))
            self._ctl('build','--pull=never','--network=none','-t',tag,'-',data=archive.getvalue(),timeout=90)
        image=json.loads(self._ctl('image','inspect',tag))[0]
        if image['Labels'].get('gir.worker.source')!=fingerprint:raise EvaluationError('WORKER_IMAGE_DRIFT')
        return 'sha256:'+image['Id'].removeprefix('sha256:')


def parse_module(source,backend):
    if type(backend)is not CandidateParserBackend:raise EvaluationError('PARSE_BACKEND_REJECTED')
    worker=backend.invocation(10)
    try:
        worker.open()
        worker.write({'v':'gir-candidate-parse@1','purpose':'parse_only','source':source},bootstrap=True)
        message=bounded_json(worker.read_line())
        if not isinstance(message,dict) or set(message)!={'transport','result'} or message['transport']!='gir-candidate-parse-result@1':
            raise EvaluationError('PARSE_PROTOCOL_REJECTED')
        result=message['result']
        expected={'status','exports','static_imports','source_sha256','top_level_evaluated','callback_invoked'}
        if (not isinstance(result,dict) or set(result)!=expected or result['status'] not in
                ('PARSED','SYNTAX_REJECTED','IMPORTS_REJECTED','EXPORTS_REJECTED') or
                result['source_sha256']!=hashlib.sha256(source.encode('utf-8')).hexdigest() or
                result['top_level_evaluated'] is not False or result['callback_invoked'] is not False or
                not isinstance(result['exports'],list) or not isinstance(result['static_imports'],list)):
            raise EvaluationError('PARSE_PROTOCOL_REJECTED')
        worker.release(bounded_json(worker.read_line()))
    finally:worker.close()
    if worker.audit['cleanup_errors'] or worker.audit['surviving_owned_processes']!=0:
        raise EvaluationError('PARSE_ISOLATION_CLEANUP_FAILED')
    return {'result':result,'worker_audit':worker.audit,'candidate_executions':0}
