import ctypes
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from typing import Optional

from .constants import HOSTS_PATH
from .i18n import T

logger = logging.getLogger("HOTS.hosts_lock")


HOTS_PROGRAMDATA_DIR = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "HOTS Hosts Lite")

CREATE_NO_WINDOW = 0x08000000

_SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
POWERSHELL_EXE = os.path.join(_SYSTEM32, "WindowsPowerShell", "v1.0", "powershell.exe")

HOSTS_LOCK_STATE_FILE = os.path.join(HOTS_PROGRAMDATA_DIR, "HOTS_hosts_lock_state.json")
HOSTS_LOCK_SID = "S-1-5-32-545"

_ps_exe_cache: Optional[str] = None
_ps_exe_resolved = False

def _resolve_powershell_exe() -> Optional[str]:
    global _ps_exe_cache, _ps_exe_resolved
    if _ps_exe_resolved:
        return _ps_exe_cache

    _ps_exe_resolved = True

    if os.path.isfile(POWERSHELL_EXE):
        _ps_exe_cache = POWERSHELL_EXE
        return _ps_exe_cache

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    pwsh_root = os.path.join(program_files, "PowerShell")
    candidate = None
    try:
        if os.path.isdir(pwsh_root):
            for entry in sorted(os.listdir(pwsh_root), reverse=True):
                exe = os.path.join(pwsh_root, entry, "pwsh.exe")
                if os.path.isfile(exe):
                    candidate = exe
                    break
    except Exception:
        candidate = None

    if candidate:
        logger.warning("powershell.exe unavailable — using pwsh.exe: %s", candidate)
        _ps_exe_cache = candidate
        return _ps_exe_cache

    which_candidate = shutil.which("powershell") or shutil.which("pwsh")
    if which_candidate:
        logger.warning("powershell.exe/pwsh.exe not at default paths — found via PATH: %s", which_candidate)
        _ps_exe_cache = which_candidate
        return _ps_exe_cache

    logger.error("No PowerShell interpreter found (neither powershell.exe nor pwsh.exe).")
    _ps_exe_cache = None
    return _ps_exe_cache

def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def _console_encoding() -> str:
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"

def _atomic_write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".antispy_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

def _load_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None

class HostsLockError(Exception):
    pass

