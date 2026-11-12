import http.client
import select
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Cloudflare's anycast network routes the client to the nearest data center anywhere in the
# world. 50 MB per download request is known to work (100 MB got HTTP 403), 25 MB upload bodies too.
CLOUDFLARE = {
    "name": "Cloudflare",
    "down": "https://speed.cloudflare.com/__down?bytes=50000000",
    "up": "https://speed.cloudflare.com/__up", "up_method": "POST",
}
DURATION = 8.0
_UPLOAD_BODY = 25_000_000
_THREADS = 4
_WARMUP = 1.0
_SAMPLE_EVERY = 0.25
_SMOOTH_WINDOW = 0.5
_UA = "HOTS-SpeedTest"
_MAX_FAILS = 3
_PROBE_EVERY = 0.4
_PROBE_TIMEOUT = 3.0
_PROBE_MIN_SAMPLES = 3
_PARTIAL_MIN = 1.5
_STALL_CUT = 3.0
_PING_FAIL_PASSES = 2
_READ_CHUNK = 16384
_STALL_MS = 900.0
_PING_WARMUP = 3
_PING_SAMPLES = 12
_PING_GAP = 0.1


class SpeedTestError(Exception):
    pass


def _retry_after(headers):
    try:
        v = (headers.get("Retry-After") or "").strip() if headers else ""
    except Exception:
        return None
    return min(int(v), 3600) if v.isdigit() else None


def _error_text(state) -> str:
    msg = str(state["err"] or "no data")
    if state["retry_after"]:
        msg += f" [retry-after={state['retry_after']}]"
    return msg


def _endpoint(url: str):
    u = urllib.parse.urlsplit(url)
    return u.hostname, (u.port or (443 if u.scheme == "https" else 80))


def _connect_ms(info, timeout: float):
    family, stype, proto, _, addr = info
    box = []

    def run():
        s = socket.socket(family, stype, proto)
        try:
            t0 = time.perf_counter()
            s.connect(addr)
            box.append((time.perf_counter() - t0) * 1000.0)
        except OSError:
            pass
        finally:
            s.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else None


