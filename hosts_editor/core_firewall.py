import ctypes
import hashlib
import logging
import os
import subprocess
import threading
from typing import List, Optional

from .core_hosts_lock import (
    CREATE_NO_WINDOW, HOTS_PROGRAMDATA_DIR,
    _atomic_write_json, _load_json, _is_admin,
)
from .i18n import T

logger = logging.getLogger("HOTS.firewall")


_SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
NETSH_EXE = os.path.join(_SYSTEM32, "netsh.exe")

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


def _rule_name(norm_path: str) -> str:
    digest = hashlib.md5(norm_path.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"{RULE_PREFIX}{digest}"


def _run_netsh(args: List[str]) -> Optional[subprocess.CompletedProcess]:
    exe = NETSH_EXE if os.path.isfile(NETSH_EXE) else "netsh"
    try:
        return subprocess.run(
            [exe, *args],
            capture_output=True, text=True, errors="replace", check=False,
            creationflags=CREATE_NO_WINDOW, timeout=20,
        )
    except Exception as e:
        logger.warning("_run_netsh: exception running %s: %s", args, e)
        return None


class FirewallError(Exception):
    pass


class FirewallAppBlocker:

    last_error: str = ""
    _op_lock = threading.RLock()


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
        items = [
            {
                "path": e["path"], "name": os.path.basename(e["path"]),
                "blocked_out": bool(e.get("blocked_out")),
                "blocked_in": bool(e.get("blocked_in")),
                "missing": not os.path.isfile(e["path"]),
                "removable": bool(e.get("removable", False)),
            }
            for e in apps.values() if e.get("path")
        ]
        items.sort(key=lambda e: e["name"].lower())
        return items

    @staticmethod
    def is_added(path: str) -> bool:
        key = os.path.normcase(os.path.abspath(path))
        return key in FirewallAppBlocker._load_state()["apps"]


    @staticmethod
    def add(path: str) -> bool:
        with FirewallAppBlocker._op_lock:
            FirewallAppBlocker.last_error = ""
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                FirewallAppBlocker.last_error = T("fw_err_no_file")
                return False

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
                        FirewallAppBlocker.last_error = T("fw_err_netsh_missing")
                        return False
                    if r.returncode != 0:
                        logger.warning(
                            "FirewallAppBlocker.remove: netsh returncode %s for %s (%s): %s",
                            r.returncode, path, direction, (r.stderr or r.stdout).strip(),
                        )

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
                    (r.stderr or r.stdout).strip() if r is not None else T("fw_err_netsh_missing")
                )
                logger.error("FirewallAppBlocker.block: netsh failed (dir=%s): %s",
                             direction, FirewallAppBlocker.last_error)
                return False

            if entry is None:
                entry = {"path": path, "rule": rule, "blocked_out": False, "blocked_in": False}
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
                FirewallAppBlocker.last_error = T("fw_err_netsh_missing")
                return False
            if r.returncode != 0:
                logger.warning("FirewallAppBlocker.unblock: netsh returncode %s for %s (%s): %s",
                                r.returncode, path, direction, (r.stderr or r.stdout).strip())

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
