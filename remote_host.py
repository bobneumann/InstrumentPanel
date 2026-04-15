"""
RemoteHost — persistent SSH connection to one machine.

A background thread polls the remote once per interval by piping a
Python+psutil script to `python3 -` (Linux) or `python -` (Windows).
One SSH round-trip returns all metrics as JSON.  The Qt timer reads
only the in-memory cache — SSH latency is invisible to the UI.

Requirements on the remote host:
  Linux:   python3 + psutil  (pip3 install psutil)
  Windows: python  + psutil  + OpenSSH service enabled
           (Enable-WindowsOptionalFeature -Online -FeatureName OpenSSH-Server)
"""

import json
import threading
import time
import logging
from typing import Optional, Callable

log = logging.getLogger(__name__)

try:
    import paramiko
    _PARAMIKO_OK = True
except ImportError:
    _PARAMIKO_OK = False
    log.warning("paramiko not installed — remote hosts disabled.  "
                "Run: py -m pip install paramiko")


# ── Metric collection scripts ────────────────────────────────────────────────
# Piped to `python3 -` / `python -` on the remote — no quoting headaches.

_LINUX_SCRIPT = b"""
import psutil, json

# Measure ctx switches across the cpu sample window (200ms)
stats_a = psutil.cpu_stats()
cpu     = psutil.cpu_percent(interval=0.2)   # single blocking sample
stats_b = psutil.cpu_stats()
ctx_per_sec = (stats_b.ctx_switches - stats_a.ctx_switches) / 0.2

n    = psutil.net_io_counters()
mem  = psutil.virtual_memory()
try:
    load1 = float(open('/proc/loadavg').read().split()[0])
except Exception:
    load1 = 0.0

print(json.dumps({
    'cpu':      cpu,
    'ram':      mem.percent,
    'disk':     psutil.disk_usage('/').percent,
    'cores':    psutil.cpu_percent(percpu=True, interval=None),
    'net_sent': n.bytes_sent,
    'net_recv': n.bytes_recv,
    'ctx_rate': min(ctx_per_sec / 10.0, 100.0),   # 0-1000/s scaled to 0-100
    'load1':    min(load1 * 20.0,  100.0),          # 0-5 load  scaled to 0-100
}))
"""

_WINDOWS_SCRIPT = b"""
import psutil, json, time
n = psutil.net_io_counters()
psutil.cpu_percent(interval=0.2)
cores = psutil.cpu_percent(percpu=True, interval=None)
print(json.dumps({
    'cpu':      psutil.cpu_percent(interval=None),
    'ram':      psutil.virtual_memory().percent,
    'disk':     psutil.disk_usage('C:\\\\').percent,
    'cores':    cores,
    'net_sent': n.bytes_sent,
    'net_recv': n.bytes_recv,
}))
"""


# ── RemoteHost ───────────────────────────────────────────────────────────────

