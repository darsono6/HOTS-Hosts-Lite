import ctypes
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from ctypes import wintypes
from typing import List, Optional

from .core_hosts_lock import (
    CREATE_NO_WINDOW, HOTS_PROGRAMDATA_DIR,
    _atomic_write_json, _load_json, _is_admin,
)
from .i18n import T

logger = logging.getLogger("HOTS.firewall")


_SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
NETSH_EXE = os.path.join(_SYSTEM32, "netsh.exe")
SC_EXE = os.path.join(_SYSTEM32, "sc.exe")

FIREWALL_STATE_FILE = os.path.join(HOTS_PROGRAMDATA_DIR, "HOTS_firewall_blocked.json")
RULE_PREFIX = "HOTS_FW_"

DIRECTIONS = ("out", "in")

_DRIVE_REMOVABLE = 2
_DRIVE_REMOTE = 4
_DRIVE_CDROM = 5


def _is_removable_or_network_drive(path: str) -> bool:
    try:
        drive, _ = os.path.splitdrive(os.path.abspath(path))
        if not drive:
            return False
        root = drive if drive.endswith("\\") else drive + "\\"
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        return drive_type in (_DRIVE_REMOVABLE, _DRIVE_REMOTE, _DRIVE_CDROM)
    except Exception:
        return False


_FW_LOCAL_KEY = r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy"
_FW_POLICY_KEY = r"SOFTWARE\Policies\Microsoft\WindowsFirewall"
_FW_PROFILES = ("DomainProfile", "StandardProfile", "PublicProfile")
_FW_SERVICE = "MpsSvc"
_SERVICE_RUNNING = 4


class _SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        (name, wintypes.DWORD) for name in (
            "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
            "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint",
        )
    ]


def _service_running() -> bool:
    adv = ctypes.WinDLL("advapi32")
    adv.OpenSCManagerW.restype = ctypes.c_void_p
    adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenServiceW.restype = ctypes.c_void_p
    adv.OpenServiceW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD]
    adv.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SERVICE_STATUS)]
    adv.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    scm = svc = None
    try:
        scm = adv.OpenSCManagerW(None, None, 1)
        if not scm:
            return True
        svc = adv.OpenServiceW(scm, _FW_SERVICE, 4)
        if not svc:
            return True
        status = _SERVICE_STATUS()
        if not adv.QueryServiceStatus(svc, ctypes.byref(status)):
            return True
        return status.dwCurrentState == _SERVICE_RUNNING
    except Exception as e:
        logger.warning("_service_running: %s", e)
        return True
    finally:
        if svc:
            adv.CloseServiceHandle(svc)
        if scm:
            adv.CloseServiceHandle(scm)


def _profile_flag(base: str, profile: str) -> Optional[bool]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{profile}") as key:
            return bool(winreg.QueryValueEx(key, "EnableFirewall")[0])
    except OSError:
        return None


def _profile_enabled(profile: str) -> bool:
    for base in (_FW_POLICY_KEY, _FW_LOCAL_KEY):
        flag = _profile_flag(base, profile)
        if flag is not None:
            return flag
    return True


def _read_hots_rules() -> Optional[dict]:
    rules = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{_FW_LOCAL_KEY}\\FirewallRules") as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, data, _ = winreg.EnumValue(key, i)
                if not isinstance(data, str) or RULE_PREFIX not in data:
                    continue
                fields = dict(p.split("=", 1) for p in data.split("|") if "=" in p)
                rule = fields.get("Name", name)
                direction = fields.get("Dir", "").lower()
                if not rule.startswith(RULE_PREFIX) or direction not in DIRECTIONS:
                    continue
                rules[(rule, direction)] = {
                    "effective": fields.get("Active", "").upper() == "TRUE"
                                 and fields.get("Action", "").lower() == "block",
                    "path": fields.get("App", ""),
                }
    except OSError as e:
        logger.warning("_read_hots_rules: %s", e)
        return None
    return rules


def _rule_still_exists(rule: str, direction: str) -> bool:
    for _ in range(5):
        rules = _read_hots_rules()
        if rules is None or (rule, direction) not in rules:
            return False
        time.sleep(0.2)
    return True


def _probe_removable(paths: List[str], timeout: float = 1.0) -> dict:
    found = {p: False for p in paths}

    def probe(p: str) -> None:
        found[p] = os.path.isfile(p)

    threads = [threading.Thread(target=probe, args=(p,), daemon=True) for p in paths]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return found


# Version-named folder ("154.0.4258.48", "app-1.0.9035"); 3+ numbers required so plain folders like "3.12" never match.
_VERSION_DIR = re.compile(r"^(?P<pre>[A-Za-z]*[-_ ]?v?)(?P<ver>\d+(?:\.\d+){2,3})$")


