"""Explicit user-local file input only. Never called by offline/import modes.

The caller must obtain local send confirmation first. This helper never scans
for keys, reads environment variables, changes permissions or starts a process.
"""
from pathlib import Path
import re
import stat

from .adapter import EvaluationError

MAX_FILE_BYTES=4096+3+2  # maximum ASCII key, optional UTF-8 BOM and one CRLF


def read_local_key(path, *, project_root):
    """Read one explicit file outside the project and ordinary Git checkouts.

Only fixed error codes escape. No credential content/path is printed, logged,
persisted or passed to a child process. Metadata checks are not an ACL audit.
"""
    try:
        path=Path(path).absolute();project_root=Path(project_root).resolve()
        if (path.name.lower() in {'env.txt','.env'} or path.is_relative_to(project_root)
                or '.git' in [part.lower() for part in path.parts]):
            raise EvaluationError('KEY_FILE_UNSAFE_LOCATION')
        # Use ordinary metadata operations, not strict final-handle resolution
        # that can require unavailable rights in a restricted Windows terminal.
        # Reject links/reparse points in the complete explicit path ancestry.
        for component in (path,*path.parents):
            info=component.lstat()
            if (component.is_symlink() or getattr(info,'st_file_attributes',0)
                    &getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0)):
                raise EvaluationError('KEY_FILE_UNSAFE_LOCATION')
        resolved=path.resolve()
        if resolved.is_relative_to(project_root):raise EvaluationError('KEY_FILE_UNSAFE_LOCATION')
        # Reject all ordinary Git checkouts/worktrees, including ignored files.
        # Ancestor metadata only; no directory listing or Git/config subprocess.
        for parent in {path.parent,*path.parents,resolved.parent,*resolved.parents}:
            if (parent/'.git').exists():raise EvaluationError('KEY_FILE_GIT_LOCATION_REJECTED')
        info=path.stat()
        if (not stat.S_ISREG(info.st_mode)
                or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0)):
            raise EvaluationError('KEY_FILE_UNSAFE_LOCATION')
        if not 1<=info.st_size<=MAX_FILE_BYTES:raise EvaluationError('KEY_FILE_INVALID_FORMAT')
        with path.open('rb') as handle:raw=handle.read(MAX_FILE_BYTES+1)
        if len(raw)>MAX_FILE_BYTES:raise EvaluationError('KEY_FILE_INVALID_FORMAT')
        value=raw.decode('utf-8-sig',errors='strict')
        if value.endswith('\r\n'):value=value[:-2]
        elif value.endswith('\n'):value=value[:-1]
        if (not 8<=len(value)<=4096 or any(ord(c)<33 or ord(c)>126 for c in value)
                or value[0] in {'\"',"'"} or value[-1] in {'\"',"'"}
                or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',value)):
            raise EvaluationError('KEY_FILE_INVALID_FORMAT')
        return value
    except EvaluationError:raise
    except FileNotFoundError:raise EvaluationError('KEY_FILE_NOT_FOUND') from None
    except (OSError,ValueError,UnicodeError,RuntimeError):
        raise EvaluationError('KEY_FILE_UNREADABLE_OR_INVALID') from None