def measure_ping(host: str, port: int, count: int = _PING_SAMPLES, timeout: float = 3.0, should_stop=None,
                 chosen_out=None):
    """(median TCP connect time in ms, jitter in ms or None); DNS is resolved once and not counted.
    Jitter is the median difference between consecutive samples. IPv4 addresses are tried first;
    the first one that answers is used for all samples."""
    try:
        infos = sorted(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                       key=lambda i: i[0] != socket.AF_INET)
    except OSError as exc:
        raise SpeedTestError(f"DNS: {exc}")
    chosen = None
    first = None
    warmed = 0
    failed = 0
    times = []
    for _ in range(_PING_WARMUP + count):
        if should_stop and should_stop():
            break
        for info in ([chosen] if chosen else infos):
            if should_stop and should_stop():
                break
            ms = _connect_ms(info, timeout)
            if ms is not None:
                chosen = info
                if first is None:
                    first = ms
                if warmed < _PING_WARMUP:
                    warmed += 1
                else:
                    times.append(ms)
                break
        if chosen is None:
            failed += 1
            if failed >= _PING_FAIL_PASSES:
                break
        time.sleep(_PING_GAP)
    if chosen_out is not None and chosen is not None:
        chosen_out.append(chosen)
    if not times and first is not None:
        times = [first]
    if not times:
        if should_stop and should_stop():
            return 0.0, None
        raise SpeedTestError(f"cannot connect to {host}:{port}")
    jitter = None
    if len(times) >= 3:
        diffs = sorted(abs(b - a) for a, b in zip(times, times[1:]))
        jitter = diffs[len(diffs) // 2]
    return sorted(times)[len(times) // 2], jitter


def _loaded_args(entry):
    if not entry:
        return None, None
    median, stalled, total = entry
    return median, ((stalled, total) if total else None)


def _probe_target(host: str, port: int):
    try:
        infos = sorted(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                       key=lambda i: i[0] != socket.AF_INET)
    except OSError:
        return None
    return infos[0] if infos else None


def _probe_once(info, rec):
    ms = _connect_ms(info, _PROBE_TIMEOUT)
    rec[1] = -1.0 if ms is None else ms


def _probe_loop(info, done, samples, lock):
    started = time.perf_counter()
    while not done.wait(_PROBE_EVERY):
        if time.perf_counter() - started < _WARMUP:
            continue
        rec = [time.perf_counter(), None]
        with lock:
            samples.append(rec)
        threading.Thread(target=_probe_once, args=(info, rec), daemon=True).start()


def _ssl_context(url: str):
    if not url.lower().startswith("https"):
        return None
    try:
        from .dialogs._about_shared import _build_ssl_context
        return _build_ssl_context()
    except Exception:
        import ssl
        return ssl.create_default_context()


def _download_worker(url, ctx):
    def worker(done, add, state, mark):
        fails = 0
        while not done.is_set():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA, "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                    mark("ok")
                    while not done.is_set():
                        chunk = resp.read(_READ_CHUNK)
                        if not chunk:
                            break
                        add(len(chunk))
            except urllib.error.HTTPError as exc:
                # the server said no (e.g. 429) - do not hammer it with retries
                state["err"] = exc
                state["retry_after"] = _retry_after(exc.headers)
                mark("dead")
                return
            except Exception as exc:
                state["err"] = exc
                fails += 1
                if fails >= _MAX_FAILS:
                    mark("dead")
                    return
                if done.wait(1.0):
                    return
    return worker


def _rejection(conn):
    """If the server already answered with an error status (it may have closed the
    connection while we were still sending), return (text, retry_after), else ("", None)."""
    try:
        resp = conn.getresponse()
        if resp.status >= 400:
            return f"HTTP Error {resp.status}: {resp.reason}", _retry_after(resp.headers)
    except Exception:
        pass
    return "", None


def _upload_worker(url, ctx, method="POST"):
    u = urllib.parse.urlsplit(url)
    host, port = _endpoint(url)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    https = u.scheme == "https"
    block = b"0" * 65536

    def worker(done, add, state, mark):
        fails = 0
        while not done.is_set():
            conn = None
            try:
                if https:
                    conn = http.client.HTTPSConnection(host, port, timeout=5, context=ctx)
                else:
                    conn = http.client.HTTPConnection(host, port, timeout=5)
                conn.putrequest(method, path)
                conn.putheader("User-Agent", _UA)
                conn.putheader("Content-Type", "application/octet-stream")
                conn.putheader("Content-Length", str(_UPLOAD_BODY))
                conn.endheaders()
                sent = 0
                answered = False
                while sent < _UPLOAD_BODY and not done.is_set():
                    conn.send(block)
                    sent += len(block)
                    # the server answering before the body is complete means it rejected us
                    if select.select([conn.sock], [], [], 0)[0]:
                        answered = True
                        break
                    add(len(block))
                if answered or not done.is_set():
                    resp = conn.getresponse()
                    resp.read()
                    if resp.status >= 400:
                        state["err"] = f"HTTP Error {resp.status}: {resp.reason}"
                        state["retry_after"] = _retry_after(resp.headers)
                        mark("dead")
                        return
                    mark("ok")
            except Exception as exc:
                text, retry = _rejection(conn)
                if text:
                    state["err"], state["retry_after"] = text, retry
                    mark("dead")
                    return
                state["err"] = exc
                fails += 1
                if fails >= _MAX_FAILS:
                    mark("dead")
                    return
                if done.wait(1.0):
                    return
            finally:
                if conn is not None:
                    conn.close()
    return worker


def _run_phase(phase: str, worker, on_sample, should_stop, duration: float,
               probe_info=None, loaded=None, notes=None):
    """Runs worker threads for `duration` seconds, reporting smoothed speed.
    Returns the average Mb/s (after warm-up), None if stopped."""
    lock = threading.Lock()
    probe_lock = threading.Lock()
    probe_samples = []
    done = threading.Event()
    state = {"bytes": 0, "err": None, "retry_after": None, "ok": 0, "dead": 0}

    def add(n):
        with lock:
            state["bytes"] += n

    def mark(key):
        with lock:
            state[key] += 1

    for _ in range(_THREADS):
        threading.Thread(target=worker, args=(done, add, state, mark), daemon=True).start()
    if probe_info is not None and loaded is not None:
        threading.Thread(target=_probe_loop, args=(probe_info, done, probe_samples, probe_lock),
                         daemon=True).start()

    t0 = time.perf_counter()
    snaps = [(0.0, 0)]
    base = None
    cut = None
    cut_note = ""
    last_move = (0.0, 0)
    try:
        while True:
            time.sleep(_SAMPLE_EVERY)
            now = time.perf_counter() - t0
            with lock:
                total = state["bytes"]
                dead, ok = state["dead"], state["ok"]
            snaps.append((now, total))
            if total > last_move[1]:
                last_move = (now, total)
            if base is None and now >= _WARMUP:
                base = (now, total)

            ref = next((s for s in snaps if now - s[0] <= _SMOOTH_WINDOW), snaps[0])
            dt = now - ref[0]
            if dt > 0:
                on_sample(phase, now, (total - ref[1]) * 8 / 1e6 / dt)

            if should_stop():
                return None
            if dead >= _THREADS and ok == 0:
                raise SpeedTestError(_error_text(state))
            if total == 0 and now >= 4.0:
                raise SpeedTestError(_error_text(state))
            if dead >= _THREADS:
                cut, cut_note = snaps[-2], _error_text(state)
                break
            if total > 0 and now - last_move[0] >= _STALL_CUT:
                cut, cut_note = last_move, f"no data for {_STALL_CUT:.0f} s"
                break
            if now >= duration:
                break
    finally:
        done.set()

    if total == 0:
        raise SpeedTestError(_error_text(state))
    end_t, end_total = cut if cut is not None else (now, total)
    if cut is not None:
        if end_t - (base or snaps[0])[0] < _PARTIAL_MIN:
            raise SpeedTestError(cut_note)
        if notes is not None:
            notes.append(cut_note)
    if base is None or end_t - base[0] < 0.2:
        base = snaps[0]
    if loaded is not None:
        ended = time.perf_counter()
        with probe_lock:
            recs = [(r[0], r[1]) for r in probe_samples]
        normal = []
        stalled = 0
        for started, result in recs:
            if result is None:
                stalled += ended - started >= _STALL_MS / 1000
            elif result < 0 or result >= _STALL_MS:
                stalled += 1
            else:
                normal.append(result)
        normal.sort()
        if cut is not None:
            loaded.append((None, 0, 0))
        else:
            median = normal[len(normal) // 2] if len(normal) >= _PROBE_MIN_SAMPLES else None
            loaded.append((median, stalled, len(normal) + stalled))
    return (end_total - base[1]) * 8 / 1e6 / (end_t - base[0])


def run_speedtest(on_ping, on_sample, on_result, should_stop, servers=None,
                  duration: float = DURATION, on_loaded=None):
    """Ping, then download, then upload against Cloudflare (or the given `servers`, tried in
    order - used by tests).
    on_ping(ms, jitter_ms_or_None); on_sample(phase, t_seconds, mbps) every ~0.25 s, phase is "down" or "up";
    on_result(phase, avg_mbps_or_None, error_text) when a phase ends.
    Returns (down, up) - up is None if the upload phase failed - or None if stopped.
    Raises SpeedTestError if the download failed. Error texts may end with
    "[retry-after=N]" when the server said how long to wait."""
    order = list(servers or [CLOUDFLARE])

    down = used = None
    loaded = []
    notes = []
    errors = []
    picks = {}
    for srv in order:
        if should_stop():
            return None
        try:
            host, port = _endpoint(srv["down"])
            picked = []
            ping, jitter = measure_ping(host, port, should_stop=should_stop, chosen_out=picked)
            if should_stop():
                return None
            on_ping(ping, jitter)
            if picked:
                picks[(host, port)] = picked[0]
            loaded = []
            notes = []
            probe = (picks.get((host, port)) or _probe_target(host, port)) if on_loaded else None
            down = _run_phase("down", _download_worker(srv["down"], _ssl_context(srv["down"])),
                              on_sample, should_stop, duration, probe_info=probe, loaded=loaded, notes=notes)
        except SpeedTestError as exc:
            errors.append(f"{srv['name']}: {exc}")
            continue
        if down is None:
            return None
        used = srv
        break
    if used is None:
        raise SpeedTestError("; ".join(errors[:2]) or "no server")
    on_result("down", down, notes[0] if notes else "")
    if on_loaded:
        on_loaded("down", *_loaded_args(loaded[0] if loaded else None))
    if notes and "429" in notes[0]:
        return down, None

    # upload: prefer the server that just served the download
    cands = [s for s in order if s.get("up")]
    if used in cands:
        cands.remove(used)
        cands.insert(0, used)
    up, up_err = None, "no upload server"
    for srv in cands:
        up_loaded = []
        up_notes = []
        try:
            up_host, up_port = _endpoint(srv["up"])
            probe = (picks.get((up_host, up_port)) or _probe_target(up_host, up_port)) if on_loaded else None
            up = _run_phase("up", _upload_worker(srv["up"], _ssl_context(srv["up"]),
                                                 srv.get("up_method", "POST")),
                            on_sample, should_stop, duration, probe_info=probe, loaded=up_loaded,
                            notes=up_notes)
        except SpeedTestError as exc:
            up, up_err = None, f"{srv['name']}: {exc}"
            continue
        if up is None:
            return None
        up_err = up_notes[0] if up_notes else ""
        break
    on_result("up", up, up_err)
    if on_loaded and up is not None:
        on_loaded("up", *_loaded_args(up_loaded[0] if up_loaded else None))
    return down, up


def _speed_tier(mbps, steps):
    """steps: [(minimum Mb/s, level), ...] from the best level down; below all of them -> 1."""
    for minimum, level in steps:
        if mbps >= minimum:
            return level
    return 1


def _ping_tier(ms, steps):
    """steps: [(maximum ms, level), ...] from the best level down; above all of them -> 1.
    No ping measured -> no limit from it."""
    if ms is None:
        return 5
    for maximum, level in steps:
        if ms <= maximum:
            return level
    return 1


def quality_levels(down, up, ping, jitter=None):
    """Rough 0-5 rating of how well the connection suits web browsing, games, watching streams
    (download) and live streaming / video calls (upload); 0 = no result yet. Indicative only:
    ping and jitter are TCP connect times to the test server, not to a game server, and packet
    loss is not measured."""
    live = _speed_tier(up, [(10, 5), (6, 4), (3, 3), (1.5, 2)]) if up is not None else 0
    if down is None:
        return {"web": 0, "games": 0, "stream": 0, "live": live}
    web = min(_speed_tier(down, [(25, 5), (10, 4), (5, 3), (2, 2)]),
              _ping_tier(ping, [(50, 5), (100, 4), (200, 3), (400, 2)]))
    games = min(_ping_tier(ping, [(20, 5), (40, 4), (70, 3), (120, 2)]),
                _ping_tier(jitter, [(5, 5), (10, 4), (20, 3), (40, 2)]),
                _speed_tier(down, [(5, 5), (3, 3), (1, 2)]))
    stream = _speed_tier(down, [(25, 5), (10, 4), (5, 3), (3, 2)])
    return {"web": web, "games": games, "stream": stream, "live": live}
