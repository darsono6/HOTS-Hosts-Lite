import logging
import os
from typing import Dict, List

import winreg

logger = logging.getLogger("HOTS.installed_apps")

_UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

_SKIP_RELEASE_TYPES = {"Update", "Hotfix", "Security Update", "Update Rollup", "Driver Update"}


def _reg_value(key, name: str):
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _clean_icon_path(raw: str) -> str:
    raw = raw.strip().strip('"')
    head, sep, tail = raw.rpartition(",")
    if sep and tail.lstrip("-").isdigit():
        raw = head
    return raw.strip().strip('"')


_JUNK_EXE_HINTS = ("unins", "uninstall", "setup", "updater")


def _find_exe_in_folder(folder: str, hint_name: str) -> str:
    try:
        entries = sorted(f for f in os.listdir(folder) if f.lower().endswith(".exe"))
    except OSError:
        return ""
    if not entries:
        return ""
    hint = hint_name.lower()
    for f in entries:
        stem = os.path.splitext(f)[0].lower()
        if stem and (stem in hint or hint.startswith(stem)):
            return os.path.join(folder, f)
    non_junk = [f for f in entries if not any(j in f.lower() for j in _JUNK_EXE_HINTS)]
    pick = non_junk[0] if non_junk else entries[0]
    return os.path.join(folder, pick)


def _resolve_exe(display_name: str, icon_raw: str, install_location: str) -> str:
    if icon_raw:
        candidate = _clean_icon_path(icon_raw)
        if candidate.lower().endswith(".exe") and os.path.isfile(candidate):
            return candidate
    if install_location and os.path.isdir(install_location):
        found = _find_exe_in_folder(install_location, display_name)
        if found and os.path.isfile(found):
            return found
    return ""


def scan_installed_apps() -> List[Dict[str, str]]:
    seen_paths = set()
    results: List[Dict[str, str]] = []

    for hive, subkey in _UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub_name) as sk:
                        display_name = _reg_value(sk, "DisplayName")
                        if not display_name:
                            continue
                        if _reg_value(sk, "SystemComponent") == 1:
                            continue
                        if _reg_value(sk, "ParentKeyName"):
                            continue
                        if str(_reg_value(sk, "ReleaseType") or "") in _SKIP_RELEASE_TYPES:
                            continue

                        exe = _resolve_exe(
                            str(display_name),
                            str(_reg_value(sk, "DisplayIcon") or ""),
                            str(_reg_value(sk, "InstallLocation") or ""),
                        )
                        if not exe:
                            continue
                        key_path = os.path.normcase(exe)
                        if key_path in seen_paths:
                            continue
                        seen_paths.add(key_path)
                        results.append({"name": str(display_name), "path": exe})
                except OSError:
                    continue
                except Exception:
                    logger.debug("scan_installed_apps: skipping %s (unexpected error)", sub_name, exc_info=True)
                    continue

    results.sort(key=lambda e: e["name"].lower())
    return results
