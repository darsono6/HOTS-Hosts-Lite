"""DNS speed test.

The queries are sent straight to the DNS servers over UDP, so the hosts file and the system
DNS cache (which would make every answer instant) are bypassed - that is what is being
measured: how fast the servers answer.
"""
import ipaddress
import json
import os
import random
import re
import socket
import struct
import subprocess
import sys
import threading
import time

DOMAINS = ("google.com", "youtube.com", "facebook.com", "wikipedia.org",
           "amazon.com", "microsoft.com", "netflix.com", "apple.com")
PUBLIC = (("cloudflare", "Cloudflare", "1.1.1.1"),
          ("google", "Google", "8.8.8.8"),
          ("quad9", "Quad9", "9.9.9.9"),
          ("opendns", "OpenDNS", "208.67.222.222"))
# The provider's second address, set together with the main one.
ALTERNATE = {"cloudflare": "1.0.0.1", "google": "8.8.4.4",
             "quad9": "149.112.112.112", "opendns": "208.67.220.220"}
ROUNDS = 3
TIMEOUT = 2.0
_GIVE_UP_AFTER = 3    # a server that answers none of its first queries is declared silent
_SERVER_DEADLINE = 12.0
FAST_MS = 30


def _win_types():
    import ctypes
    from ctypes import wintypes

    class IP_ADDR_STRING(ctypes.Structure):
        pass

    IP_ADDR_STRING._fields_ = [("Next", ctypes.POINTER(IP_ADDR_STRING)),
                               ("IpAddress", ctypes.c_char * 16),
                               ("IpMask", ctypes.c_char * 16),
                               ("Context", wintypes.DWORD)]

    class FIXED_INFO(ctypes.Structure):
        _fields_ = [("HostName", ctypes.c_char * 132),
                    ("DomainName", ctypes.c_char * 132),
                    ("CurrentDnsServer", ctypes.POINTER(IP_ADDR_STRING)),
                    ("DnsServerList", IP_ADDR_STRING),
                    ("NodeType", wintypes.UINT),
                    ("ScopeId", ctypes.c_char * 260),
                    ("EnableRouting", wintypes.UINT),
                    ("EnableProxy", wintypes.UINT),
                    ("EnableDns", wintypes.UINT)]

    return IP_ADDR_STRING, FIXED_INFO


def _windows_dns_servers():
    import ctypes
    from ctypes import wintypes

    _, FIXED_INFO = _win_types()
    func = ctypes.windll.iphlpapi.GetNetworkParams
    size = wintypes.ULONG(0)
    func(None, ctypes.byref(size))  # ERROR_BUFFER_OVERFLOW: tells how big the buffer must be
    buf = ctypes.create_string_buffer(size.value)
    if func(buf, ctypes.byref(size)) != 0:
        return []
    node = ctypes.cast(buf, ctypes.POINTER(FIXED_INFO)).contents.DnsServerList
    servers = []
    while True:
        ip = node.IpAddress.decode("ascii", "ignore").strip()
        if ip and ip != "0.0.0.0" and ip not in servers:
            servers.append(ip)
        if not node.Next:
            break
        node = node.Next.contents
    return servers


def _resolv_conf_servers(path="/etc/resolv.conf"):
    servers = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver" and "%" not in parts[1]:
                    servers.append(parts[1])
    except OSError:
        pass
    return servers


def system_dns_servers():
    """DNS servers configured in the system (primary first); [] if they cannot be read."""
    try:
        if sys.platform == "win32":
            return _windows_dns_servers()
        return _resolv_conf_servers()
    except Exception:
        return []


def build_servers(system_addr=None):
    servers = []
    public = list(PUBLIC)
    if system_addr:
        provider = next((name for _, name, addr in PUBLIC if addr == system_addr), "")
        servers.append({"key": "system", "name": "", "provider": provider, "addr": system_addr, "port": 53})
        public = [p for p in PUBLIC if p[2] != system_addr]  # already being tested as "your DNS"
    for key, name, addr in public:
        servers.append({"key": key, "name": name, "provider": "", "addr": addr, "port": 53})
    return servers


def _build_query(name: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired, 1 question
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in name.split(".")) + b"\x00"
    return header + qname + struct.pack(">HH", 1, 1)  # type A, class IN


