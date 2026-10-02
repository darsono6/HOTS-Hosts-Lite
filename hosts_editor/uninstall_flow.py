import os
import shutil
import stat
import winreg

from .constants import HOSTS_PATH, CUSTOM_DOMAINS_PATH, PROFILES_DIR
from .core import list_backups, strip_all_parental_blocks
from .core_hosts_lock import HostsLockManager
from .core_firewall import FirewallAppBlocker

REG_KEY = r"Software\HOTS Hosts Lite"

_BACKUP_PROFILES = (1, 2, 3)


def count_hosts_backups() -> int:
    total = 0
    for profile in _BACKUP_PROFILES:
        try:
            total += len(list_backups(HOSTS_PATH, profile))
        except Exception:
            pass
    return total


def custom_domains_file_exists() -> bool:
    try:
        return os.path.isfile(str(CUSTOM_DOMAINS_PATH))
    except Exception:
        return False


def count_active_firewall_blocks() -> int:
    try:
        apps = FirewallAppBlocker.list_apps()
    except Exception:
        return 0
    return sum(1 for a in apps if a.get("blocked_out") or a.get("blocked_in"))


def _delete_hosts_backups() -> int:
    deleted = 0
    for profile in _BACKUP_PROFILES:
        try:
            backups = list_backups(HOSTS_PATH, profile)
        except Exception:
            backups = []
        for bak_path, _dt in backups:
            try:
                try:
                    os.chmod(bak_path, stat.S_IWRITE)
                except OSError:
                    pass
                os.remove(bak_path)
                deleted += 1
            except Exception:
                pass
    return deleted


def _remove_all_firewall_blocks() -> list:
    failed = []
    try:
        apps = FirewallAppBlocker.list_apps()
    except Exception:
        apps = []
    for app in apps:
        if not (app.get("blocked_out") or app.get("blocked_in")):
            continue
        try:
            if not FirewallAppBlocker.remove(app["path"]):
                failed.append(app["path"])
        except Exception:
            failed.append(app["path"])
    return failed


def _delete_dir(path) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _delete_file(path) -> None:
    try:
        if os.path.isfile(path):
            try:
                os.chmod(path, stat.S_IWRITE)
            except OSError:
                pass
            os.remove(path)
    except Exception:
        pass


def _delete_key_recursive(hive, path) -> None:
    try:
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return
    try:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_key_recursive(hive, path + "\\" + sub)
    finally:
        winreg.CloseKey(key)
    try:
        winreg.DeleteKey(hive, path)
    except OSError:
        pass


def _remove_registry_keys() -> None:
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            _delete_key_recursive(hive, REG_KEY)
        except Exception:
            pass


def apply_uninstall_choices(choices: dict) -> dict:
    # Hosts lock + registry cleanup always run, even if choices is empty -
    # otherwise the hosts file could stay permanently locked.
    report = {"errors": []}

    try:
        HostsLockManager.disable()
    except Exception as e:
        report["errors"].append(f"hosts_lock: {e}")

    try:
        _remove_registry_keys()
    except Exception as e:
        report["errors"].append(f"registry: {e}")

    if choices.get("remove_blocking_config"):
        try:
            strip_all_parental_blocks(HOSTS_PATH)
        except Exception as e:
            report["errors"].append(f"hosts_strip: {e}")
        failed_fw = _remove_all_firewall_blocks()
        if failed_fw:
            report["errors"].append(f"firewall: {failed_fw}")

    if choices.get("delete_custom_domains"):
        _delete_file(str(CUSTOM_DOMAINS_PATH))

    if choices.get("delete_saved_config"):
        _delete_dir(str(PROFILES_DIR))
        # AppData intentionally not deleted here: error.log stays open, deleting it would fail.

    if choices.get("delete_hosts_backups"):
        report["backups_deleted"] = _delete_hosts_backups()

    report["delete_data"] = bool(choices.get("delete_saved_config"))
    return report
