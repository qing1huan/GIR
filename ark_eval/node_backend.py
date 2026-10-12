"""Linux rootless Podman transport, with per-launch kernel/profile verification.

Only trusted controller code executes here. JS runs in a no-mount, no-network
container. OS isolation is enforced independently of Node vm/context behavior.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tarfile
import time
import uuid

from headless_v01.types import Fault, canonical_json
from .adapter import EvaluationError, bounded_json
from .callback_contract import MESSAGE_BYTES
from .container_profile import (BOOT_BYTES, CPU_QUOTA, MEMORY, PIDS, STDERR_BYTES,
                                STDOUT_BYTES, clean_environment, flags, image_lock, verify_inspection)


class PodmanControlError(EvaluationError):
    """Control failure metadata contains fixed categories, never CLI output."""
    def __init__(self, command, returncode, reason):
        super().__init__('PODMAN_CONTROL_FAILED')
        self.command = command if command in ('inspect', 'top', 'kill', 'rm', 'create') else 'other'
        self.returncode = returncode
        self.reason = reason


class NodeBackend:
    def __init__(self):
        self.environment = clean_environment()
        self.lock = image_lock(); self.disabled = False
        info = json.loads(self._ctl('info', '--format=json'))['host']
        if (not info['security']['rootless'] or info['cgroupVersion'] != 'v2'
                or info['cgroupManager'] != 'systemd'
                or not {'cpu', 'memory', 'pids'} <= set(info.get('cgroupControllers', []))):
            raise EvaluationError('ROOTLESS_RESOURCE_LIMITS_UNAVAILABLE')
        self.host = {key: info[key] for key in ('cgroupVersion', 'cgroupManager', 'cgroupControllers')}
        self.host['rootless'] = True; self.host['uid'] = os.getuid()
        self.worker_image = self._build()

    def _ctl(self, *args, data=None, timeout=20, allowed=(0,)):
        result = subprocess.run(['/usr/bin/podman', *args], input=data, capture_output=True,
                                env=self.environment, cwd='/', timeout=timeout)
        if result.returncode not in allowed or len(result.stdout) > 1048576 or len(result.stderr) > 65536:
            reason = 'CONTROL_FAILED'
            if len(result.stdout) > 1048576 or len(result.stderr) > 65536:
                reason = 'OUTPUT_LIMIT'
            elif (args[0] == 'top' and result.returncode == 125 and
                  result.stderr == b'Error: top can only be used on running containers\n'):
                reason = 'TOP_NOT_RUNNING'
            raise PodmanControlError(args[0], result.returncode, reason)
        return result.stdout.decode('utf-8', errors='strict').strip()

    def inspect(self, name):
        return json.loads(self._ctl('inspect', name))[0]

    def _build(self):
        root = Path(__file__).parent
        files = {name: (root / name).read_bytes() for name in ('node_worker.mjs', 'node_supervisor.sh')}
        fingerprint = hashlib.sha256(files['node_worker.mjs'] + files['node_supervisor.sh']
                                    + self.lock['repository_digest'].encode()).hexdigest()
        self.worker_hash = fingerprint
        tag = 'localhost/gir-node-worker:' + fingerprint[:24]
        result = subprocess.run(['/usr/bin/podman', 'image', 'exists', tag],
                                capture_output=True, env=self.environment, cwd='/', timeout=20)
        if result.returncode not in (0, 1): raise EvaluationError('IMAGE_QUERY_FAILED')
        if result.returncode == 1:
            definition = ('FROM ' + self.lock['repository_digest'] + '\n'
                          'COPY node_worker.mjs /gir-worker.mjs\n'
                          'COPY node_supervisor.sh /gir-supervisor.sh\n'
                          'LABEL gir.worker.source="' + fingerprint + '"\n'
                          'ENTRYPOINT ["/bin/sh", "/gir-supervisor.sh"]\n')
            files['Containerfile'] = definition.encode()
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as tar:
                for name, content in files.items():
                    member = tarfile.TarInfo(name); member.size = len(content); member.mode = 0o444
                    tar.addfile(member, io.BytesIO(content))
            self._ctl('build', '--pull=never', '--network=none', '-t', tag, '-',
                      data=archive.getvalue(), timeout=90)
        image = json.loads(self._ctl('image', 'inspect', tag))[0]
        if image['Labels'].get('gir.worker.source') != fingerprint:
            raise EvaluationError('WORKER_IMAGE_DRIFT')
        return 'sha256:' + image['Id'].removeprefix('sha256:')

    def invocation(self, timeout=10):
        if self.disabled: raise EvaluationError('BACKEND_DISABLED_AFTER_ISOLATION_FAILURE')
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= 10:
            raise EvaluationError('INVALID_WALL_TIMEOUT')
        return WorkerProcess(self, timeout)


def process_identity(pid):
    try:
        tail = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()
        return tail[19], tail[0]
    except FileNotFoundError:
        return None


class WorkerProcess:
    def __init__(self, backend, timeout):
        self.backend = backend; self.name = 'gir-node-' + uuid.uuid4().hex
        self.deadline = time.monotonic() + timeout; self.process = None; self.created = False
        self.selector = selectors.DefaultSelector(); self.buffer = bytearray()
        self.stdout_bytes = 0; self.stderr_bytes = 0; self.eof = False; self.cgroup = None
        self.container_id = None
        self.audit = {'container': self.name, 'image': backend.worker_image,
                      'base_digest': backend.lock['repository_digest'], 'node': backend.lock['node_version'],
                      'worker_source_sha256': backend.worker_hash, 'rootless_host': backend.host,
                      'wall_timeout_seconds': timeout, 'input_keys': None, 'terminal': None,
                      'reviewed_source_executions': 0, 'generated_source_executions': 0,
                      'terminated_tree_processes': 0, 'surviving_owned_processes': None}

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise Fault('JS_TIMEOUT', phase='callback')
        return remaining

    def open(self):
        self.container_id = self.backend._ctl('create', '--interactive', *flags(self.name), self.backend.worker_image)
        self.created = True
        if not re.fullmatch('[0-9a-f]{64}', self.container_id):
            raise EvaluationError('OWNED_CONTAINER_IDENTITY_MISMATCH')
        self.audit['container_id'] = self.container_id
        self.process = subprocess.Popen(['/usr/bin/podman', 'start', '--attach', '--interactive', self.name],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=self.backend.environment, cwd='/', start_new_session=True)
        self.selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
        self.selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
        while True:
            self.remaining(); data = self.backend.inspect(self.name)
            if data['State']['Running']: break
            if data['State']['Status'] in ('exited', 'stopped'): raise Fault('JS_EXITED', phase='callback')
            time.sleep(.02)
        self.verify_owned(data)
        pid = data['State']['Pid']
        if os.stat('/proc/' + str(pid)).st_uid != os.getuid():
            raise EvaluationError('CONTAINER_HOST_UID_DRIFT')
        if os.readlink('/proc/' + str(pid) + '/ns/pid') == os.readlink('/proc/self/ns/pid'):
            raise EvaluationError('HOST_PID_NAMESPACE_REJECTED')
        status = {row.split(':', 1)[0]: row.split(':', 1)[1].strip()
                  for row in Path('/proc/' + str(pid) + '/status').read_text().splitlines() if ':' in row}
        if int(status['CapEff'], 16) != 0 or status['NoNewPrivs'] != '1' or status['Seccomp'] != '2':
            raise EvaluationError('KERNEL_SECURITY_PROFILE_DRIFT')
        self.audit['kernel_security'] = {key: status[key] for key in ('CapEff', 'NoNewPrivs', 'Seccomp')}
        cgroup_line = Path('/proc/' + str(pid) + '/cgroup').read_text().strip()
        if not cgroup_line.startswith('0::/'): raise EvaluationError('CGROUP_V2_REQUIRED')
        self.cgroup = (Path('/sys/fs/cgroup') / cgroup_line[4:]).resolve()
        if not self.cgroup.is_relative_to(Path('/sys/fs/cgroup')):
            raise EvaluationError('CGROUP_PATH_REJECTED')
        limits = {name: (self.cgroup / name).read_text().strip()
                  for name in ('cpu.max', 'memory.max', 'memory.swap.max', 'pids.max')}
        quota = tuple(map(int, limits['cpu.max'].split()))
        if quota != CPU_QUOTA or limits['memory.max'] != str(MEMORY) or limits['pids.max'] != str(PIDS) or limits['memory.swap.max'] != '0':
            raise EvaluationError('KERNEL_RESOURCE_LIMIT_DRIFT')
        self.audit['kernel_limits'] = limits
        self.audit['network_mode'] = data['HostConfig']['NetworkMode']
        self.audit['readonly_rootfs'] = data['HostConfig']['ReadonlyRootfs']
        self.audit['log_driver'] = data['HostConfig']['LogConfig']['Type']
        self.audit['user_mounts'] = data.get('Mounts', [])
        self.audit['initial_memory_events'] = self.memory_events()
        ready = bounded_json(self.read_line())
        if ready != {'transport': 'gir-node-ready@1', 'node': self.backend.lock['node_version']}:
            raise Fault('ProtocolViolation', phase='callback')
        return self

    def memory_events(self):
        try:
            return {key: int(value) for key, value in
                    (row.split() for row in (self.cgroup / 'memory.events').read_text().splitlines())}
        except (FileNotFoundError, AttributeError): return None

    def write(self, value, *, bootstrap=False):
        raw = canonical_json(value).encode('utf-8') + b'\n'
        if len(raw) > (BOOT_BYTES if bootstrap else MESSAGE_BYTES):
            raise Fault('BudgetExceeded', {'resource': 'worker_input_bytes'}, 'callback')
        if bootstrap: self.audit['input_keys'] = sorted(value)
        try:
            self.process.stdin.write(raw); self.process.stdin.flush()
        except (BrokenPipeError, OSError): raise Fault('JS_DISCONNECTED', phase='callback') from None

    def read_line(self):
        while True:
            index = self.buffer.find(b'\n')
            if index >= 0:
                if index > MESSAGE_BYTES: raise Fault('BudgetExceeded', {'resource':'worker_line_bytes'}, 'callback')
                raw = bytes(self.buffer[:index]); del self.buffer[:index+1]
                try: return raw.decode('utf-8', errors='strict')
                except UnicodeError: raise Fault('ProtocolViolation', phase='callback') from None
            if len(self.buffer) > MESSAGE_BYTES: raise Fault('BudgetExceeded', {'resource':'worker_line_bytes'}, 'callback')
            if self.eof: raise Fault('JS_DISCONNECTED', phase='callback')
            for key, _ in self.selector.select(min(self.remaining(), .25)):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.data == 'stdout': self.eof = True
                    continue
                if key.data == 'stdout':
                    self.stdout_bytes += len(chunk); self.buffer.extend(chunk)
                    if self.stdout_bytes > STDOUT_BYTES:
                        raise Fault('BudgetExceeded', {'resource':'worker_stdout_bytes'}, 'callback')
                else:
                    self.stderr_bytes += len(chunk)
                    if self.stderr_bytes > STDERR_BYTES:
                        raise Fault('BudgetExceeded', {'resource':'worker_stderr_bytes'}, 'callback')

    def release(self, exit_frame, *, require_zero=True):
        if (not isinstance(exit_frame, dict) or set(exit_frame) != {'transport', 'code'}
                or exit_frame['transport'] != 'gir-node-exit@1' or type(exit_frame['code']) is not int
                or not 0 <= exit_frame['code'] <= 255):
            raise Fault('ProtocolViolation', phase='callback')
        self.audit['node_exit_code'] = exit_frame['code']
        self.audit['final_memory_events'] = self.memory_events()
        if require_zero and exit_frame['code'] != 0: raise Fault('JS_EXITED', phase='callback')
        self.process.stdin.write(b'release\n'); self.process.stdin.flush(); self.process.stdin.close()
        try: code = self.process.wait(timeout=self.remaining())
        except subprocess.TimeoutExpired: raise Fault('JS_TIMEOUT', phase='callback') from None
        # Drain after real container exit; no fake exit/done frame can bypass trailing-output checks.
        while self.selector.get_map():
            for key, _ in self.selector.select(min(self.remaining(), .1)):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk: self.selector.unregister(key.fileobj); continue
                if key.data == 'stdout': self.buffer.extend(chunk); self.stdout_bytes += len(chunk)
                else: self.stderr_bytes += len(chunk)
            if self.stderr_bytes > STDERR_BYTES or self.stdout_bytes > STDOUT_BYTES:
                raise Fault('BudgetExceeded', phase='callback')
        state = self.backend.inspect(self.name)['State']
        if self.buffer or (require_zero and (code != 0 or state['ExitCode'] != 0)) or state['Running']:
            raise Fault('ProtocolViolation', phase='callback')

    def verify_owned(self, data):
        verify_inspection(data)
        if (not self.container_id or data['Id'] != self.container_id or
                data['Image'].removeprefix('sha256:') != self.backend.worker_image.removeprefix('sha256:') or
                data['Config']['Labels'].get('gir.worker.source') != self.backend.worker_hash):
            raise EvaluationError('OWNED_CONTAINER_IDENTITY_MISMATCH')

    def verify_exited_tree(self):
        """Verify the bound cgroup subtree, including a removed scope, fail closed."""
        root = Path('/sys/fs/cgroup')
        path = self.cgroup
        if (path is None or path == root or not path.is_relative_to(root) or
                root.resolve(strict=True) != root or path.resolve() != path):
            raise EvaluationError('OWNED_CGROUP_UNVERIFIED')
        try:
            events = dict(row.split() for row in (path / 'cgroup.events').read_text().splitlines())
        except FileNotFoundError:
            # A missing events file in an existing scope is not an empty tree.
            try:
                path.lstat()
            except FileNotFoundError:
                # systemd removes both the container child and its libpod scope.
                # Find the first existing ancestor and verify the removed branch
                # is absent there; inaccessible paths never mean empty scopes.
                removed = path; parent = path.parent
                while parent != root:
                    try: parent.lstat()
                    except FileNotFoundError:
                        removed = parent; parent = parent.parent
                    else: break
                if parent.resolve(strict=True) != parent or not parent.is_dir():
                    raise EvaluationError('OWNED_CGROUP_UNVERIFIED')
                if removed.name not in {entry.name for entry in parent.iterdir()}:
                    self.audit['exit_race_cgroup'] = 'BOUND_SCOPE_REMOVED'
                    return
            raise EvaluationError('OWNED_CGROUP_UNVERIFIED') from None
        if events.get('populated') != '0':
            raise EvaluationError('OWNED_CGROUP_NOT_EMPTY')
        self.audit['exit_race_cgroup'] = 'SUBTREE_POPULATED_ZERO'

    def close(self):
        self.audit['stdout_bytes'] = self.stdout_bytes; self.audit['stderr_bytes'] = self.stderr_bytes
        if self.audit.get('final_memory_events') is None:
            self.audit['final_memory_events'] = self.memory_events()
        identities = {}; failures = []; stage = 'inspect_owned'
        try:
            if self.created:
                data = self.backend.inspect(self.name); self.verify_owned(data)
                if data['State']['Running']:
                    stage = 'top_owned'
                    try:
                        rows = self.backend._ctl('top', self.container_id, 'hpid').splitlines()[1:]
                    except PodmanControlError as error:
                        if error.command != 'top' or error.returncode != 125 or error.reason != 'TOP_NOT_RUNNING':
                            raise
                        stage = 'verify_exit_race'
                        exited = self.backend.inspect(self.container_id); self.verify_owned(exited)
                        state = exited['State']
                        if (state['Running'] is not False or type(state['Pid']) is not int or state['Pid'] != 0 or
                                state['Status'] not in ('exited', 'stopped') or state['OOMKilled'] is not False or
                                any(state.get(key, True) is not False for key in ('Paused', 'Restarting', 'Dead'))):
                            raise EvaluationError('OWNED_EXIT_STATE_UNVERIFIED')
                        self.verify_exited_tree()
                        self.audit['cleanup_exit_race'] = 'VERIFIED_TOP_NOT_RUNNING'
                    else:
                        identities = {int(row): process_identity(int(row)) for row in rows if row.strip().isdigit()}
                        # Release bounded stdout/stderr backpressure before conmon cleanup.
                        stage = 'disconnect_owned_transport'; self.disconnect_transport()
                        stage = 'kill_owned'
                        self.backend._ctl('kill', '--signal', 'KILL', self.container_id, allowed=(0, 125))
                stage = 'verify_process_tree'
                deadline = time.monotonic() + 4
                survivors = list(identities)
                while time.monotonic() < deadline:
                    survivors = [pid for pid, prior in identities.items() if prior and
                                 (current := process_identity(pid)) and current[0] == prior[0] and current[1] != 'Z']
                    if not survivors: break
                    time.sleep(.02)
                self.audit['terminated_tree_processes'] = len(identities)
                if survivors:
                    self.audit['surviving_owned_processes'] = len(survivors)
                    raise EvaluationError('PROCESS_TREE_SURVIVED')
                stage = 'remove_owned'
                self.backend._ctl('rm', '--force', '--time', '1', self.container_id)
                self.audit['surviving_owned_processes'] = 0
        except Exception as error:
            failures.append('OWNED_CONTAINER_CLEANUP_FAILED:' + type(error).__name__); self.backend.disabled = True
            self.audit['cleanup_failure'] = {'stage': stage, 'exception': type(error).__name__}
            if isinstance(error, PodmanControlError):
                self.audit['cleanup_failure'].update(command=error.command, returncode=error.returncode, reason=error.reason)
            elif isinstance(error, EvaluationError) and error.args and error.args[0] in {
                    'CONTAINER_PROFILE_DRIFT', 'OWNED_CONTAINER_IDENTITY_MISMATCH',
                    'OWNED_EXIT_STATE_UNVERIFIED', 'OWNED_CGROUP_UNVERIFIED',
                    'OWNED_CGROUP_NOT_EMPTY', 'PROCESS_TREE_SURVIVED'}:
                self.audit['cleanup_failure']['code'] = error.args[0]
        finally:
            if self.process:
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL); self.process.wait(timeout=2)
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream and not stream.closed: stream.close()
            self.selector.close()
        self.audit['cleanup_errors'] = failures
        if failures: raise EvaluationError('ISOLATION_CLEANUP_FAILED')

    def disconnect_transport(self):
        """Only the process group created by this controller; never a user terminal."""
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=2)
