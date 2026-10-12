"""v4 SHA configuration over unchanged public IR image and Node transport."""
import hashlib,io,json,subprocess,tarfile
from .adapter import EvaluationError
from .node_backend import NodeBackend
from .pair003_ir_backend import image_payload
from .v4_scope import ApprovedV4Scope,ROOT

def v4_payload(scope,task,environment):
    if type(scope)is not ApprovedV4Scope:raise EvaluationError('V4_APPROVED_SCOPE_REQUIRED')
    scope.require();files=image_payload(ROOT,environment)
    old=b"SOURCE_SHA256='9cd7ea46ba86790a5413c78e5bd5846c538b75cfb65f6713c5596a47f1e87b42'"
    new=("SOURCE_SHA256='"+scope.manifest['source_sha256'][task+'/gameir_text']+"'").encode()
    if files['gir-ir-worker.py'].count(old)!=1:raise EvaluationError('V4_IR_TEMPLATE_DRIFT')
    files['gir-ir-worker.py']=files['gir-ir-worker.py'].replace(old,new)
    return files

class V4IRBackend(NodeBackend):
    def __init__(self,scope,task):
        if type(scope)is not ApprovedV4Scope:raise EvaluationError('V4_APPROVED_SCOPE_REQUIRED')
        scope.source(task,'gameir_text');self.scope=scope;self.task=task;super().__init__()

    def _build(self):
        # Existing content-addressed packaging scheme; all process/profile and
        # cleanup code remains inherited from NodeBackend/WorkerProcess.
        files=v4_payload(self.scope,self.task,self.environment)
        self.payload_hashes={n:hashlib.sha256(b).hexdigest() for n,b in sorted(files.items())}
        fingerprint=hashlib.sha256((json.dumps(self.payload_hashes,sort_keys=True)+self.lock['repository_digest']).encode()).hexdigest()
        self.worker_hash=fingerprint;tag='localhost/gir-scoped-ir:'+fingerprint[:24]
        found=subprocess.run(['/usr/bin/podman','image','exists',tag],capture_output=True,env=self.environment,cwd='/',timeout=20)
        if found.returncode not in (0,1):raise EvaluationError('IR_IMAGE_QUERY_FAILED')
        if found.returncode==1:
            files['Containerfile']=('FROM '+self.lock['repository_digest']+'\nCOPY . /\nLABEL gir.worker.source="'+fingerprint+
                '"\nENTRYPOINT ["/bin/sh", "/gir-supervisor.sh"]\n').encode()
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
