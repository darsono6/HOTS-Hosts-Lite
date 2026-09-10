import os
import tempfile

from .constants import PROFILES_DIR
from .core import parse_hosts, entries_to_text

SLOTS = (1, 2, 3)


def _slot_path(n: int) -> str:
    if n not in SLOTS:
        raise ValueError(f"invalid profile slot: {n}")
    return os.path.join(PROFILES_DIR, f"profile_{n}.txt")


def slot_exists(n: int) -> bool:
    return os.path.isfile(_slot_path(n))


def save_slot(n: int, entries: list) -> str:
    path = _slot_path(n)
    text = entries_to_text(entries)
    os.makedirs(PROFILES_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=PROFILES_DIR, prefix=".profile_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return path


def load_slot_entries(n: int) -> list:
    path = _slot_path(n)
    if not os.path.isfile(path):
        return []
    return parse_hosts(path)