class RemoteHost:
    """
    One SSH connection + background polling thread.

    Usage:
        host = RemoteHost("web01", "192.168.1.10", user="admin",
                          key_path="C:/Users/Bob/.ssh/id_rsa")
        host.start()
        src = host.source("cpu")      # callable() -> float
        src = host.net_rate_source("recv")   # MB/s
    """

    def __init__(self, name: str, host: str, port: int = 22,
                 user: str = "root", key_path: str = None,
                 password: str = None, os: str = "linux",
                 poll_s: float = 2.0):
        self.name     = name
        self.status   = "disconnected"   # "connecting" | "connected" | "error"
        self.error    = ""

        self._host     = host
        self._port     = port
        self._user     = user
        self._key_path = key_path
        self._password = password
        self._os       = os.lower()
        self._poll_s   = poll_s

        self._ssh:   Optional["paramiko.SSHClient"] = None
        self._cache: dict  = {}
        self._lock   = threading.Lock()

        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── public API ────────────────────────────────────────────────────── #

    def start(self):
        if not _PARAMIKO_OK:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"rhost-{self.name}"
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close_ssh()

    def get(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            v = self._cache.get(key, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def source(self, key: str) -> Callable[[], float]:
        """Return a zero-arg callable that reads one metric from the cache."""
        return lambda: self.get(key)

    def net_rate_source(self, direction: str) -> Callable[[], float]:
        """
        Return a MB/s rate callable for 'sent' or 'recv'.
        Each call computes delta from the previous call, same pattern
        as local datasources.py net sources.
        """
        cache_key  = f"net_{direction}"
        prev_bytes = [None]
        prev_t     = [None]

        def _get() -> float:
            raw = self.get(cache_key, -1.0)
            if raw < 0:
                return 0.0
            now = time.monotonic()
            if prev_bytes[0] is None:
                prev_bytes[0] = raw
                prev_t[0]     = now
                return 0.0
            dt = now - prev_t[0]
            if dt < 0.01:
                return 0.0
            rate = (raw - prev_bytes[0]) / dt / 1_048_576
            prev_bytes[0] = raw
            prev_t[0]     = now
            return max(0.0, min(100.0, rate))

        return _get

    # ── internals ─────────────────────────────────────────────────────── #

    def _close_ssh(self):
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None

    def _connect(self) -> bool:
        self.status = "connecting"
        self._close_ssh()
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kw = dict(hostname=self._host, port=self._port,
                      username=self._user, timeout=10,
                      banner_timeout=10, auth_timeout=10)
            if self._key_path:
                kw["key_filename"] = self._key_path
            elif self._password:
                kw["password"] = self._password
            client.connect(**kw)
            self._ssh  = client
            self.status = "connected"
            self.error  = ""
            log.info("Connected to %s (%s)", self.name, self._host)
            return True
        except Exception as exc:
            self.status = "error"
            self.error  = str(exc)
            log.warning("Cannot connect to %s: %s", self.name, exc)
            return False

    def _run(self) -> Optional[dict]:
        """Execute metric script on remote, return parsed dict or None."""
        script = _WINDOWS_SCRIPT if self._os == "windows" else _LINUX_SCRIPT
        py     = "python" if self._os == "windows" else "python3"
        try:
            transport = self._ssh.get_transport()
            if transport is None or not transport.is_active():
                return None
            stdin, stdout, stderr = self._ssh.exec_command(f"{py} -", timeout=15)
            stdin.write(script)
            stdin.close()
            stdout.channel.settimeout(15)
            raw = stdout.read().decode(errors="replace").strip()
            if not raw:
                err = stderr.read().decode(errors="replace").strip()
                log.warning("%s: empty response; stderr: %s", self.name, err[:200])
                return None
            return json.loads(raw)
        except Exception as exc:
            log.warning("%s: poll error: %s", self.name, exc)
            self.error = str(exc)
            return None

    def _update(self, data: dict):
        with self._lock:
            self._cache["cpu"]      = float(data.get("cpu",      0))
            self._cache["ram"]      = float(data.get("ram",      0))
            self._cache["disk"]     = float(data.get("disk",     0))
            self._cache["ctx_rate"] = float(data.get("ctx_rate", 0))
            self._cache["load1"]    = float(data.get("load1",    0))
            for i, v in enumerate(data.get("cores", [])):
                self._cache[f"core_{i}"] = float(v)
            self._cache["net_sent"] = float(data.get("net_sent", -1))
            self._cache["net_recv"] = float(data.get("net_recv", -1))

    def _loop(self):
        backoff = 5.0
        while not self._stop.is_set():
            # (Re)connect if needed
            alive = (self._ssh is not None and
                     self._ssh.get_transport() is not None and
                     self._ssh.get_transport().is_active())
            if not alive:
                if not self._connect():
                    self._stop.wait(backoff)
                    backoff = min(backoff * 1.5, 60.0)
                    continue
                backoff = 5.0

            data = self._run()
            if data:
                self._update(data)
                self.status = "connected"
            else:
                self.status = "error"
                self._close_ssh()   # force reconnect next iteration

            self._stop.wait(self._poll_s)
