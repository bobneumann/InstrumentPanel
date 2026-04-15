"""
Data sources for Instrument Panel.
Each source is a callable() -> float, suitable for passing to GaugeSlot.source.

Usage:
    from datasources import cpu_core, cpu_total, ram_percent, disk_percent

    source = cpu_core(0)   # returns a callable
    value  = source()      # call it to get current reading
"""

import time
import psutil

# ── CPU per-core cache ───────────────────────────────────────────────────────
# psutil.cpu_percent(percpu=True) should be called once per poll cycle,
# not once per gauge. We cache with a short TTL so all cpu_core() callables
# share the same measurement.

_cpu_core_cache: list = []
_cpu_core_time: float = 0.0
_CPU_CACHE_TTL = 0.2   # seconds

def _refresh_cpu_cores() -> None:
    global _cpu_core_cache, _cpu_core_time
    _cpu_core_cache = psutil.cpu_percent(percpu=True, interval=None)
    _cpu_core_time  = time.monotonic()

# Prime the measurement — first call always returns 0s on Windows
psutil.cpu_percent(percpu=True, interval=None)
psutil.cpu_percent(interval=None)


# ── Public source factories ──────────────────────────────────────────────────

def cpu_core(i: int):
    """Per-core CPU utilization, 0–100 %."""
    def _get() -> float:
        if time.monotonic() - _cpu_core_time > _CPU_CACHE_TTL:
            _refresh_cpu_cores()
        return float(_cpu_core_cache[i]) if i < len(_cpu_core_cache) else 0.0
    return _get


def cpu_total():
    """Overall CPU utilization, 0–100 %."""
    return lambda: float(psutil.cpu_percent(interval=None))


def ram_percent():
    """Physical RAM used, 0–100 %."""
    return lambda: float(psutil.virtual_memory().percent)


def disk_percent(path: str = "C:\\"):
    """Disk used, 0–100 %."""
    def _get() -> float:
        try:
            return float(psutil.disk_usage(path).percent)
        except Exception:
            return 0.0
    return _get


def net_bytes_sent_rate(interval: float = 1.0):
    """Network bytes sent per second (MB/s), 0–100 capped."""
    _prev = [psutil.net_io_counters().bytes_sent]
    _prev_time = [time.monotonic()]
    def _get() -> float:
        now  = time.monotonic()
        curr = psutil.net_io_counters().bytes_sent
        dt   = now - _prev_time[0] or interval
        rate_mb = (curr - _prev[0]) / dt / 1_048_576
        _prev[0]      = curr
        _prev_time[0] = now
        return min(100.0, rate_mb)
    return _get


def net_bytes_recv_rate(interval: float = 1.0):
    """Network bytes received per second (MB/s), 0–100 capped."""
    _prev = [psutil.net_io_counters().bytes_recv]
    _prev_time = [time.monotonic()]
    def _get() -> float:
        now  = time.monotonic()
        curr = psutil.net_io_counters().bytes_recv
        dt   = now - _prev_time[0] or interval
        rate_mb = (curr - _prev[0]) / dt / 1_048_576
        _prev[0]      = curr
        _prev_time[0] = now
        return min(100.0, rate_mb)
    return _get
