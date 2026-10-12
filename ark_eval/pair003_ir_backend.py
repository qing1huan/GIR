"""Independent IR image; reuse unchanged rootless transport and kernel checks.

Bakes public installed WSL Python/runtime files only. No download, install,
host mount, credential input or candidate source baked into the image.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

from .adapter import EvaluationError
from .node_backend import NodeBackend

ENGINE_FILES=('headless_v01/__init__.py','headless_v01/compiler.py','headless_v01/types.py',
 'headless_v01/runtime.py','headless_v01/services.py','headless_v01/plan_checks.py',
 'ark_eval/__init__.py','ark_eval/adapter.py','ark_eval/bridge.py','ark_eval/tasks.py','ark_eval/oracle.py')


def image_payload(root,environment):
    files={'gir-worker.mjs':(root/'ark_eval/pair003_ir_shim.mjs').read_bytes(),
           'gir-ir-worker.py':(root/'ark_eval/pair003_ir_worker.py').read_bytes(),
           'gir-supervisor.sh':(root/'ark_eval/node_supervisor.sh').read_bytes()}
    for name in ENGINE_FILES:files['gir-runtime/'+name]=(root/name).read_bytes()
    binary=Path('/usr/bin/python3.12');stdlib=Path('/usr/lib/python3.12')
    if not binary.is_file() or not stdlib.is_dir():raise EvaluationError('INSTALLED_PYTHON312_UNAVAILABLE')
    files['gir-python/bin/python3.12']=binary.read_bytes()
    extensions=[]
    for path in sorted(stdlib.rglob('*')):
        if (path.is_file() and path.suffix in ('.py','.so') and '__pycache__' not in path.parts
                and path.name not in ('sitecustomize.py','usercustomize.py')):
            files['gir-python/lib/python3.12/'+path.relative_to(stdlib).as_posix()]=path.read_bytes()
            if path.suffix=='.so':extensions.append(str(path))
    proc=subprocess.run(['/usr/bin/ldd',str(binary),*extensions],capture_output=True,env=environment,cwd='/',timeout=20)
    if proc.returncode!=0 or b'not found' in proc.stdout:raise EvaluationError('PUBLIC_PYTHON_DEPENDENCY_UNAVAILABLE')
    libs=set(re.findall(r'(?<!\w)(/(?:usr/)?lib[^\s:()]*)',proc.stdout.decode('utf-8')))
    for name in sorted(libs):
        path=Path(name)
        if not path.is_file():continue
        target='gir-python/lib/'+path.name;content=path.read_bytes()
        if target in files and files[target]!=content:raise EvaluationError('PYTHON_LIBRARY_NAME_COLLISION')
        files[target]=content
    if 'gir-python/lib/ld-linux-x86-64.so.2' not in files:raise EvaluationError('PUBLIC_PYTHON_LOADER_UNAVAILABLE')
    return files


class Pair003IRBackend(NodeBackend):
    def _build(self):
        root=Path(__file__).resolve().parents[1];files=image_payload(root,self.environment)
        self.payload_hashes={n:hashlib.sha256(b).hexdigest() for n,b in sorted(files.items())}
        fingerprint=hashlib.sha256((json.dumps(self.payload_hashes,sort_keys=True)+self.lock['repository_digest']).encode()).hexdigest()
        self.worker_hash=fingerprint;tag='localhost/gir-pair003-ir:'+fingerprint[:24]
        exists=subprocess.run(['/usr/bin/podman','image','exists',tag],capture_output=True,env=self.environment,cwd='/',timeout=20)
        if exists.returncode not in (0,1):raise EvaluationError('IR_IMAGE_QUERY_FAILED')
        if exists.returncode==1:
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
