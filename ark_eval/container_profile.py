"""One fixed rootless profile shared by the real Node worker and its probes."""
import json
import os
from pathlib import Path
import re
import sys

from .adapter import EvaluationError

MEMORY = 64 * 1024 * 1024
CPU_QUOTA = (25000, 100000)
PIDS = 32
STDOUT_BYTES = 262144
STDERR_BYTES = 8192
BOOT_BYTES = 32768


def clean_environment():
    if sys.platform != "linux" or os.getuid() == 0:
        raise EvaluationError("ROOTLESS_LINUX_REQUIRED")
    import pwd
    uid = os.getuid(); account = pwd.getpwuid(uid)
    return {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": account.pw_dir,
            "USER": account.pw_name, "LOGNAME": account.pw_name, "LC_ALL": "C",
            "XDG_RUNTIME_DIR": "/run/user/" + str(uid),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/" + str(uid) + "/bus"}


def image_lock():
    lock = json.loads(Path(__file__).with_name("node_image_lock.json").read_text(encoding="utf-8"))
    if (set(lock) != {"schema", "repository_digest", "node_version", "architecture", "source_tag", "source"}
            or lock["schema"] != "gir-node-image@1" or lock["architecture"] != "amd64"
            or not re.fullmatch(r"docker\.io/library/node@sha256:[0-9a-f]{64}", lock["repository_digest"])
            or not re.fullmatch(r"v24\.\d+\.\d+", lock["node_version"])
            or lock["source"] != "https://github.com/nodejs/docker-node"):
        raise EvaluationError("INVALID_NODE_IMAGE_LOCK")
    return lock


def flags(name):
    if not re.fullmatch(r"gir-node-[0-9a-f]{32}", name):
        raise EvaluationError("INVALID_OWNED_CONTAINER_NAME")
    return ["--name", name, "--pull=never", "--network=none", "--read-only", "--log-driver=none",
            "--http-proxy=false", "--env-host=false", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--userns=keep-id",
            "--user=" + str(os.getuid()) + ":" + str(os.getgid()),
            "--cpus=0.25", "--memory=64m", "--memory-swap=64m", "--pids-limit=32",
            "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=8m", "--hostname=gir-node-worker",
            "--env=PATH=/usr/local/bin:/usr/bin:/bin", "--env=HOME=/tmp", "--env=LANG=C",
            "--label=gir.reviewed-worker=phase3"]


def verify_inspection(data):
    host = data["HostConfig"]
    if (host["NetworkMode"] != "none" or not host["ReadonlyRootfs"] or host["Privileged"]
            or host.get("Binds") or host.get('PidMode') == 'host' or host.get('IpcMode') == 'host'
            or host["Memory"] != MEMORY or host["PidsLimit"] != PIDS or host['LogConfig']['Type'] != 'none'
            or data["Config"]["User"] != str(os.getuid()) + ":" + str(os.getgid())
            or any(m.get("Type") != "tmpfs" or m.get("Destination") != "/tmp"
                   or m.get("Source", "").startswith("/") for m in data.get("Mounts", []))):
        raise EvaluationError("CONTAINER_PROFILE_DRIFT")
    if not any("no-new-privileges" in value for value in host["SecurityOpt"]):
        raise EvaluationError("CONTAINER_PROFILE_DRIFT")
    if data["Config"]["Labels"].get("gir.reviewed-worker") != "phase3":
        raise EvaluationError("OWNED_CONTAINER_IDENTITY_MISMATCH")