def query_dns(addr: str, port: int = 53, name: str = "google.com", timeout=None):
    """Milliseconds until a good answer arrives, or None (no answer, error answer, network error)."""
    timeout = TIMEOUT if timeout is None else timeout
    qid = random.getrandbits(16)
    packet = _build_query(name, qid)
    sock = socket.socket(socket.AF_INET6 if ":" in addr else socket.AF_INET, socket.SOCK_DGRAM)
    try:
        t0 = time.perf_counter()
        deadline = t0 + timeout
        sock.sendto(packet, (addr, port))
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            data, _ = sock.recvfrom(4096)
            ms = (time.perf_counter() - t0) * 1000.0
            if len(data) < 12 or struct.unpack(">H", data[:2])[0] != qid:
                continue  # somebody else's packet
            flags = struct.unpack(">H", data[2:4])[0]
            return ms if (flags & 0x8000) and (flags & 0x000F) == 0 else None  # response, no error
    except OSError:
        return None
    finally:
        sock.close()


def probe_server(server, should_stop=lambda: False):
    times, lost, total = [], 0, 0
    planned = ROUNDS * len(DOMAINS)
    streak = 0
    started = time.monotonic()
    stopped = False
    for _ in range(ROUNDS):
        for domain in DOMAINS:
            if should_stop() or time.monotonic() - started > _SERVER_DEADLINE:
                stopped = True
                break
            total += 1
            ms = query_dns(server["addr"], server.get("port", 53), domain)
            if ms is None:
                lost += 1
                streak += 1
            else:
                times.append(ms)
                streak = 0
            if not times and streak >= _GIVE_UP_AFTER:
                lost += planned - total  # silent server: do not wait for the remaining queries
                total = planned
                stopped = True
                break
        if stopped:
            break
    ordered = sorted(times)
    return {"key": server["key"], "name": server["name"], "provider": server.get("provider", ""),
            "addr": server["addr"], "default": bool(server.get("default")),
            "median": ordered[len(ordered) // 2] if ordered else None,
            "best": ordered[0] if ordered else None,
            "worst": ordered[-1] if ordered else None,
            "lost": lost, "total": total}


def run_dns_test(servers, should_stop, on_row):
    """Tests all servers in parallel (each one sequentially). on_row(index, stats) fires as each
    finishes. Returns the stats in the order of `servers`, None for a server cut short by stop."""
    results = [None] * len(servers)

    def work(i):
        stats = probe_server(servers[i], should_stop)
        if should_stop():
            return
        results[i] = stats
        on_row(i, stats)

    threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(servers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def verdict(rows):
    """(kind, data) summing up the result, for the user's own question: is slow loading the
    fault of DNS? rows: stats dicts as returned above (key "system" = the user's DNS)."""
    system = next((r for r in rows if r["key"] == "system"), None)
    public = [r for r in rows if r["key"] != "system" and r["median"] is not None]
    best = min(public, key=lambda r: r["median"]) if public else None

    if system is None:
        return ("unknown", {}) if best else ("offline", {})
    if system["median"] is None:
        return ("none", {}) if best else ("offline", {})
    if system["median"] <= FAST_MS:
        return "fast", {"ms": system["median"]}
    if best is None:
        return "alone", {}
    gain = system["median"] - best["median"]
    if gain >= 20 and system["median"] >= 1.5 * best["median"]:
        return "better", {"name": best["name"], "gain": gain}
    return "same", {}


_PS_HEAD = (
    "$ErrorActionPreference='Stop';"
    "$c=Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' }"
    " | Select-Object -First 1;"
    "if(-not $c){ exit 3 };"
)


def _powershell(script: str):
    """CompletedProcess of the script, or an error text when it could not be run at all."""
    from .core_hosts_lock import CREATE_NO_WINDOW, _resolve_powershell_exe
    exe = _resolve_powershell_exe()
    if not exe:
        return "PowerShell not found"
    try:
        return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    except Exception as exc:
        return str(exc)


def _run_dns_script(action: str) -> str:
    """Runs `action` (PowerShell) against the adapter that carries the default route, i.e. the
    one in use. Needs administrator rights. Returns "" on success, otherwise a short error text."""
    res = _powershell(_PS_HEAD + action + ";Clear-DnsClientCache")
    if isinstance(res, str):
        return res
    if res.returncode == 3:
        return "no active network adapter"
    return "" if res.returncode == 0 else (res.stderr or res.stdout or f"exit code {res.returncode}").strip()[:300]


def system_dns_is_default():
    """True when the DNS of the adapter in use is assigned automatically (router / provider),
    False when it was set by hand, None if that cannot be told."""
    if sys.platform != "win32":
        return None
    res = _powershell(_PS_HEAD + "$n=(Get-ItemProperty -Path \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip"
                      "\\Parameters\\Interfaces\\$($c.NetAdapter.InterfaceGuid)\").NameServer;"
                      "if($n){'static'}else{'auto'}")
    if isinstance(res, str) or res.returncode != 0:
        return None
    return {"auto": True, "static": False}.get(res.stdout.strip())


_GUID_RE = re.compile(r"^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$")


def _backup_path() -> str:
    from .core_hosts_lock import HOTS_PROGRAMDATA_DIR
    return os.path.join(HOTS_PROGRAMDATA_DIR, "HOTS_dns_backup.json")


def _load_backup() -> dict:
    # {adapter GUID: static IPv4 servers}; an empty list means the adapter used automatic DNS.
    try:
        with open(_backup_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for guid, servers in raw.items() if isinstance(raw, dict) else ():
        try:
            if _GUID_RE.match(guid) and isinstance(servers, list):
                out[guid] = [str(ipaddress.IPv4Address(s)) for s in servers]
        except (ValueError, TypeError):
            pass
    return out


def _save_backup(backup: dict) -> None:
    from .core_hosts_lock import _atomic_write_json
    if backup:
        _atomic_write_json(_backup_path(), backup)
        return
    try:
        os.remove(_backup_path())
    except FileNotFoundError:
        pass


def dns_backup_exists() -> bool:
    return bool(_load_backup())


def _current_dns_state():
    # (adapter GUID, static servers) of the adapter in use; None if it cannot be read exactly.
    res = _powershell(_PS_HEAD + "$g=$c.NetAdapter.InterfaceGuid;"
                      "$n=(Get-ItemProperty -Path \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip"
                      "\\Parameters\\Interfaces\\$g\").NameServer;\"$g|$n\"")
    if isinstance(res, str) or res.returncode != 0:
        return None
    guid, _, names = res.stdout.strip().partition("|")
    if not _GUID_RE.match(guid):
        return None
    servers = []
    for token in re.split(r"[,\s]+", names.strip()):
        if token:
            try:
                servers.append(str(ipaddress.IPv4Address(token)))
            except ValueError:
                return None
    return guid, servers


def _remember_original_dns():
    # Keeps the DNS the adapter had before the app first changed it, so it can be put back.
    # Returns the GUID when a new entry was saved, so a failed change can undo it.
    state = _current_dns_state()
    if state is None:
        return None
    guid, servers = state
    backup = _load_backup()
    if guid in backup:
        return None
    backup[guid] = servers
    try:
        _save_backup(backup)
    except Exception:
        return None
    return guid


def set_system_dns(primary: str, alternate: str) -> str:
    primary, alternate = str(ipaddress.IPv4Address(primary)), str(ipaddress.IPv4Address(alternate))
    saved = _remember_original_dns()
    err = _run_dns_script(f"Set-DnsClientServerAddress -InterfaceIndex $c.InterfaceIndex "
                          f"-ServerAddresses ('{primary}','{alternate}')")
    if err and saved:
        backup = _load_backup()
        backup.pop(saved, None)
        try:
            _save_backup(backup)
        except Exception:
            pass
    return err


def saved_original_servers() -> list:
    for servers in _load_backup().values():
        if servers:
            return servers
    return []


def restore_original_dns(to_automatic: bool = False) -> str:
    backup = _load_backup()
    if not backup:
        return ""
    script = "$ErrorActionPreference='Stop';"
    for guid, servers in backup.items():
        addresses = ("-ServerAddresses (" + ",".join(f"'{s}'" for s in servers) + ")") \
            if servers and not to_automatic else "-ResetServerAddresses"
        script += (f"$a=Get-NetAdapter | Where-Object {{ $_.InterfaceGuid -eq '{guid}' }};"
                   f"if($a){{ Set-DnsClientServerAddress -InterfaceIndex $a.InterfaceIndex {addresses} }};")
    res = _powershell(script + "Clear-DnsClientCache")
    if isinstance(res, str):
        return res
    if res.returncode != 0:
        return (res.stderr or res.stdout or f"exit code {res.returncode}").strip()[:300]
    _save_backup({})
    return ""


def reset_system_dns(to_automatic: bool = False) -> str:
    if _load_backup():
        return restore_original_dns(to_automatic)
    return _run_dns_script("Set-DnsClientServerAddress -InterfaceIndex $c.InterfaceIndex -ResetServerAddresses")