def _version_dir_info(name: str):
    m = _VERSION_DIR.match(name)
    if not m:
        return None
    return m.group("pre").lower(), tuple(int(x) for x in m.group("ver").split("."))


def _current_exe(path: str) -> str:
    """Return the exe that represents the program across updates.

    Firewall rules match an exact path, but updaters put new builds into new version folders.
    If the exe sits in a version folder: prefer the same-named exe in the parent folder
    (stable entry point), otherwise take the newest sibling version folder containing it.
    Anything else is returned unchanged.
    """
    folder, exe = os.path.split(path)
    info = _version_dir_info(os.path.basename(folder))
    if info is None:
        return path
    parent = os.path.dirname(folder)
    stable = os.path.join(parent, exe)
    if os.path.isfile(stable):
        return stable
    try:
        names = os.listdir(parent)
    except OSError:
        return path
    best, best_ver = None, ()
    for d in names:
        other = _version_dir_info(d)
        if other is None or other[0] != info[0] or other[1] <= best_ver:
            continue
        cand = os.path.join(parent, d, exe)
        if os.path.isfile(cand):
            best, best_ver = cand, other[1]
    return best or path


def _rule_name(norm_path: str) -> str:
    digest = hashlib.md5(norm_path.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:12]
    return f"{RULE_PREFIX}{digest}"


def _oem_encoding() -> str:
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"


_NETSH_ENCODING = _oem_encoding()


def _netsh_error() -> str:
    if not os.path.isfile(NETSH_EXE) and shutil.which("netsh") is None:
        return T("fw_err_netsh_missing")
    return T("fw_err_netsh_failed")


def _run_netsh(args: List[str]) -> Optional[subprocess.CompletedProcess]:
    exe = NETSH_EXE if os.path.isfile(NETSH_EXE) else "netsh"
    try:
        return subprocess.run(
            [exe, *args],
            capture_output=True, text=True, encoding=_NETSH_ENCODING, errors="replace", check=False,
            creationflags=CREATE_NO_WINDOW, timeout=20,
        )
    except Exception as e:
        logger.warning("_run_netsh: exception running %s: %s", args, e)
        return None


