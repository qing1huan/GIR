"""Small exact-scope adapter over existing public Python image packaging."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from .adapter import EvaluationError
from .node_backend import NodeBackend
from .pair003_ir_backend import image_payload
from .repair001_scope import ExactScope


def scoped_payload(root,environment,scope):
    if type(scope)is not ExactScope:raise EvaluationError('IR_EXACT_SCOPE_REQUIRED')
    scope.require();files=image_payload(root,environment)
    old=b"SOURCE_SHA256='9cd7ea46ba86790a5413c78e5bd5846c538b75cfb65f6713c5596a47f1e87b42'"
    new=("SOURCE_SHA256='"+scope.manifest['candidate_sha256']['gameir_text']+"'").encode()
    template=files['gir-ir-worker.py']
    if template.count(old)!=1:raise EvaluationError('IR_WORKER_TEMPLATE_DRIFT')
    # Only trusted worker hash configuration changes. No candidate text is baked,
    # transformed or patched; unchanged candidate JSON is sent via bounded stdin.
    files['gir-ir-worker.py']=template.replace(old,new)
    return files


class ScopedIRBackend(NodeBackend):
    def __init__(self,scope):
        if type(scope)is not ExactScope:raise EvaluationError('IR_EXACT_SCOPE_REQUIRED')
        scope.require();self.scope=scope;super().__init__()

    def _build(self):
        root=Path(__file__).resolve().parents[1];files=scoped_payload(root,self.environment,self.scope)
        self.payload_hashes={n:hashlib.sha256(b).hexdigest() for n,b in sorted(files.items())}
        fingerprint=hashlib.sha256((json.dumps(self.payload_hashes,sort_keys=True)+self.lock['repository_digest']).encode()).hexdigest()
        self.worker_hash=fingerprint;tag='localhost/gir-scoped-ir:'+fingerprint[:24]
        found=subprocess.run(['/usr/bin/podman','image','exists',tag],capture_output=True,env=self.environment,cwd='/',timeout=20)
        if found.returncode not in (0,1):raise EvaluationError('IR_IMAGE_QUERY_FAILED')
        if found.returncode==1:
            files['Containerfile']=('FROM '+self.lock['repository_digest']+'\nCOPY . /\n'
                'LABEL gir.worker.source="'+fingerprint+'"\nENTRYPOINT ["/bin/sh", "/gir-supervisor.sh"]\n').encode()
            archive=io.BytesIO()
            with tarfile.open(fileobj=archive,mode='w') as tar:
                for name,content in files.items():
                    member=tarfile.TarInfo(name);member.size=len(content)
                    member.mode=0o555 if name in {'gir-python/bin/python3.12','gir-python/lib/ld-linux-x86-64.so.2'} else 0o444
                    tar.addfile(member,io.BytesIO(content))
            self._ctl('build','--pull=never','--network=none','-t',tag,'-',data=archive.getvalue(),timeout=90)
        image=json.loads(self._ctl('image','inspect',tag))[0]
        if image['Labels'].get('gir.worker.source')!=fingerprint:raise EvaluationError('IR_IMAGE_DRIFT')
        return 'sha256:'+image['Id'].removeprefix('sha256:')
