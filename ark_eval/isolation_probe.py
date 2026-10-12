"""Read-only tool availability probe; never proves isolation or enables execution."""
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def probe():
    commands = {name: shutil.which(name) for name in ("node", "docker", "podman", "wsl")}
    report = {"kind": "READ_ONLY_AVAILABILITY_NOT_ISOLATION_PROOF", "commands": commands,
              "platform": platform.platform(), "python": sys.version,
              "windows_sandbox_executable": Path("C:/Windows/System32/WindowsSandbox.exe").is_file(),
              "source_executor": "DISABLED_ISOLATION_UNVERIFIED", "source_executions": 0,
              "real_model_calls": 0, "settings_changed": False,
              "unverified": ["filesystem_and_credentials_denied", "network_egress_denied",
                             "environment_scrubbed", "spawn_and_native_module_denied",
                             "cpu_memory_process_limits", "wall_timeout_and_process_tree_kill"]}
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
                report["os_metadata"] = {name: winreg.QueryValueEx(key, name)[0]
                                         for name in ("ProductName", "EditionID", "CurrentBuild")}
        except OSError: report["os_metadata"] = "UNAVAILABLE"
    if commands["wsl"]:
        try:
            completed = subprocess.run([commands["wsl"], "--status"], capture_output=True, timeout=5)
            def decode(data):
                return data.decode("utf-16-le" if b"\x00" in data else "utf-8", errors="replace").strip()
            report["wsl_status"] = {"exit_code": completed.returncode,
                                    "stdout": decode(completed.stdout), "stderr": decode(completed.stderr)}
        except (OSError, subprocess.TimeoutExpired): report["wsl_status"] = "UNAVAILABLE"
    return report


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=True, indent=2))
