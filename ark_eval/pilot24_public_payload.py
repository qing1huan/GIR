"""Exact frozen public bytes recovered from a retained, audited local image."""
import hashlib
import io
from pathlib import PurePosixPath
import tarfile

from .pair003_ir_backend import ENGINE_FILES
from .pilot24_execution_scope import ROOT, PUBLIC_RUNTIME, read, require, sha

ARCHIVE = 'results/pilot24-frozen-public-runtime-20261012-001.tar'
ARCHIVE_SHA = 'fd0fb99b2a2ed8d59e029aba1315678ad3747bb0d69279200951c29744d184c0'
ARCHIVE_LIMIT = 160 * 1024 * 1024
TOTAL_LIMIT = 128 * 1024 * 1024
MEMBER_LIMIT = 16 * 1024 * 1024


def source_paths():
    paths = {'gir-worker.mjs': 'ark_eval/pair003_ir_shim.mjs',
             'gir-ir-worker.py': 'ark_eval/pilot24_execution_worker.py',
             'gir-supervisor.sh': 'ark_eval/node_supervisor.sh'}
    for name in ENGINE_FILES + ('ark_eval/host.py', 'ark_eval/callback_contract.py', 'ark_eval/budget.py'):
        paths['gir-runtime/' + name] = name
    return paths


def frozen_public_payload():
    expected = read(PUBLIC_RUNTIME)['files_sha256']
    require(type(expected) is dict and len(expected) == 701, 'P24_PUBLIC_ARCHIVE_FILE_COUNT')
    # An archive must not conceal drift in the currently reviewed repository.
    require(all(name in expected and sha(path) == expected[name]
                for name, path in source_paths().items()), 'P24_PUBLIC_REPOSITORY_SOURCE_DRIFT')
    path = ROOT / ARCHIVE
    require(path.resolve() == path and path.is_file() and
            not any(p.is_symlink() for p in (path, *path.parents)), 'P24_PUBLIC_ARCHIVE_PATH')
    with path.open('rb') as handle:
        content = handle.read(ARCHIVE_LIMIT + 1)
    require(len(content) <= ARCHIVE_LIMIT and hashlib.sha256(content).hexdigest() == ARCHIVE_SHA,
            'P24_PUBLIC_ARCHIVE_DIGEST')
    files = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:') as archive:
        for member in archive:
            name = member.name
            parts = PurePosixPath(name)
            require(name in expected and name not in files and not parts.is_absolute() and
                    '..' not in parts.parts and str(parts) == name and member.isfile() and
                    not member.issym() and not member.islnk() and
                    0 <= member.size <= MEMBER_LIMIT, 'P24_PUBLIC_ARCHIVE_MEMBER')
            total += member.size
            require(total <= TOTAL_LIMIT, 'P24_PUBLIC_ARCHIVE_TOTAL_LIMIT')
            data = archive.extractfile(member).read(member.size + 1)
            require(len(data) == member.size and hashlib.sha256(data).hexdigest() == expected[name],
                    'P24_PUBLIC_ARCHIVE_CONTENT')
            files[name] = data
    require(set(files) == set(expected), 'P24_PUBLIC_ARCHIVE_FILE_SET')
    return files