class HostsLockManager:

    last_error: str = ""
    _op_lock = threading.RLock()

    @staticmethod
    def _load_state() -> dict:
        return _load_json(HOSTS_LOCK_STATE_FILE) or {"active": False}

    @staticmethod
    def _save_state(active: bool) -> None:
        _atomic_write_json(HOSTS_LOCK_STATE_FILE, {"active": active})

    @staticmethod
    def is_active() -> bool:
        return bool(HostsLockManager._load_state().get("active"))

    @staticmethod
    def _real_locked(hosts_path: str = HOSTS_PATH) -> Optional[bool]:
        exe = _resolve_powershell_exe()
        if exe is None:
            logger.warning("HostsLockManager._real_locked: no PowerShell interpreter available")
            return None
        safe_path = hosts_path.replace("'", "''")
        ps_command = (
            f"$sid = New-Object System.Security.Principal.SecurityIdentifier('{HOSTS_LOCK_SID}'); "
            f"$acl = Get-Acl -LiteralPath '{safe_path}'; "
            "$found = $acl.Access | Where-Object { "
            "$_.AccessControlType -eq 'Deny' -and "
            "$_.FileSystemRights.ToString() -match 'Write' -and "
            "($_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $sid.Value) "
            "}; "
            "if ($found) { 'LOCKED' } else { 'UNLOCKED' }"
        )
        try:
            result = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", ps_command],
                capture_output=True, text=True, encoding=_console_encoding(), errors="replace", check=False,
                creationflags=CREATE_NO_WINDOW, timeout=30,
            )
            out = result.stdout.strip()
            if out == "LOCKED":
                return True
            if out == "UNLOCKED":
                return False
            logger.warning("HostsLockManager._real_locked: unexpected output %r (stderr: %s)",
                            out, result.stderr.strip())
            return None
        except Exception as e:
            logger.warning("HostsLockManager._real_locked: exception: %s", e)
            return None

    @staticmethod
    def enable(hosts_path: str = HOSTS_PATH) -> bool:
        with HostsLockManager._op_lock:
            HostsLockManager.last_error = ""
            if not _is_admin():
                HostsLockManager.last_error = T("antispy_err_no_admin")
                return False
            if not os.path.exists(hosts_path):
                HostsLockManager.last_error = T("hosts_lock_err_no_file")
                return False
            exe = _resolve_powershell_exe()
            if exe is None:
                HostsLockManager.last_error = T("antispy_err_no_admin")
                logger.error("HostsLockManager.enable: brak interpretera PowerShell")
                return False
            safe_path = hosts_path.replace("'", "''")

            ps_command = (
                f"$acl = Get-Acl -LiteralPath '{safe_path}'; "
                f"$sid = New-Object System.Security.Principal.SecurityIdentifier('{HOSTS_LOCK_SID}'); "
                "$rights = [System.Security.AccessControl.FileSystemRights]::Write -bor "
                "[System.Security.AccessControl.FileSystemRights]::Delete; "
                "$rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, $rights, 'Deny'); "
                "$acl.AddAccessRule($rule); "
                f"Set-Acl -LiteralPath '{safe_path}' -AclObject $acl"
            )
            try:
                r = subprocess.run(
                    [exe, "-NoProfile", "-NonInteractive", "-Command", ps_command],
                    capture_output=True, text=True, encoding=_console_encoding(), errors="replace", check=False,
                    creationflags=CREATE_NO_WINDOW, timeout=30,
                )
                if r.returncode != 0:
                    HostsLockManager.last_error = r.stderr.strip() or r.stdout.strip()
                    logger.error("HostsLockManager.enable: PowerShell failed (code %s): %s",
                                 r.returncode, HostsLockManager.last_error)
                    return False
                HostsLockManager._save_state(True)
                logger.info("HostsLockManager.enable: OK")
                return True
            except Exception as e:
                HostsLockManager.last_error = str(e)
                logger.exception("HostsLockManager.enable: unexpected error")
                return False

    @staticmethod
    def disable(hosts_path: str = HOSTS_PATH) -> bool:
        with HostsLockManager._op_lock:
            HostsLockManager.last_error = ""
            if not _is_admin():
                HostsLockManager.last_error = T("antispy_err_no_admin")
                return False
            exe = _resolve_powershell_exe()
            if exe is None:
                HostsLockManager.last_error = T("antispy_err_no_admin")
                logger.error("HostsLockManager.disable: brak interpretera PowerShell")
                return False
            safe_path = hosts_path.replace("'", "''")
            ps_command = (
                f"$acl = Get-Acl -LiteralPath '{safe_path}'; "
                f"$sid = New-Object System.Security.Principal.SecurityIdentifier('{HOSTS_LOCK_SID}'); "
                "$toRemove = @($acl.Access | Where-Object { $_.AccessControlType -eq 'Deny' -and "
                "($_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $sid.Value) }); "
                "foreach ($rule in $toRemove) { $acl.RemoveAccessRule($rule) | Out-Null }; "
                f"Set-Acl -LiteralPath '{safe_path}' -AclObject $acl"
            )
            try:
                r = subprocess.run(
                    [exe, "-NoProfile", "-NonInteractive", "-Command", ps_command],
                    capture_output=True, text=True, encoding=_console_encoding(), errors="replace", check=False,
                    creationflags=CREATE_NO_WINDOW, timeout=30,
                )
                if r.returncode != 0:
                    HostsLockManager.last_error = r.stderr.strip() or r.stdout.strip()
                    logger.error("HostsLockManager.disable: PowerShell failed (code %s): %s",
                                 r.returncode, HostsLockManager.last_error)
                    return False
                HostsLockManager._save_state(False)
                logger.info("HostsLockManager.disable: OK")
                return True
            except Exception as e:
                HostsLockManager.last_error = str(e)
                logger.exception("HostsLockManager.disable: unexpected error")
                return False

    @staticmethod
    def check_drift(hosts_path: str = HOSTS_PATH) -> Optional[str]:
        cached = HostsLockManager.is_active()
        real = HostsLockManager._real_locked(hosts_path)
        if real is None:
            return None
        if cached and not real:
            HostsLockManager._save_state(False)
            return "regressed"
        if not cached and real:
            HostsLockManager._save_state(True)
            return "restored"
        return None