class FirewallAppBlocker:

    last_error: str = ""
    _op_lock = threading.RLock()

    @staticmethod
    def is_enabled() -> bool:
        return _service_running() and all(_profile_enabled(p) for p in _FW_PROFILES)

    @staticmethod
    def enable_firewall() -> bool:
        with FirewallAppBlocker._op_lock:
            FirewallAppBlocker.last_error = ""
            if not _is_admin():
                FirewallAppBlocker.last_error = T("antispy_err_no_admin")
                return False
            if not _service_running():
                try:
                    subprocess.run(
                        [SC_EXE if os.path.isfile(SC_EXE) else "sc", "start", _FW_SERVICE],
                        capture_output=True, check=False,
                        creationflags=CREATE_NO_WINDOW, timeout=20,
                    )
                except Exception as e:
                    logger.warning("FirewallAppBlocker.enable_firewall: sc start failed: %s", e)
                for _ in range(20):
                    if _service_running():
                        break
                    time.sleep(0.5)
            r = _run_netsh(["advfirewall", "set", "allprofiles", "state", "on"])
            if r is None:
                FirewallAppBlocker.last_error = _netsh_error()
                return False
            if r.returncode != 0:
                logger.warning("FirewallAppBlocker.enable_firewall: netsh returncode %s: %s",
                               r.returncode, (r.stderr or r.stdout).strip())
            if not FirewallAppBlocker.is_enabled():
                FirewallAppBlocker.last_error = T("fw_err_enable_failed")
                return False
            logger.info("FirewallAppBlocker.enable_firewall: OK")
            return True

    @staticmethod
    def _follow_updates() -> bool:
        apps = FirewallAppBlocker._load_state()["apps"]
        moves = []
        for entry in apps.values():
            path = entry.get("path", "")
            if not path or entry.get("removable"):
                continue
            new = _current_exe(path)
            if os.path.normcase(new) != os.path.normcase(path):
                dirs = [d for d in DIRECTIONS if entry.get(f"blocked_{d}")]
                moves.append((path, new, dirs))

        moved = False
        for old, new, dirs in moves:
            if dirs and not _is_admin():
                continue
            if not dirs and not FirewallAppBlocker.add(new):
                continue
            if not all(FirewallAppBlocker.block(new, d) for d in dirs):
                logger.warning("FirewallAppBlocker._follow_updates: cannot block %s: %s",
                               new, FirewallAppBlocker.last_error)
                continue
            if not all(FirewallAppBlocker.unblock(old, d) for d in dirs):
                continue
            if FirewallAppBlocker.remove(old):
                moved = True
                logger.info("FirewallAppBlocker._follow_updates: %s -> %s", old, new)
        return moved

    @staticmethod
    def check_drift() -> Optional[str]:
        with FirewallAppBlocker._op_lock:
            moved = FirewallAppBlocker._follow_updates()
            rules = _read_hots_rules()
            if rules is None:
                return "updated" if moved else None
            apps = FirewallAppBlocker._load_state()["apps"]
            by_rule = {
                (e.get("rule") or _rule_name(os.path.normcase(e.get("path", "")))): e
                for e in apps.values()
            }
            regressed = restored = False
            for rule, entry in by_rule.items():
                for direction in DIRECTIONS:
                    flag = f"blocked_{direction}"
                    info = rules.get((rule, direction))
                    if entry.get(flag) and not (info and info["effective"]):
                        entry[flag] = False
                        regressed = True
            for (rule, direction), info in rules.items():
                if not info["effective"] or not info["path"]:
                    continue
                entry = by_rule.get(rule)
                if entry is None:
                    path = os.path.abspath(info["path"])
                    key = os.path.normcase(path)
                    if key in apps:
                        continue
                    entry = apps[key] = {
                        "path": path, "rule": rule,
                        "blocked_out": False, "blocked_in": False,
                        "removable": _is_removable_or_network_drive(path),
                    }
                    by_rule[rule] = entry
                flag = f"blocked_{direction}"
                if not entry.get(flag):
                    entry[flag] = True
                    restored = True
            if regressed or restored:
                FirewallAppBlocker._save_state(apps)
            if regressed:
                return "regressed"
            if any(e.get("blocked_out") or e.get("blocked_in") for e in apps.values()) \
                    and not FirewallAppBlocker.is_enabled():
                return "disabled"
            if restored:
                return "restored"
            return "updated" if moved else None

    @staticmethod
    def _load_state() -> dict:
        data = _load_json(FIREWALL_STATE_FILE) or {"apps": {}}
        data.setdefault("apps", {})
        apps = data["apps"]

        migrated = False
        for entry in apps.values():
            if "blocked_out" not in entry or "blocked_in" not in entry:
                old = bool(entry.get("blocked"))
                entry["blocked_out"] = old
                entry["blocked_in"] = old
                entry.pop("blocked", None)
                migrated = True
        if migrated:
            FirewallAppBlocker._save_state(apps)

        return data

    @staticmethod
    def _save_state(apps: dict) -> None:
        _atomic_write_json(FIREWALL_STATE_FILE, {"apps": apps})

    @staticmethod
    def list_apps() -> List[dict]:
        apps = FirewallAppBlocker._load_state()["apps"]
        entries = [e for e in apps.values() if e.get("path")]
        found = _probe_removable([e["path"] for e in entries if e.get("removable")])
        items = [
            {
                "path": e["path"], "name": os.path.basename(e["path"]),
                "blocked_out": bool(e.get("blocked_out")),
                "blocked_in": bool(e.get("blocked_in")),
                "missing": not (found[e["path"]] if e.get("removable") else os.path.isfile(e["path"])),
                "removable": bool(e.get("removable", False)),
            }
            for e in entries
        ]
        items.sort(key=lambda e: e["name"].lower())
        return items

    @staticmethod
    def is_added(path: str) -> bool:
        key = os.path.normcase(_current_exe(os.path.abspath(path)))
        return key in FirewallAppBlocker._load_state()["apps"]


    @staticmethod
    def add(path: str) -> bool:
        with FirewallAppBlocker._op_lock:
            FirewallAppBlocker.last_error = ""
            if not FirewallAppBlocker.is_enabled():
                FirewallAppBlocker.last_error = T("fw_err_disabled")
                return False
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                FirewallAppBlocker.last_error = T("fw_err_no_file")
                return False
            path = _current_exe(path)

            key = os.path.normcase(path)
            state = FirewallAppBlocker._load_state()
            apps = state["apps"]
            if key not in apps:
                apps[key] = {
                    "path": path, "rule": _rule_name(key),
                    "blocked_out": False, "blocked_in": False,
                    "removable": _is_removable_or_network_drive(path),
                }
                FirewallAppBlocker._save_state(apps)
            return True

    @staticmethod
    def remove(path: str) -> bool:
        with FirewallAppBlocker._op_lock:
            FirewallAppBlocker.last_error = ""
            key = os.path.normcase(os.path.abspath(path))
            state = FirewallAppBlocker._load_state()
            apps = state["apps"]
            entry = apps.get(key)

            blocked_dirs = [d for d in DIRECTIONS if entry and entry.get(f"blocked_{d}")]
            if blocked_dirs:
                if not _is_admin():
                    FirewallAppBlocker.last_error = T("antispy_err_no_admin")
                    return False
                rule = entry.get("rule") or _rule_name(key)
                for direction in blocked_dirs:
                    r = _run_netsh(["advfirewall", "firewall", "delete", "rule",
                                    f"name={rule}", f"dir={direction}"])
                    if r is None:
                        FirewallAppBlocker.last_error = _netsh_error()
                        return False
                    if r.returncode != 0:
                        if not _service_running():
                            FirewallAppBlocker.last_error = T("fw_err_service_stopped")
                            return False
                        logger.warning(
                            "FirewallAppBlocker.remove: netsh returncode %s for %s (%s): %s",
                            r.returncode, path, direction, (r.stderr or r.stdout).strip(),
                        )
                    if _rule_still_exists(rule, direction):
                        FirewallAppBlocker.last_error = T("fw_err_remove_failed")
                        return False
                    entry[f"blocked_{direction}"] = False
                    FirewallAppBlocker._save_state(apps)

            if key in apps:
                del apps[key]
                FirewallAppBlocker._save_state(apps)
            return True


    @staticmethod
    def block(path: str, direction: str) -> bool:
        with FirewallAppBlocker._op_lock:
            FirewallAppBlocker.last_error = ""
            if direction not in DIRECTIONS:
                raise ValueError(f"invalid direction: {direction!r}")
            if not _is_admin():
                FirewallAppBlocker.last_error = T("antispy_err_no_admin")
                return False
            if not FirewallAppBlocker.is_enabled():
                FirewallAppBlocker.last_error = T("fw_err_disabled")
                return False
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                FirewallAppBlocker.last_error = T("fw_err_no_file")
                return False

            key = os.path.normcase(path)
            state = FirewallAppBlocker._load_state()
            apps = state["apps"]
            entry = apps.get(key)
            flag = f"blocked_{direction}"
            if entry and entry.get(flag):
                return True

            rule = (entry.get("rule") if entry else None) or _rule_name(key)
            r = _run_netsh([
                "advfirewall", "firewall", "add", "rule",
                f"name={rule}", f"dir={direction}", "action=block",
                f"program={path}", "enable=yes",
            ])
            if r is None or r.returncode != 0:
                FirewallAppBlocker.last_error = (
                    (r.stderr or r.stdout).strip() if r is not None else _netsh_error()
                )
                logger.error("FirewallAppBlocker.block: netsh failed (dir=%s): %s",
                             direction, FirewallAppBlocker.last_error)
                return False

            if entry is None:
                entry = {
                    "path": path, "rule": rule,
                    "blocked_out": False, "blocked_in": False,
                    "removable": _is_removable_or_network_drive(path),
                }
            entry["rule"] = rule
            entry[flag] = True
            apps[key] = entry
            FirewallAppBlocker._save_state(apps)
            logger.info("FirewallAppBlocker.block: OK (%s, dir=%s)", path, direction)
            return True

    @staticmethod
    def unblock(path: str, direction: str) -> bool:
        with FirewallAppBlocker._op_lock:
            FirewallAppBlocker.last_error = ""
            if direction not in DIRECTIONS:
                raise ValueError(f"invalid direction: {direction!r}")
            if not _is_admin():
                FirewallAppBlocker.last_error = T("antispy_err_no_admin")
                return False

            key = os.path.normcase(os.path.abspath(path))
            state = FirewallAppBlocker._load_state()
            apps = state["apps"]
            entry = apps.get(key)
            rule = (entry.get("rule") if entry else None) or _rule_name(key)

            r = _run_netsh(["advfirewall", "firewall", "delete", "rule",
                            f"name={rule}", f"dir={direction}"])
            if r is None:
                FirewallAppBlocker.last_error = _netsh_error()
                return False
            if r.returncode != 0:
                if not _service_running():
                    FirewallAppBlocker.last_error = T("fw_err_service_stopped")
                    return False
                logger.warning("FirewallAppBlocker.unblock: netsh returncode %s for %s (%s): %s",
                                r.returncode, path, direction, (r.stderr or r.stdout).strip())
            if _rule_still_exists(rule, direction):
                FirewallAppBlocker.last_error = T("fw_err_remove_failed")
                return False

            if entry is not None:
                entry[f"blocked_{direction}"] = False
                apps[key] = entry
                FirewallAppBlocker._save_state(apps)
            logger.info("FirewallAppBlocker.unblock: OK (%s, dir=%s)", path, direction)
            return True


    @staticmethod
    def block_all(path: str) -> bool:
        with FirewallAppBlocker._op_lock:
            for direction in DIRECTIONS:
                if not FirewallAppBlocker.block(path, direction):
                    return False
            return True

    @staticmethod
    def unblock_all(path: str) -> bool:
        with FirewallAppBlocker._op_lock:
            for direction in DIRECTIONS:
                if not FirewallAppBlocker.unblock(path, direction):
                    return False
            return True
